"""Reading inspection photos with the extraction model, including unreadable photos and failures."""

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.ollama import OllamaError
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus, InspectionStatus
from app.models.schemas import ExtractedField, ExtractionResult
from app.services import duplicates
from app.services.normalization import clean_name, normalize_phone, parse_date, service_display
from app.services.image_prep import prepare_for_model
from app.services.retry import backoff
from app.services.storage import ImageStorage
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

STALE_CLAIM = timedelta(minutes=10)
KEY_FIELDS = ("customer_name", "customer_phone", "service_description", "date")
# A photo counts as unreadable when at least this many key fields could not be read.
UNREADABLE_MISSING_FIELDS = 3


class Extractor(Protocol):
    """Anything that can read a form photo (the real client or a test fake)."""

    def extract(self, image: bytes) -> tuple[ExtractionResult, dict[str, Any]]:
        """Return the validated result and the raw output."""
        ...


def claim_due_extractions(db: Session, now: datetime | None = None, limit: int = 5) -> list[int]:
    """Atomically claim inspections whose extraction is due (or whose claim went stale)."""
    now = now or utcnow()
    due = and_(Inspection.extraction_status == ExtractionStatus.PENDING, Inspection.extraction_next_attempt_at <= now)
    stale = and_(
        Inspection.extraction_status == ExtractionStatus.PROCESSING,
        Inspection.extraction_claimed_at < now - STALE_CLAIM,
    )
    candidates = list(db.scalars(select(Inspection.id).where(or_(due, stale)).order_by(Inspection.id).limit(limit)))
    claimed: list[int] = []
    for inspection_id in candidates:
        result = db.execute(
            update(Inspection)
            .where(Inspection.id == inspection_id, or_(due, stale))
            .values(extraction_status=ExtractionStatus.PROCESSING, extraction_claimed_at=now)
        )
        if result.rowcount == 1:  # type: ignore[attr-defined]
            claimed.append(inspection_id)
    db.commit()
    return claimed


def is_unreadable(result: ExtractionResult) -> bool:
    """True if most key fields are missing.

    The model's own "is this a form?" answer is not used: in tests on real photos it said "no"
    for forms it had just read correctly.
    """
    missing = sum(1 for name in KEY_FIELDS if getattr(result, name).value is None)
    return missing >= UNREADABLE_MISSING_FIELDS


def vehicle_display(other_fields: dict[str, ExtractedField]) -> str | None:
    """Customer-facing fallback when no service was written: "tu Chevrolet Aveo"."""
    parts = [other_fields[k].value for k in ("vehicle_brand", "vehicle_model") if k in other_fields]
    words = " ".join(p for p in parts if p)
    return service_display(f"tu {words}") if words else None


def apply_extraction(inspection: Inspection, result: ExtractionResult, default_region: str) -> None:
    """Copy normalised values onto the inspection and record per-field confidence."""
    confidence: dict[str, str] = {}

    def conf(name: str, value: object) -> None:
        field = getattr(result, name)
        confidence[name] = "missing" if value is None else field.confidence

    inspection.customer_name = clean_name(result.customer_name.value)
    conf("customer_name", inspection.customer_name)

    inspection.phone_raw = result.customer_phone.value
    inspection.customer_phone = normalize_phone(result.customer_phone.value, default_region)
    conf("customer_phone", inspection.customer_phone)
    if inspection.phone_raw and inspection.customer_phone is None:
        confidence["customer_phone"] = "invalid"

    inspection.service_description = result.service_description.value
    inspection.service_display = service_display(result.service_description.value) or vehicle_display(
        result.other_fields
    )
    conf("service_description", inspection.service_description)

    parsed = parse_date(result.date.value)
    if parsed is None and result.date.value:
        confidence["date"] = "invalid"
    else:
        conf("date", parsed)
    upload_day = (inspection.image_uploaded_at or inspection.created_at).date()
    inspection.service_date = parsed or upload_day

    inspection.extra_fields = {k: v.value for k, v in result.other_fields.items() if v.value is not None} or None
    inspection.field_confidence = confidence


def process_extraction(
    db: Session,
    inspection_id: int,
    extractor: Extractor,
    storage: ImageStorage,
    settings: Settings,
    now: datetime | None = None,
) -> Inspection:
    """Run extraction for one claimed inspection; retry transient errors and fall back to manual entry."""
    now = now or utcnow()
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise LookupError(inspection_id)
    inspection.extraction_claimed_at = None

    if inspection.status != InspectionStatus.PENDING_CONFIRMATION or not inspection.image_path:
        inspection.extraction_status = ExtractionStatus.FAILED
        inspection.extraction_error = "no photo or inspection no longer pending"
        db.commit()
        return inspection

    inspection.extraction_attempts += 1
    try:
        image = prepare_for_model(storage.read(inspection.image_path), settings.extraction_max_image_side)
        result, raw = extractor.extract(image)
    except (OllamaError, OSError) as exc:
        inspection.extraction_error = str(exc)[:500]
        if inspection.extraction_attempts >= settings.max_extraction_attempts:
            inspection.extraction_status = ExtractionStatus.FAILED
            logger.warning("inspection %s extraction failed permanently: %s", inspection.id, type(exc).__name__)
        else:
            inspection.extraction_status = ExtractionStatus.PENDING
            inspection.extraction_next_attempt_at = now + backoff(
                inspection.extraction_attempts, settings.extraction_retry_base_seconds
            )
            logger.info("inspection %s extraction retry scheduled", inspection.id)
        db.commit()
        return inspection

    inspection.raw_extraction = raw
    inspection.extraction_error = None
    if inspection.edited_at is None:
        # Never overwrite data the employee has already typed by hand.
        apply_extraction(inspection, result, settings.default_country_code)
    inspection.extraction_status = ExtractionStatus.UNREADABLE if is_unreadable(result) else ExtractionStatus.DONE
    db.commit()
    logger.info("inspection %s extraction finished: %s", inspection.id, inspection.extraction_status)

    existing = duplicates.register(db, inspection)
    if existing is not None:
        duplicates.discard_as_duplicate(db, inspection, existing)
        storage.delete(inspection.image_path)
        inspection.image_path = None
        inspection.image_deleted_at = now
        db.commit()
    return inspection
