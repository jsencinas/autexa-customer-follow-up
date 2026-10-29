"""Interpreting stored customer messages and acting on them (asynchronously, in the worker).

Pipeline: gate (recent survey only) → rules → local LLM → asymmetric decision policy.
A missed opt-out is far worse than a false one, so the opt-out threshold is low; unsure
ratings go to the admin review queue. The LLM never writes messages.
"""

import logging
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.llm_client import LlmInterpretation
from app.integrations.ollama import OllamaError
from app.models.db_models import InboundMessage, Inspection
from app.models.enums import (
    InboundStatus,
    InspectionStatus,
    InterpretationSource,
    OptOutSource,
    Rating,
    SurveyStage,
)
from app.services import opt_out, survey_flow
from app.services.messages import Messages
from app.services.reply_rules import Interpretation, interpret_by_rules
from app.services.retry import backoff
from app.services.surveys import MessageSender
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

CLAIM_LEASE = timedelta(minutes=10)


class ReplyInterpreter(Protocol):
    """The LLM client (or a test fake)."""

    model: str

    def interpret(self, bot_message: str, customer_reply: str) -> LlmInterpretation:
        """Classify a reply."""
        ...


def claim_due_messages(db: Session, now: datetime | None = None, limit: int = 20) -> list[int]:
    """Claim pending messages by pushing their next attempt forward (a crash frees them after the lease)."""
    now = now or utcnow()
    due = (InboundMessage.status == InboundStatus.PENDING, InboundMessage.next_attempt_at <= now)
    ids = list(db.scalars(select(InboundMessage.id).where(*due).order_by(InboundMessage.id).limit(limit)))
    claimed = []
    for message_id in ids:
        result = db.execute(
            update(InboundMessage).where(InboundMessage.id == message_id, *due).values(next_attempt_at=now + CLAIM_LEASE)
        )
        if result.rowcount == 1:  # type: ignore[attr-defined]
            claimed.append(message_id)
    db.commit()
    return claimed


def find_survey(db: Session, message: InboundMessage, window_days: int, survey_id: int | None) -> Inspection | None:
    """The survey this message replies to: the button's survey, or this number's latest recent one."""
    if survey_id is not None:
        inspection = db.get(Inspection, survey_id)
        if inspection is not None and inspection.customer_phone == message.from_phone and inspection.sent_at:
            return inspection
        return None
    since = message.sent_at - timedelta(days=window_days)
    return db.scalar(
        select(Inspection)
        .where(
            Inspection.customer_phone == message.from_phone,
            Inspection.status.in_([InspectionStatus.SENT, InspectionStatus.ANSWERED]),
            Inspection.sent_at >= since,
            Inspection.sent_at <= message.sent_at,
        )
        .order_by(Inspection.sent_at.desc())
    )


def _bot_context(inspection: Inspection, messages: Messages) -> str:
    """The last thing the bot said to the customer, for the LLM prompt."""
    if inspection.survey_stage == SurveyStage.AWAITING_COMMENT and inspection.followup_variant is not None:
        return messages.followup_question[inspection.followup_variant % len(messages.followup_question)]
    template = messages.template(inspection.template_name or "")
    return template.text if template else "Encuesta de satisfacción: ¿qué tal fue el servicio? (Bien / Regular / Mal)"


def _from_llm(result: LlmInterpretation, model: str) -> Interpretation:
    return Interpretation(
        intents=set(result.intents),
        source=InterpretationSource.LLM,
        confidence=result.confidence,
        rating=Rating(result.rating) if result.rating else None,
        comment=result.comment,
        model=model,
    )


def _save(message: InboundMessage, interp: Interpretation, status: InboundStatus, inspection: Inspection | None) -> None:
    message.status = status
    message.inspection_id = inspection.id if inspection else None
    message.interpretation_source = interp.source
    message.intents = sorted(interp.intents)
    message.interpreted_rating = interp.rating
    message.interpreted_comment = interp.comment
    message.confidence = interp.confidence
    message.model_name = interp.model


def process_message(
    db: Session,
    message_id: int,
    interpreter: ReplyInterpreter,
    sender: MessageSender,
    messages: Messages,
    settings: Settings,
    now: datetime | None = None,
) -> InboundMessage:
    """Interpret one stored message and apply the decision policy."""
    now = now or utcnow()
    message = db.get(InboundMessage, message_id)
    if message is None or message.status != InboundStatus.PENDING:
        raise LookupError(message_id)
    message.attempts += 1

    rules = interpret_by_rules(message.text, message.button_payload, messages)
    inspection = find_survey(db, message, settings.interpretation_window_days, rules.survey_id if rules else None)
    if inspection is None:
        # Ordinary chat for the employees: never sent to the LLM, no reply, nothing stored about it.
        message.status = InboundStatus.IGNORED
        db.commit()
        return message

    interp = rules
    if interp is None:
        if not message.text:
            message.status = InboundStatus.IGNORED
            db.commit()
            return message
        try:
            interp = _from_llm(interpreter.interpret(_bot_context(inspection, messages), message.text), interpreter.model)
        except OllamaError as exc:
            message.last_error = str(exc)[:500]
            if message.attempts >= settings.max_interpretation_attempts:
                # Hand it to a human instead of losing a possible opt-out.
                message.status = InboundStatus.NEEDS_REVIEW
                message.inspection_id = inspection.id
                logger.warning("message %s sent to review after LLM failures", message.id)
            else:
                message.next_attempt_at = now + backoff(message.attempts, settings.retry_base_seconds)
            db.commit()
            return message

    status = _decide(db, message, interp, inspection, sender, messages, settings, now)
    _save(message, interp, status, inspection)
    db.commit()
    return message


def _decide(
    db: Session,
    message: InboundMessage,
    interp: Interpretation,
    inspection: Inspection,
    sender: MessageSender,
    messages: Messages,
    settings: Settings,
    now: datetime,
) -> InboundStatus:
    phone = message.from_phone

    # Opt-out first; it keeps working even after an employee joined the conversation.
    if "opt_out" in interp.intents:
        if interp.confidence >= settings.opt_out_threshold:
            source = OptOutSource.BUTTON if interp.from_button else OptOutSource.INTERPRETED
            entry = opt_out.add(
                db, phone, source, inspection_id=inspection.id, inbound_message_id=message.id, now=now
            )
            opt_out.send_confirmation(db, entry, sender, messages, now)
            return InboundStatus.DONE
        return InboundStatus.NEEDS_REVIEW

    if interp.from_button and interp.rating:
        survey_flow.record_rating(db, inspection, interp.rating, interp.source, sender, messages, settings, now)
        return InboundStatus.DONE

    assert inspection.sent_at is not None
    if survey_flow.employee_joined(db, phone, inspection.sent_at, message.sent_at) and not (
        survey_flow.comment_window_open(db, inspection, message.sent_at)
    ):
        # A human took over: the message belongs to their conversation.
        return InboundStatus.IGNORED

    if "rating" in interp.intents and interp.rating and survey_flow.can_rate(inspection):
        if interp.confidence < settings.rating_threshold:
            return InboundStatus.NEEDS_REVIEW
        comment = interp.comment if "comment" in interp.intents else None
        survey_flow.record_rating(
            db, inspection, interp.rating, interp.source, sender, messages, settings, now, comment=comment
        )
        return InboundStatus.DONE

    if ("comment" in interp.intents or "rating" in interp.intents) and survey_flow.comment_window_open(
        db, inspection, message.sent_at
    ):
        survey_flow.record_comment(db, inspection, interp.comment or message.text or "", sender, messages, now)
        return InboundStatus.DONE

    return InboundStatus.IGNORED
