from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db_models import Inspection, MonthlyReport, OptOut, User
from app.models.enums import ExtractionStatus, InspectionStatus


def test_inspection_defaults_and_aware_datetimes(db: Session) -> None:
    inspection = Inspection()
    db.add(inspection)
    db.commit()
    db.expire_all()
    loaded = db.get(Inspection, inspection.id)
    assert loaded is not None
    assert loaded.status == InspectionStatus.PENDING_CONFIRMATION
    assert loaded.extraction_status == ExtractionStatus.PENDING
    assert loaded.created_at.tzinfo is UTC


def test_dedup_key_is_unique_but_nullable(db: Session) -> None:
    db.add_all([Inspection(), Inspection(), Inspection(dedup_key="abc")])
    db.commit()
    db.add(Inspection(dedup_key="abc"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_opt_out_phone_is_unique(db: Session) -> None:
    db.add(OptOut(phone="+34600000000", source="admin"))
    db.commit()
    db.add(OptOut(phone="+34600000000", source="button"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_monthly_report_period_is_unique(db: Session) -> None:
    db.add(MonthlyReport(year=2026, month=9))
    db.commit()
    db.add(MonthlyReport(year=2026, month=9))
    with pytest.raises(IntegrityError):
        db.commit()


def test_username_is_unique(db: Session) -> None:
    db.add(User(username="ana", password_hash="x", created_at=datetime.now(UTC)))
    db.commit()
    db.add(User(username="ana", password_hash="y"))
    with pytest.raises(IntegrityError):
        db.commit()
