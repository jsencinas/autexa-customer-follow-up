"""Duplicate inspection detection based on normalised extracted data.

The check is enforced by the database: an active, complete inspection holds a unique `dedup_key`,
so two near-simultaneous submissions of the same form cannot both keep it.
"""

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db_models import Inspection
from app.models.enums import ACTIVE_STATUSES, InspectionStatus
from app.services.normalization import name_key, service_key

logger = logging.getLogger(__name__)


def compute_dedup_key(inspection: Inspection) -> str | None:
    """Hash of name, phone, service and date; None unless all four are present."""
    if not (
        inspection.customer_name
        and inspection.customer_phone
        and inspection.service_description
        and inspection.service_date
    ):
        return None
    parts = (
        name_key(inspection.customer_name),
        inspection.customer_phone,
        service_key(inspection.service_description),
        inspection.service_date.isoformat(),
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def find_active_duplicate(db: Session, key: str, exclude_id: int) -> Inspection | None:
    """The active inspection holding this key, other than `exclude_id`."""
    return db.scalar(
        select(Inspection).where(
            Inspection.dedup_key == key,
            Inspection.id != exclude_id,
            Inspection.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
    )


def register(db: Session, inspection: Inspection) -> Inspection | None:
    """Give the inspection its dedup key. Returns the existing inspection if this one is a duplicate.

    Commits on success. The caller decides what to do with a duplicate (see `discard_as_duplicate`).
    """
    key = compute_dedup_key(inspection)
    if key == inspection.dedup_key:
        return None
    existing = find_active_duplicate(db, key, inspection.id) if key else None
    if existing is not None:
        return existing
    inspection.dedup_key = key
    try:
        db.commit()
    except IntegrityError:
        # Another submission claimed the key between our check and our write.
        db.rollback()
        inspection = db.get(Inspection, inspection.id)  # type: ignore[assignment]
        return find_active_duplicate(db, key, inspection.id) if key else None
    return None


def release(inspection: Inspection) -> None:
    """Drop the dedup key so an inactive record no longer blocks resubmission (caller commits)."""
    inspection.dedup_key = None


def discard_as_duplicate(db: Session, inspection: Inspection, existing: Inspection) -> None:
    """Mark the new inspection as a discarded duplicate of `existing` (no survey will be scheduled)."""
    inspection.status = InspectionStatus.DISCARDED
    inspection.duplicate_of_id = existing.id
    release(inspection)
    db.commit()
    logger.info("inspection %s is a duplicate of %s", inspection.id, existing.id)
