from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.whatsapp import WhatsAppError
from app.models.db_models import EmployeeMessage, Inspection
from app.models.enums import InspectionStatus, Rating, SurveyStage
from app.services import survey_flow
from app.services.messages import Messages, load_messages
from tests.fakes import FakeSender, scheduled_inspection

SENT = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


@pytest.fixture
def messages(settings: Settings) -> Messages:
    return load_messages(settings.messages_file)


@pytest.fixture
def sent(db: Session) -> Inspection:
    return scheduled_inspection(
        db, SENT, status=InspectionStatus.SENT, sent_at=SENT, survey_stage=SurveyStage.AWAITING_RATING
    )


def test_rating_then_question_then_comment_then_thanks(
    db: Session, sent: Inspection, messages: Messages, settings: Settings
) -> None:
    sender = FakeSender()
    now = SENT + timedelta(minutes=5)
    survey_flow.record_rating(db, sent, Rating.GOOD, "rule", sender, messages, settings, now)
    assert sent.status == InspectionStatus.ANSWERED and sent.rating == Rating.GOOD
    assert sent.survey_stage == SurveyStage.AWAITING_COMMENT
    assert sender.texts[0][1] in messages.followup_question
    assert sent.comment_window_closes_at == now + timedelta(minutes=settings.free_text_window_minutes)
    assert survey_flow.comment_window_open(db, sent, now + timedelta(minutes=10))
    survey_flow.record_comment(db, sent, "Muy amables", sender, messages, now + timedelta(minutes=10))
    assert sent.comment == "Muy amables" and sent.survey_stage == SurveyStage.COMPLETED
    assert sender.texts[1][1] in messages.thanks and sent.thanks_sent_at is not None


def test_rating_is_only_recorded_once(db: Session, sent: Inspection, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    survey_flow.record_rating(db, sent, Rating.GOOD, "rule", sender, messages, settings, SENT)
    survey_flow.record_rating(db, sent, Rating.BAD, "rule", sender, messages, settings, SENT)
    assert sent.rating == Rating.GOOD and len(sender.texts) == 1


def test_rating_with_comment_goes_straight_to_thanks(
    db: Session, sent: Inspection, messages: Messages, settings: Settings
) -> None:
    sender = FakeSender()
    survey_flow.record_rating(db, sent, Rating.OKAY, "llm", sender, messages, settings, SENT, comment="Tardaron un poco")
    assert sent.comment == "Tardaron un poco" and sent.survey_stage == SurveyStage.COMPLETED
    assert len(sender.texts) == 1 and sender.texts[0][1] in messages.thanks


def test_comment_window_closes_after_timeout(db: Session, sent: Inspection, messages: Messages, settings: Settings) -> None:
    survey_flow.record_rating(db, sent, Rating.GOOD, "rule", FakeSender(), messages, settings, SENT)
    late = SENT + timedelta(minutes=settings.free_text_window_minutes + 1)
    assert not survey_flow.comment_window_open(db, sent, late)


def test_comment_window_closes_when_employee_joins(
    db: Session, sent: Inspection, messages: Messages, settings: Settings
) -> None:
    survey_flow.record_rating(db, sent, Rating.GOOD, "rule", FakeSender(), messages, settings, SENT)
    db.add(EmployeeMessage(wa_message_id="e1", phone=sent.customer_phone, sent_at=SENT + timedelta(minutes=3)))
    db.commit()
    # The customer's next message is part of the human conversation.
    assert not survey_flow.comment_window_open(db, sent, SENT + timedelta(minutes=4))
    # A message written before the employee joined still counts.
    assert survey_flow.comment_window_open(db, sent, SENT + timedelta(minutes=2))


def test_followup_send_failure_keeps_rating(db: Session, sent: Inspection, messages: Messages, settings: Settings) -> None:
    sender = FakeSender(errors=[WhatsAppError("down", retryable=True)])
    survey_flow.record_rating(db, sent, Rating.BAD, "rule", sender, messages, settings, SENT)
    assert sent.rating == Rating.BAD and sent.survey_stage == SurveyStage.COMPLETED


def test_followup_variants_rotate_per_customer(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    used = []
    for n in range(4):
        i = scheduled_inspection(db, SENT, status=InspectionStatus.SENT, sent_at=SENT,
                                 survey_stage=SurveyStage.AWAITING_RATING, service_description=f"s{n}")
        survey_flow.record_rating(db, i, Rating.GOOD, "rule", sender, messages, settings, SENT)
        used.append(i.followup_variant)
    assert all(used[k] != used[k - 1] for k in range(1, len(used)))
