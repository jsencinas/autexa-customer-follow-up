"""Duplicate inspection detection based on normalised extracted data.

The check is enforced by the database: an active, complete inspection holds a unique `dedup_key`,
so two near-simultaneous submissions of the same form cannot both keep it.
"""

import hashlib
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db_models import Inspection
from app.models.enums import ACTIVE_STATUSES, InspectionStatus
from app.services.normalization import name_key, service_key

logger = logging.getLogger(__name__)


def key_for(name: str | None, phone: str | None, service: str | None, service_date: date | None) -> str | None:
    """Hash of name, phone, service and date; None unless name, phone and date are present.

    An empty service is a value like any other: the service box is often left blank on the
    paper form, and two uploads of the same blank form must still be caught.
    """
    if not (name and phone and service_date):
        return None
    parts = (name_key(name), phone, service_key(service or ""), service_date.isoformat())
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def compute_dedup_key(inspection: Inspection) -> str | None:
    """The dedup key of an inspection's current data."""
    return key_for(
        inspection.customer_name, inspection.customer_phone, inspection.service_description, inspection.service_date
    )


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


def backfill_keys(db: Session) -> list[tuple[int, int]]:
    """Give a key to active inspections that lack one (e.g. after the key rules changed).

    Conflicts are not resolved automatically, because those inspections may already be confirmed:
    returns (inspection id, existing duplicate id) pairs for a person to review.
    """
    conflicts: list[tuple[int, int]] = []
    rows = db.scalars(
        select(Inspection)
        .where(Inspection.dedup_key.is_(None), Inspection.status.in_([s.value for s in ACTIVE_STATUSES]))
        .order_by(Inspection.id)
    )
    for inspection in list(rows):
        existing = register(db, inspection)
        if existing is not None:
            conflicts.append((inspection.id, existing.id))
            logger.warning("inspection %s duplicates %s; review it in the status window", inspection.id, existing.id)
    return conflicts
