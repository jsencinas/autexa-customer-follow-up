"""Storing webhook events. Nothing is interpreted here; the worker does that asynchronously."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db_models import ContactActivity, EmployeeMessage, InboundMessage, Inspection
from app.models.enums import InboundStatus, InspectionStatus, SurveyStage
from app.models.schemas import WaEcho, WaMessage, WaStatus, WebhookPayload
from app.services.normalization import canonical_mexico
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


@dataclass
class StoreSummary:
    """How many events of each kind were stored (duplicates are not counted)."""

    messages: int = 0
    echoes: int = 0
    statuses: int = 0


def to_e164(wa_number: str) -> str:
    """WhatsApp sends numbers as digits without '+' (and Mexican mobiles with a legacy 521 prefix)."""
    digits = "".join(ch for ch in wa_number if ch.isdigit())
    return canonical_mexico(f"+{digits}")


def _timestamp(value: str, fallback: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return fallback


def _touch_activity(db: Session, phone: str, *, customer: datetime | None = None, employee: datetime | None = None) -> None:
    activity = db.get(ContactActivity, phone)
    if activity is None:
        activity = ContactActivity(phone=phone)
        db.add(activity)
    if customer and (activity.last_customer_message_at is None or customer > activity.last_customer_message_at):
        activity.last_customer_message_at = customer
    if employee and (activity.last_employee_message_at is None or employee > activity.last_employee_message_at):
        activity.last_employee_message_at = employee


def _store_message(db: Session, message: WaMessage, now: datetime) -> bool:
    if db.scalar(select(InboundMessage.id).where(InboundMessage.wa_message_id == message.id)):
        return False
    text: str | None = None
    payload: str | None = None
    if message.text is not None:
        text = message.text.body
    if message.button is not None:
        payload, text = message.button.payload, message.button.text
    if message.interactive is not None and message.interactive.button_reply is not None:
        payload, text = message.interactive.button_reply.id, message.interactive.button_reply.title
    phone = to_e164(message.from_)
    sent_at = _timestamp(message.timestamp, now)
    db.add(
        InboundMessage(
            wa_message_id=message.id,
            from_phone=phone,
            message_type=message.type,
            text=text,
            button_payload=payload,
            sent_at=sent_at,
            received_at=now,
            status=InboundStatus.PENDING,
            next_attempt_at=now,
        )
    )
    _touch_activity(db, phone, customer=sent_at)
    return True


def _store_echo(db: Session, echo: WaEcho, now: datetime) -> bool:
    if db.scalar(select(EmployeeMessage.id).where(EmployeeMessage.wa_message_id == echo.id)):
        return False
    phone = to_e164(echo.to)
    sent_at = _timestamp(echo.timestamp, now)
    db.add(EmployeeMessage(wa_message_id=echo.id, phone=phone, sent_at=sent_at))
    _touch_activity(db, phone, employee=sent_at)
    return True


def _apply_status(db: Session, status: WaStatus) -> bool:
    """A survey template that Meta reports as undeliverable is marked failed."""
    if status.status != "failed":
        return False
    inspection = db.scalar(select(Inspection).where(Inspection.wa_message_id == status.id))
    if inspection is None or inspection.status != InspectionStatus.SENT:
        return False
    if inspection.survey_stage != SurveyStage.AWAITING_RATING:
        return False
    inspection.status = InspectionStatus.FAILED
    inspection.last_error = "WhatsApp reported the survey as undelivered"
    inspection.dedup_key = None
    return True


def store_webhook(
    db: Session, payload: WebhookPayload, phone_number_id: str, now: datetime | None = None
) -> StoreSummary:
    """Persist customer messages, employee echoes and delivery failures. Idempotent per message id."""
    now = now or utcnow()
    summary = StoreSummary()
    for entry in payload.entry:
        for change in entry.changes:
            value = change.value
            if value.metadata and value.metadata.phone_number_id and value.metadata.phone_number_id != phone_number_id:
                continue  # another number on the same app
            for message in value.messages:
                summary.messages += _store_message(db, message, now)
            for echo in value.message_echoes:
                summary.echoes += _store_echo(db, echo, now)
            for status in value.statuses:
                summary.statuses += _apply_status(db, status)
    try:
        db.commit()
    except IntegrityError:
        # The same delivery is being stored concurrently; Meta's retry is already handled.
        db.rollback()
        logger.info("duplicate webhook delivery ignored")
        return StoreSummary()
    if summary.messages or summary.echoes:
        logger.info("webhook stored %s messages, %s echoes", summary.messages, summary.echoes)
    return summary
