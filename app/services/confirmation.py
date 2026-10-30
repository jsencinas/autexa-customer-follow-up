"""Employee review: saving corrections, confirming (which schedules the survey) and discarding."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus, InspectionStatus
from app.services import duplicates, opt_out
from app.services.inspections import InvalidTransition, get_inspection
from app.services.normalization import clean_name, normalize_phone, parse_date, service_display
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class ReviewForm(BaseModel):
    """Values submitted from the review form (all optional strings)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = ""
    customer_phone: str = ""
    service_description: str = ""
    service_display: str = ""
    service_date: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return "" if value is None else value


@dataclass
class ReviewResult:
    """Outcome of saving or confirming."""

    inspection: Inspection
    errors: dict[str, str] = field(default_factory=dict)
    duplicate_of: Inspection | None = None
    confirmed: bool = False
    opted_out: bool = False


def _require_pending(inspection: Inspection, now: datetime) -> None:
    if inspection.status != InspectionStatus.PENDING_CONFIRMATION:
        raise InvalidTransition("Esta inspección ya no está pendiente de confirmar.")
    if inspection.expires_at is not None and inspection.expires_at <= now:
        raise InvalidTransition("Esta inspección ha caducado. Vuelve a subir la foto.")


def validate(form: ReviewForm, default_region: str) -> tuple[dict[str, object], dict[str, str]]:
    """Normalise the submitted values; return (values, errors). Name and a valid phone are required."""
    errors: dict[str, str] = {}
    name = clean_name(form.customer_name)
    if not name:
        errors["customer_name"] = "El nombre es obligatorio."
    phone = normalize_phone(form.customer_phone, default_region)
    if not form.customer_phone:
        errors["customer_phone"] = "El teléfono es obligatorio."
    elif phone is None:
        errors["customer_phone"] = "El teléfono no es válido."
    service_date = parse_date(form.service_date) if form.service_date else None
    if form.service_date and service_date is None:
        errors["service_date"] = "Fecha no válida (usa dd/mm/aaaa)."
    description = " ".join(form.service_description.split()) or None
    values: dict[str, object] = {
        "customer_name": name,
        "customer_phone": phone,
        "phone_raw": form.customer_phone or None,
        "service_description": form.service_description or None,
        "service_display": service_display(form.service_display or description),
        "service_date": service_date,
    }
    return values, errors


NOT_EDITABLE = "La encuesta ya se envió o se está enviando; esta inspección ya no se puede modificar."


def save_edits(
    db: Session, inspection_id: int, form: ReviewForm, settings: Settings, now: datetime | None = None
) -> ReviewResult:
    """Store the employee's corrections and re-check for duplicates.

    Pending inspections keep partial data; scheduled ones only accept fully valid changes.
    """
    now = now or utcnow()
    inspection = get_inspection(db, inspection_id)
    if inspection.status == InspectionStatus.SCHEDULED:
        return _save_scheduled(db, inspection, form, settings, now)
    _require_pending(inspection, now)
    values, errors = validate(form, settings.default_country_code)
    for key, value in values.items():
        if key == "service_date" and value is None:
            value = inspection.service_date or (inspection.image_uploaded_at or inspection.created_at).date()
        setattr(inspection, key, value)
    inspection.edited_at = now
    if inspection.extraction_status in (ExtractionStatus.PENDING, ExtractionStatus.PROCESSING):
        # The employee typed the data by hand; stop waiting for the model.
        inspection.extraction_status = ExtractionStatus.FAILED
        inspection.extraction_error = "replaced by manual entry"
    db.commit()

    existing = duplicates.register(db, inspection)
    if existing is not None:
        duplicates.discard_as_duplicate(db, inspection, existing)
        return ReviewResult(inspection, errors, duplicate_of=existing)
    return ReviewResult(inspection, errors)


def _save_scheduled(
    db: Session, inspection: Inspection, form: ReviewForm, settings: Settings, now: datetime
) -> ReviewResult:
    """Edit a confirmed inspection whose survey has not been sent yet.

    The write is conditional (still scheduled and not claimed by the worker), so a survey being
    sent at that moment is never sent with half-changed data.
    """
    values, errors = validate(form, settings.default_country_code)
    if errors:
        return ReviewResult(inspection, errors)
    if values["service_date"] is None:
        values["service_date"] = inspection.service_date
    key = duplicates.key_for(
        values["customer_name"],  # type: ignore[arg-type]
        values["customer_phone"],  # type: ignore[arg-type]
        values["service_description"],  # type: ignore[arg-type]
        values["service_date"],  # type: ignore[arg-type]
    )
    existing = duplicates.find_active_duplicate(db, key, inspection.id) if key else None
    if existing is not None:
        message = f"Con estos datos coincidiría con la inspección #{existing.id}, que ya está registrada."
        return ReviewResult(inspection, {"form": message})
    opted_out = opt_out.is_opted_out(db, values["customer_phone"])  # type: ignore[arg-type]
    changes = {**values, "edited_at": now, "dedup_key": None if opted_out else key}
    if opted_out:
        changes["status"] = InspectionStatus.OPTED_OUT
    try:
        updated = db.execute(
            update(Inspection)
            .where(
                Inspection.id == inspection.id,
                Inspection.status == InspectionStatus.SCHEDULED,
                Inspection.send_claimed_at.is_(None),
            )
            .values(**changes)
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return ReviewResult(inspection, {"form": "Otra inspección con estos mismos datos se acaba de registrar."})
    if updated.rowcount != 1:  # type: ignore[attr-defined]
        raise InvalidTransition(NOT_EDITABLE)
    db.refresh(inspection)
    logger.info("scheduled inspection %s edited", inspection.id)
    return ReviewResult(inspection, opted_out=opted_out)


def confirm(
    db: Session,
    inspection_id: int,
    form: ReviewForm,
    settings: Settings,
    user_id: int | None,
    now: datetime | None = None,
) -> ReviewResult:
    """Save the corrections and, if valid, confirm and schedule the survey.

    Opted-out customers are stored as `opted_out` and never scheduled.
    """
    now = now or utcnow()
    # Only pending inspections can be confirmed (a double click must not reschedule a survey).
    _require_pending(get_inspection(db, inspection_id), now)
    result = save_edits(db, inspection_id, form, settings, now)
    if result.errors or result.duplicate_of is not None:
        return result
    inspection = result.inspection
    inspection.confirmed_at = now
    inspection.confirmed_by_id = user_id
    if opt_out.is_opted_out(db, inspection.customer_phone):
        inspection.status = InspectionStatus.OPTED_OUT
        duplicates.release(inspection)
        result.opted_out = True
    else:
        inspection.status = InspectionStatus.SCHEDULED
        inspection.scheduled_at = now + timedelta(hours=settings.survey_delay_hours)
    db.commit()
    result.confirmed = True
    logger.info("inspection %s confirmed (%s)", inspection.id, inspection.status)
    return result


def discard(db: Session, inspection_id: int, now: datetime | None = None) -> Inspection:
    """Discard a pending inspection, or cancel a survey not sent yet; it no longer blocks a resubmission."""
    inspection = get_inspection(db, inspection_id)
    if inspection.status == InspectionStatus.SCHEDULED:
        cancelled = db.execute(
            update(Inspection)
            .where(
                Inspection.id == inspection.id,
                Inspection.status == InspectionStatus.SCHEDULED,
                Inspection.send_claimed_at.is_(None),
            )
            .values(status=InspectionStatus.DISCARDED, dedup_key=None)
        )
        db.commit()
        if cancelled.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidTransition(NOT_EDITABLE)
        db.refresh(inspection)
        logger.info("survey for inspection %s cancelled", inspection.id)
        return inspection
    if inspection.status != InspectionStatus.PENDING_CONFIRMATION:
        raise InvalidTransition("Solo se pueden descartar inspecciones pendientes o con la encuesta sin enviar.")
    inspection.status = InspectionStatus.DISCARDED
    duplicates.release(inspection)
    db.commit()
    return inspection
