from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.ollama import OllamaUnavailable
from app.models.db_models import EmployeeMessage, InboundMessage, Inspection, OptOut
from app.models.enums import InboundStatus, InspectionStatus, Rating, SurveyStage
from app.services import opt_out, reply_interpretation
from app.services.messages import Messages, load_messages
from app.services.reply_rules import interpret_by_rules, normalize_text
from tests.fakes import FakeInterpreter, FakeSender, scheduled_inspection

SENT = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
PHONE = "+34612345678"


@pytest.fixture
def messages(settings: Settings) -> Messages:
    return load_messages(settings.messages_file)


@pytest.fixture
def survey(db: Session) -> Inspection:
    return scheduled_inspection(
        db, SENT, status=InspectionStatus.SENT, sent_at=SENT, survey_stage=SurveyStage.AWAITING_RATING,
        template_name="encuesta_satisfaccion_v1",
    )


def inbound(db: Session, text: str | None = None, payload: str | None = None, at: datetime | None = None,
            phone: str = PHONE) -> InboundMessage:
    message = InboundMessage(
        wa_message_id=f"wamid.{db.query(InboundMessage).count() + 1}",
        from_phone=phone,
        message_type="button" if payload else "text",
        text=text,
        button_payload=payload,
        sent_at=at or SENT + timedelta(minutes=5),
        next_attempt_at=SENT,
    )
    db.add(message)
    db.commit()
    return message


def llm(intents: list[str], rating: str | None = None, comment: str | None = None, confidence: float = 0.95) -> dict[str, Any]:
    return {"intents": intents, "rating": rating, "comment": comment, "confidence": confidence}


def run(db: Session, message: InboundMessage, settings: Settings, messages: Messages,
        interpreter: FakeInterpreter | None = None, sender: FakeSender | None = None,
        now: datetime | None = None) -> InboundMessage:
    return reply_interpretation.process_message(
        db, message.id, interpreter or FakeInterpreter(), sender or FakeSender(), messages, settings,
        now or message.sent_at,
    )


# --- rules -------------------------------------------------------------------


def test_normalize_text() -> None:
    assert normalize_text("¡Muy BIEN!!") == "muy bien"
    assert normalize_text("No me escribáis más.") == "no me escribais mas"
    assert normalize_text("👍") == "👍"


def test_rules(messages: Messages) -> None:
    assert interpret_by_rules("Bien", None, messages).rating == Rating.GOOD  # type: ignore[union-attr]
    assert interpret_by_rules("¡¡regular!!", None, messages).rating == Rating.OKAY  # type: ignore[union-attr]
    assert interpret_by_rules("👎", None, messages).rating == Rating.BAD  # type: ignore[union-attr]
    assert interpret_by_rules("STOP", None, messages).intents == {"opt_out"}  # type: ignore[union-attr]
    assert interpret_by_rules("Por favor, dejad de escribirme", None, messages).intents == {"opt_out"}  # type: ignore[union-attr]
    assert interpret_by_rules("La bajada de precio", None, messages) is None  # "baja" only as a whole word
    assert interpret_by_rules("bien, pero tardaron mucho", None, messages) is None  # needs the LLM
    button = interpret_by_rules(None, "survey:5:bad", messages)
    assert button and button.rating == Rating.BAD and button.survey_id == 5 and button.from_button


# --- pipeline ------------------------------------------------------------------


def test_button_rating_needs_no_llm(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter, sender = FakeInterpreter(), FakeSender()
    message = run(db, inbound(db, "Bien", f"survey:{survey.id}:good"), settings, messages, interpreter, sender)
    assert message.status == InboundStatus.DONE and not interpreter.calls
    db.refresh(survey)
    assert survey.rating == Rating.GOOD and survey.status == InspectionStatus.ANSWERED
    assert sender.texts and sender.texts[0][1] in messages.followup_question


def test_rating_with_extra_text_is_stored(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter([llm(["rating", "comment"], "good", "aunque tardaron un poco")])
    message = run(db, inbound(db, "Bien, aunque tardaron un poco"), settings, messages, interpreter)
    assert message.status == InboundStatus.DONE and message.interpretation_source == "llm"
    db.refresh(survey)
    assert survey.rating == Rating.GOOD and survey.comment == "aunque tardaron un poco"
    bot_message, reply = interpreter.calls[0]
    assert "Hola" in bot_message and reply == "Bien, aunque tardaron un poco"


def test_unrelated_message_is_ignored_without_reply(
    db: Session, survey: Inspection, settings: Settings, messages: Messages
) -> None:
    sender = FakeSender()
    interpreter = FakeInterpreter([llm(["unrelated"])])
    message = run(db, inbound(db, "¿Cuánto cuesta cambiar las ruedas?"), settings, messages, interpreter, sender)
    assert message.status == InboundStatus.IGNORED
    assert not sender.texts and not sender.templates
    db.refresh(survey)
    assert survey.rating is None and survey.status == InspectionStatus.SENT


def test_opt_out_in_free_wording_is_applied(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    sender = FakeSender()
    pending = scheduled_inspection(db, SENT + timedelta(days=3), service_description="otro servicio")
    interpreter = FakeInterpreter([llm(["opt_out"], confidence=0.6)])
    message = run(db, inbound(db, "Quitadme de vuestra lista, gracias"), settings, messages, interpreter, sender)
    assert message.status == InboundStatus.DONE
    assert opt_out.is_opted_out(db, PHONE)
    assert db.query(OptOut).one().source == "interpreted"
    db.refresh(pending)
    assert pending.status == InspectionStatus.OPTED_OUT  # already-scheduled survey cancelled
    assert sender.texts == [(PHONE, messages.opt_out_confirmation[0])]


def test_opt_out_button(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    run(db, inbound(db, "No enviar más mensajes", f"survey:{survey.id}:opt_out"), settings, messages)
    assert db.query(OptOut).one().source == "button"


def test_very_unsure_opt_out_goes_to_review(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter([llm(["opt_out"], confidence=0.3)])
    assert run(db, inbound(db, "uff otra vez"), settings, messages, interpreter).status == InboundStatus.NEEDS_REVIEW
    assert not opt_out.is_opted_out(db, PHONE)


def test_low_confidence_rating_goes_to_review(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter([llm(["rating"], "okay", confidence=0.5)])
    message = run(db, inbound(db, "psé"), settings, messages, interpreter)
    assert message.status == InboundStatus.NEEDS_REVIEW and message.interpreted_rating == "okay"
    db.refresh(survey)
    assert survey.rating is None


def test_number_without_recent_survey_never_reaches_llm(db: Session, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter()
    message = run(db, inbound(db, "Hola, ¿tenéis cita el lunes?", phone="+34699999999"), settings, messages, interpreter)
    assert message.status == InboundStatus.IGNORED and not interpreter.calls


def test_old_survey_is_outside_the_window(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter()
    late = SENT + timedelta(days=settings.interpretation_window_days + 1)
    assert run(db, inbound(db, "Mal", at=late), settings, messages, interpreter).status == InboundStatus.IGNORED


def test_messages_are_retried_while_llm_is_down(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    interpreter = FakeInterpreter([OllamaUnavailable("down")] * settings.max_interpretation_attempts)
    message = inbound(db, "Bien pero caro")
    now = message.sent_at
    assert reply_interpretation.claim_due_messages(db, now) == [message.id]
    run(db, message, settings, messages, interpreter, now=now)
    assert message.status == InboundStatus.PENDING and message.next_attempt_at > now  # type: ignore[operator]
    for _ in range(settings.max_interpretation_attempts - 1):
        now = message.next_attempt_at  # type: ignore[assignment]
        assert reply_interpretation.claim_due_messages(db, now) == [message.id]
        run(db, message, settings, messages, interpreter, now=now)
    # After the retry limit a human reviews it instead of losing a possible opt-out.
    assert message.status == InboundStatus.NEEDS_REVIEW


def test_free_text_after_employee_message_is_not_stored(
    db: Session, survey: Inspection, settings: Settings, messages: Messages
) -> None:
    run(db, inbound(db, None, f"survey:{survey.id}:good"), settings, messages)
    db.add(EmployeeMessage(wa_message_id="echo1", phone=PHONE, sent_at=SENT + timedelta(minutes=6)))
    db.commit()
    interpreter = FakeInterpreter([llm(["comment"], comment="Vale, ¿y el presupuesto?")])
    message = run(db, inbound(db, "Vale, ¿y el presupuesto?", at=SENT + timedelta(minutes=7)), settings, messages, interpreter)
    assert message.status == InboundStatus.IGNORED
    db.refresh(survey)
    assert survey.comment is None


def test_opt_out_still_works_after_employee_joined(
    db: Session, survey: Inspection, settings: Settings, messages: Messages
) -> None:
    db.add(EmployeeMessage(wa_message_id="echo1", phone=PHONE, sent_at=SENT + timedelta(minutes=1)))
    db.commit()
    interpreter = FakeInterpreter([llm(["opt_out"], confidence=0.7)])
    run(db, inbound(db, "y no me mandéis encuestas"), settings, messages, interpreter)
    assert opt_out.is_opted_out(db, PHONE)


def test_text_rating_ignored_after_employee_joined(
    db: Session, survey: Inspection, settings: Settings, messages: Messages
) -> None:
    db.add(EmployeeMessage(wa_message_id="echo1", phone=PHONE, sent_at=SENT + timedelta(minutes=1)))
    db.commit()
    assert run(db, inbound(db, "Mal"), settings, messages).status == InboundStatus.IGNORED
    db.refresh(survey)
    assert survey.rating is None


def test_comment_inside_window(db: Session, survey: Inspection, settings: Settings, messages: Messages) -> None:
    sender = FakeSender()
    run(db, inbound(db, None, f"survey:{survey.id}:okay"), settings, messages, sender=sender)
    interpreter = FakeInterpreter([llm(["comment"], comment="El café de la sala de espera estaba frío")])
    run(db, inbound(db, "El café de la sala de espera estaba frío", at=SENT + timedelta(minutes=8)), settings, messages,
        interpreter, sender)
    db.refresh(survey)
    assert survey.comment == "El café de la sala de espera estaba frío" and survey.survey_stage == SurveyStage.COMPLETED
    assert sender.texts[-1][1] in messages.thanks
