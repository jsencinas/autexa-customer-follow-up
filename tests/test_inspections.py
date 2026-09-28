import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.domain import Inspection, RecordStatus
from app.models.schemas import ExtractedData
from app.services.inspections import check_and_create_inspection


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestCheckAndCreateInspection:
    """Tests for duplicate detection and record creation."""

    def test_creates_new_record(self, db_session):
        """A new unique inspection is created successfully."""
        data = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza general",
            date="2026-09-28"
        )
        is_dup, record = check_and_create_inspection(db_session, data, "+528001234567")
        
        assert is_dup is False
        assert record.id is not None
        assert record.status == RecordStatus.pending_confirmation
        assert record.employee_phone == "+528001234567"

    def test_detects_exact_duplicate(self, db_session):
        """An identical inspection is flagged as duplicate."""
        data = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza general",
            date="2026-09-28"
        )
        check_and_create_inspection(db_session, data, "+528001234567")
        
        # Same data again
        is_dup, record = check_and_create_inspection(db_session, data, "+528001234567")
        assert is_dup is True

    def test_case_insensitive_name_duplicate(self, db_session):
        """Name comparison is case-insensitive."""
        data1 = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza",
            date="2026-09-28"
        )
        data2 = ExtractedData(
            customer_name="JUAN PÉREZ",
            customer_phone="6141234567",
            service_description="Limpieza",
            date="2026-09-28"
        )
        check_and_create_inspection(db_session, data1, "+528001234567")
        is_dup, _ = check_and_create_inspection(db_session, data2, "+528001234567")
        assert is_dup is True

    def test_different_service_is_not_duplicate(self, db_session):
        """Different service description means it's a new inspection."""
        data1 = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza general",
            date="2026-09-28"
        )
        data2 = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Fumigación",
            date="2026-09-28"
        )
        check_and_create_inspection(db_session, data1, "+528001234567")
        is_dup, _ = check_and_create_inspection(db_session, data2, "+528001234567")
        assert is_dup is False

    def test_expired_record_does_not_block(self, db_session):
        """An expired record should NOT block a resubmission."""
        data = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza",
            date="2026-09-28"
        )
        _, record = check_and_create_inspection(db_session, data, "+528001234567")
        record.status = RecordStatus.expired
        db_session.commit()
        
        # Same data again — should NOT be flagged as duplicate
        is_dup, new_record = check_and_create_inspection(db_session, data, "+528001234567")
        assert is_dup is False
        assert new_record.id != record.id

    def test_failed_record_does_not_block(self, db_session):
        """A failed record should NOT block a resubmission."""
        data = ExtractedData(
            customer_name="Juan Pérez",
            customer_phone="6141234567",
            service_description="Limpieza",
            date="2026-09-28"
        )
        _, record = check_and_create_inspection(db_session, data, "+528001234567")
        record.status = RecordStatus.failed
        db_session.commit()
        
        is_dup, _ = check_and_create_inspection(db_session, data, "+528001234567")
        assert is_dup is False
