import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.domain import Inspection, RecordStatus, OptedOutPhone
from app.services.survey import send_survey_to_customer, handle_customer_reply


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_inspection(db_session):
    """Create a sample scheduled inspection."""
    record = Inspection(
        employee_phone="+528001234567",
        customer_name="María López",
        customer_phone="+526149876543",
        service_description="Fumigación",
        date="2026-09-28",
        status=RecordStatus.scheduled,
        consent_confirmed=True,
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


class TestSendSurveyToCustomer:
    """Tests for the survey sending logic."""

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_sends_template_successfully(self, MockWA, db_session, sample_inspection):
        """Survey is sent and status updated to 'sent'."""
        mock_wa = MockWA.return_value
        mock_wa.send_template_message = AsyncMock(return_value={"messages": [{"id": "123"}]})
        
        await send_survey_to_customer(sample_inspection, db_session)
        
        mock_wa.send_template_message.assert_called_once()
        assert sample_inspection.status == RecordStatus.sent

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_skips_opted_out_customer(self, MockWA, db_session, sample_inspection):
        """Survey is not sent if the customer has opted out."""
        db_session.add(OptedOutPhone(phone="+526149876543"))
        db_session.commit()
        
        await send_survey_to_customer(sample_inspection, db_session)
        
        MockWA.return_value.send_template_message.assert_not_called()
        assert sample_inspection.status == RecordStatus.failed

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_skips_without_consent(self, MockWA, db_session):
        """Survey is not sent if consent is not confirmed."""
        record = Inspection(
            customer_name="Test",
            customer_phone="+526141111111",
            service_description="Test",
            date="2026-09-28",
            status=RecordStatus.scheduled,
            consent_confirmed=False,
        )
        db_session.add(record)
        db_session.commit()
        
        await send_survey_to_customer(record, db_session)
        
        MockWA.return_value.send_template_message.assert_not_called()
        assert record.status == RecordStatus.failed


class TestHandleCustomerReply:
    """Tests for customer reply handling."""

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_records_rating(self, MockWA, db_session):
        """A rating keyword is stored correctly."""
        mock_wa = MockWA.return_value
        mock_wa.send_text_message = AsyncMock()
        
        record = Inspection(
            customer_phone="+526149876543",
            customer_name="Test",
            service_description="Test",
            date="2026-09-28",
            status=RecordStatus.sent,
        )
        db_session.add(record)
        db_session.commit()
        
        await handle_customer_reply("+526149876543", "Bueno", db_session)
        
        assert record.survey_rating == "bueno"
        mock_wa.send_text_message.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_records_feedback_and_completes(self, MockWA, db_session):
        """After rating, feedback completes the survey."""
        mock_wa = MockWA.return_value
        mock_wa.send_text_message = AsyncMock()
        
        record = Inspection(
            customer_phone="+526149876543",
            customer_name="Test",
            service_description="Test",
            date="2026-09-28",
            status=RecordStatus.sent,
            survey_rating="bueno",
        )
        db_session.add(record)
        db_session.commit()
        
        await handle_customer_reply("+526149876543", "Todo perfecto, gracias", db_session)
        
        assert record.survey_feedback == "Todo perfecto, gracias"
        assert record.status == RecordStatus.answered

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_opt_out_registers_and_confirms(self, MockWA, db_session):
        """'STOP' keyword registers the opt-out and confirms."""
        mock_wa = MockWA.return_value
        mock_wa.send_text_message = AsyncMock()
        
        await handle_customer_reply("+526149876543", "stop", db_session)
        
        opted = db_session.query(OptedOutPhone).filter_by(phone="+526149876543").first()
        assert opted is not None
        mock_wa.send_text_message.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.survey.WhatsAppClient")
    async def test_ignores_reply_with_no_active_survey(self, MockWA, db_session):
        """A reply from an unknown customer is silently ignored."""
        mock_wa = MockWA.return_value
        mock_wa.send_text_message = AsyncMock()
        
        await handle_customer_reply("+526140000000", "Bueno", db_session)
        
        mock_wa.send_text_message.assert_not_called()
