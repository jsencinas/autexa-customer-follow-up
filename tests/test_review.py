from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import AuditLog, InboundMessage, Inspection, OptOut, User
from app.models.enums import InboundStatus, InspectionStatus, Rating, SurveyStage
from app.services import opt_out, review
from app.services.messages import Messages, load_messages
from tests.conftest import csrf_from
from tests.fakes import FakeSender, scheduled_inspection

SENT = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
PHONE = "+34612345678"


@pytest.fixture
def messages(settings: Settings) -> Messages:
    return load_messages(settings.messages_file)


@pytest.fixture
def survey(db: Session) -> Inspection:
    return scheduled_inspection(db, SENT, status=InspectionStatus.SENT, sent_at=SENT,
                                survey_stage=SurveyStage.AWAITING_RATING)


@pytest.fixture
def unsure(db: Session, survey: Inspection) -> InboundMessage:
    message = InboundMessage(wa_message_id="w1", from_phone=PHONE, message_type="text", text="psé",
                             sent_at=SENT + timedelta(minutes=3), status=InboundStatus.NEEDS_REVIEW,
                             inspection_id=survey.id, intents=["rating"], interpreted_rating="okay", confidence=0.5)
    db.add(message)
    db.commit()
    return message


def test_admin_sets_rating_without_messaging_customer(
    db: Session, survey: Inspection, unsure: InboundMessage, admin_user: User, messages: Messages, settings: Settings
) -> None:
    sender = FakeSender()
    review.resolve(db, unsure.id, review.ReviewDecision.RATING, admin_user.id, sender, messages, settings, rating=Rating.OKAY)
    db.refresh(survey)
    assert survey.rating == Rating.OKAY and survey.rating_source == "admin"
    assert survey.survey_stage == SurveyStage.COMPLETED and not sender.texts
    assert unsure.status == InboundStatus.DONE and unsure.reviewed_by_id == admin_user.id
    assert db.query(AuditLog).filter_by(action="review_reply").count() == 1
    with pytest.raises(review.ReviewError):
        review.resolve(db, unsure.id, review.ReviewDecision.UNRELATED, admin_user.id, sender, messages, settings)


def test_admin_marks_unrelated(db: Session, survey: Inspection, unsure: InboundMessage, admin_user: User,
                               messages: Messages, settings: Settings) -> None:
    review.resolve(db, unsure.id, review.ReviewDecision.UNRELATED, admin_user.id, FakeSender(), messages, settings)
    db.refresh(survey)
    assert survey.rating is None and unsure.review_decision == "unrelated"


def test_admin_applies_opt_out(db: Session, survey: Inspection, unsure: InboundMessage, admin_user: User,
                               messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    review.resolve(db, unsure.id, review.ReviewDecision.OPT_OUT, admin_user.id, sender, messages, settings)
    assert opt_out.is_opted_out(db, PHONE)
    assert sender.texts == [(PHONE, messages.opt_out_confirmation[0])]


def test_rating_requires_a_value(db: Session, unsure: InboundMessage, admin_user: User, messages: Messages,
                                 settings: Settings) -> None:
    with pytest.raises(review.ReviewError):
        review.resolve(db, unsure.id, review.ReviewDecision.RATING, admin_user.id, FakeSender(), messages, settings)


def test_review_page_flow(admin_client: TestClient, db: Session, survey: Inspection, unsure: InboundMessage) -> None:
    page = admin_client.get("/admin/review")
    assert "psé" in page.text and "María García" in page.text
    response = admin_client.post(f"/admin/review/{unsure.id}",
                                 data={"decision": "rating", "rating": "bad", "csrf_token": csrf_from(page.text)},
                                 follow_redirects=False)
    assert response.status_code == 303
    db.refresh(survey)
    assert survey.rating == "bad"
    assert "No hay nada pendiente" in admin_client.get("/admin/review").text


def test_opt_out_add_is_idempotent_and_cancels_scheduled(db: Session) -> None:
    scheduled = scheduled_inspection(db, SENT + timedelta(days=1), dedup_key="k")
    opt_out.add(db, PHONE, "admin")
    opt_out.add(db, PHONE, "interpreted")
    assert db.query(OptOut).count() == 1
    db.refresh(scheduled)
    assert scheduled.status == InspectionStatus.OPTED_OUT and scheduled.dedup_key is None


def test_opt_out_removal_requires_reason(db: Session) -> None:
    entry = opt_out.add(db, PHONE, "admin")
    with pytest.raises(opt_out.OptOutError):
        opt_out.remove(db, entry.id, "  ", None)
    opt_out.remove(db, entry.id, "El cliente pidió volver a recibirlas", None)
    assert not opt_out.is_opted_out(db, PHONE)
    log = db.query(AuditLog).filter_by(action="remove_opt_out").one()
    assert log.reason == "El cliente pidió volver a recibirlas" and PHONE not in (log.target or "")


def test_opt_out_pages(admin_client: TestClient, db: Session, sender: FakeSender) -> None:
    page = admin_client.get("/admin/opt-outs")
    token = csrf_from(page.text)
    assert admin_client.post("/admin/opt-outs", data={"phone": "123", "csrf_token": token}).status_code == 400
    assert admin_client.post("/admin/opt-outs", data={"phone": "612 345 678", "csrf_token": token},
                             follow_redirects=False).status_code == 303
    assert opt_out.is_opted_out(db, PHONE) and not sender.texts  # no message for manual additions
    entry = db.query(OptOut).one()
    assert admin_client.post(f"/admin/opt-outs/{entry.id}/remove", data={"reason": "", "csrf_token": token}).status_code == 400
    assert admin_client.post(f"/admin/opt-outs/{entry.id}/remove", data={"reason": "pidió volver", "csrf_token": token},
                             follow_redirects=False).status_code == 303
    assert not opt_out.is_opted_out(db, PHONE)


def test_review_routes_reject_employee(employee_client: TestClient) -> None:
    assert employee_client.get("/admin/review").status_code == 403
    assert employee_client.get("/admin/opt-outs").status_code == 403
