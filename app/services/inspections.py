"""Creating and listing inspections from uploaded photos."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus, InspectionStatus
from app.services.storage import ImageStorage
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class InspectionNotFound(LookupError):
    """No inspection with that id."""


class InvalidTransition(ValueError):
    """The requested action is not allowed in the inspection's current status."""


def storage_for(settings: Settings) -> ImageStorage:
    """The image storage configured in settings."""
    return ImageStorage(settings.storage_dir, settings.max_upload_mb * 1024 * 1024)


def create_from_upload(
    db: Session, settings: Settings, data: bytes, user_id: int | None, now: datetime | None = None
) -> Inspection:
    """Store the photo and create a pending inspection queued for extraction."""
    now = now or utcnow()
    name = storage_for(settings).save(data)
    inspection = Inspection(
        status=InspectionStatus.PENDING_CONFIRMATION,
        uploaded_by_id=user_id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.confirmation_expiry_hours),
        image_path=name,
        image_uploaded_at=now,
        extraction_status=ExtractionStatus.PENDING,
        extraction_next_attempt_at=now,
    )
    db.add(inspection)
    db.commit()
    logger.info("inspection %s created from upload", inspection.id)
    return inspection


def get_inspection(db: Session, inspection_id: int) -> Inspection:
    """Load an inspection or raise InspectionNotFound."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise InspectionNotFound(inspection_id)
    return inspection


def replace_image(
    db: Session, settings: Settings, inspection_id: int, data: bytes, now: datetime | None = None
) -> Inspection:
    """Replace the photo of a pending inspection ("retake") and queue extraction again."""
    now = now or utcnow()
    inspection = get_inspection(db, inspection_id)
    if inspection.status != InspectionStatus.PENDING_CONFIRMATION:
        raise InvalidTransition("Solo se puede repetir la foto de una inspección pendiente.")
    storage = storage_for(settings)
    new_name = storage.save(data)
    if inspection.image_path:
        storage.delete(inspection.image_path)
    inspection.image_path = new_name
    inspection.image_uploaded_at = now
    inspection.image_deleted_at = None
    inspection.extraction_status = ExtractionStatus.PENDING
    inspection.extraction_attempts = 0
    inspection.extraction_next_attempt_at = now
    inspection.extraction_claimed_at = None
    inspection.extraction_error = None
    db.commit()
    return inspection


def list_by_status(db: Session, status: InspectionStatus | None, limit: int = 200) -> list[Inspection]:
    """Most recent inspections, optionally filtered by status."""
    query = select(Inspection).order_by(Inspection.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Inspection.status == status)
    return list(db.scalars(query))


def progress(inspection: Inspection, worker_alive: bool) -> dict[str, object]:
    """What the review page shows while waiting; `signature` changes whenever that display should change."""
    data: dict[str, object] = {
        "status": inspection.status,
        "extraction_status": inspection.extraction_status,
        "attempts": inspection.extraction_attempts,
        "worker_alive": worker_alive,
    }
    data["signature"] = "|".join(str(v) for v in data.values())
    return data
