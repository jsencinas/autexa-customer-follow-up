from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.db_models import Inspection
from app.models.enums import InspectionStatus
from app.services import duplicates, extraction, inspections
from tests.test_extraction import JPEG, FakeExtractor, good_result


def make(db: Session, **fields) -> Inspection:  # type: ignore[no-untyped-def]
    values = {
        "customer_name": "María García",
        "customer_phone": "+34612345678",
        "service_description": "Cambio de aceite",
        "service_date": date(2026, 10, 3),
    }
    values.update(fields)
    inspection = Inspection(**values)
    db.add(inspection)
    db.commit()
    return inspection


def test_key_ignores_case_and_spacing(db: Session) -> None:
    a = make(db)
    b = make(db, customer_name="  maría   GARCÍA ", service_description="cambio  de ACEITE")
    assert duplicates.compute_dedup_key(a) == duplicates.compute_dedup_key(b)


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer_name", "María López"),
        ("customer_phone", "+34611111111"),
        ("service_description", "Cambio de ruedas"),
        ("service_date", date(2026, 10, 4)),
    ],
)
def test_any_difference_is_a_new_inspection(db: Session, field: str, value: object) -> None:
    first = make(db)
    assert duplicates.register(db, first) is None
    second = make(db, **{field: value})
    assert duplicates.register(db, second) is None


def test_incomplete_records_get_no_key(db: Session) -> None:
    inspection = make(db, customer_phone=None)
    assert duplicates.compute_dedup_key(inspection) is None
    assert duplicates.register(db, inspection) is None and inspection.dedup_key is None


@pytest.mark.parametrize(
    "status",
    [InspectionStatus.PENDING_CONFIRMATION, InspectionStatus.SCHEDULED, InspectionStatus.SENT, InspectionStatus.ANSWERED],
)
def test_active_statuses_block(db: Session, status: InspectionStatus) -> None:
    first = make(db, status=status)
    duplicates.register(db, first)
    assert duplicates.register(db, make(db)) is not None


@pytest.mark.parametrize(
    "status",
    [InspectionStatus.EXPIRED, InspectionStatus.FAILED, InspectionStatus.DISCARDED, InspectionStatus.OPTED_OUT],
)
def test_inactive_statuses_do_not_block(db: Session, status: InspectionStatus) -> None:
    first = make(db)
    duplicates.register(db, first)
    first.status = status
    duplicates.release(first)
    db.commit()
    assert duplicates.register(db, make(db)) is None


def test_database_rejects_race(session_factory: sessionmaker[Session]) -> None:
    """Two sessions pass the read check at the same time; only one may keep the key."""
    s1, s2 = session_factory(), session_factory()
    a, b = make(s1), make(s2)
    key = duplicates.compute_dedup_key(a)
    assert key and duplicates.find_active_duplicate(s1, key, a.id) is None
    assert duplicates.find_active_duplicate(s2, key, b.id) is None
    a.dedup_key = key
    s1.commit()
    # Session 2 already "checked" before session 1 wrote: register's write must fail and report the duplicate.
    b2 = s2.get(Inspection, b.id)
    assert b2 is not None
    b2.dedup_key = key
    with pytest.raises(IntegrityError):
        s2.commit()
    s2.rollback()
    assert duplicates.register(s2, s2.get(Inspection, b.id)).id == a.id  # type: ignore[arg-type, union-attr]
    s1.close()
    s2.close()


def test_duplicate_after_extraction_is_discarded(db: Session, settings: Settings) -> None:
    storage = inspections.storage_for(settings)
    first = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    second = inspections.create_from_upload(db, settings, JPEG + b"different photo", user_id=None)
    fake = FakeExtractor([good_result(), good_result()])
    claimed = extraction.claim_due_extractions(db)
    for inspection_id in claimed:
        extraction.process_extraction(db, inspection_id, fake, storage, settings)
    db.refresh(first)
    db.refresh(second)
    assert first.status == InspectionStatus.PENDING_CONFIRMATION and first.dedup_key
    assert second.status == InspectionStatus.DISCARDED
    assert second.duplicate_of_id == first.id and second.dedup_key is None
    assert second.image_path is None


def test_blank_service_on_both_is_still_a_duplicate(db: Session) -> None:
    first = make(db, service_description=None)
    assert duplicates.register(db, first) is None and first.dedup_key
    second = make(db, service_description="  ")
    assert duplicates.register(db, second).id == first.id  # type: ignore[union-attr]


def test_blank_service_differs_from_written_service(db: Session) -> None:
    duplicates.register(db, make(db, service_description=None))
    assert duplicates.register(db, make(db)) is None


def test_repeated_upload_of_form_without_service_is_discarded(db: Session, settings: Settings) -> None:
    blank = good_result(service_description={"value": None})
    first = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    second = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    fake = FakeExtractor([blank, blank])
    for inspection_id in extraction.claim_due_extractions(db):
        extraction.process_extraction(db, inspection_id, fake, inspections.storage_for(settings), settings)
    db.refresh(second)
    assert second.status == InspectionStatus.DISCARDED and second.duplicate_of_id == first.id


def test_backfill_keys_reports_conflicts_without_discarding(db: Session) -> None:
    first = make(db, status=InspectionStatus.SCHEDULED, service_description=None)
    second = make(db, status=InspectionStatus.SCHEDULED, service_description=None)
    other = make(db, status=InspectionStatus.SCHEDULED, customer_name="Otra Persona")
    assert duplicates.backfill_keys(db) == [(second.id, first.id)]
    db.refresh(second)
    assert first.dedup_key and other.dedup_key and second.dedup_key is None
    assert second.status == InspectionStatus.SCHEDULED  # left for a person to decide
