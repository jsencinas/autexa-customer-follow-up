"""The opt-out list: numbers that must never receive bot messages again.

Opting out only stops bot messages; employees can still chat with the customer by hand.
The list is never touched by the retention cleanup.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.whatsapp import WhatsAppError
from app.logging_utils import mask_phone
from app.models.db_models import Inspection, OptOut
from app.models.enums import InspectionStatus, OptOutSource, SurveyStage
from app.services import duplicates
from app.services.admin_settings import record_audit
from app.services.messages import Messages
from app.services.surveys import MessageSender
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class OptOutError(ValueError):
    """Invalid opt-out operation."""


def is_opted_out(db: Session, phone: str | None) -> bool:
    """True if the (E.164) number is on the opt-out list."""
    if not phone:
        return False
    return db.scalar(select(OptOut.id).where(OptOut.phone == phone)) is not None


def add(
    db: Session,
    phone: str,
    source: OptOutSource,
    *,
    inspection_id: int | None = None,
    inbound_message_id: int | None = None,
    created_by_id: int | None = None,
    now: datetime | None = None,
) -> OptOut:
    """Add a number (idempotent) and cancel every survey not yet sent to it."""
    now = now or utcnow()
    entry = db.scalar(select(OptOut).where(OptOut.phone == phone))
    if entry is None:
        entry = OptOut(
            phone=phone,
            source=source,
            opted_out_at=now,
            inspection_id=inspection_id,
            inbound_message_id=inbound_message_id,
            created_by_id=created_by_id,
        )
        db.add(entry)
        if source == OptOutSource.ADMIN:
            record_audit(db, created_by_id, "add_opt_out", mask_phone(phone), None, None)
        logger.info("opt-out added for %s (%s)", mask_phone(phone), source)
    for inspection in db.scalars(select(Inspection).where(Inspection.customer_phone == phone)):
        if inspection.status == InspectionStatus.SCHEDULED and inspection.send_claimed_at is None:
            inspection.status = InspectionStatus.OPTED_OUT
            duplicates.release(inspection)
        elif inspection.survey_stage in (SurveyStage.AWAITING_RATING, SurveyStage.AWAITING_COMMENT):
            inspection.survey_stage = SurveyStage.COMPLETED
    db.commit()
    return entry


def send_confirmation(db: Session, entry: OptOut, sender: MessageSender, messages: Messages, now: datetime) -> None:
    """Tell the customer they will not get more automatic messages (once)."""
    if entry.confirmation_sent_at is not None:
        return
    try:
        sender.send_text(entry.phone, messages.opt_out_confirmation[0])
    except WhatsAppError as exc:
        logger.warning("opt-out confirmation to %s not sent: %s", mask_phone(entry.phone), exc)
        return
    entry.confirmation_sent_at = now
    db.commit()


def remove(db: Session, opt_out_id: int, reason: str, user_id: int | None) -> None:
    """Remove a number from the list; a reason is mandatory and recorded in the audit log."""
    reason = reason.strip()
    if not reason:
        raise OptOutError("Indica el motivo para quitar el número de la lista.")
    entry = db.get(OptOut, opt_out_id)
    if entry is None:
        raise OptOutError("Número no encontrado.")
    record_audit(
        db,
        user_id,
        "remove_opt_out",
        mask_phone(entry.phone),
        {"source": entry.source, "opted_out_at": entry.opted_out_at.isoformat()},
        None,
        reason,
    )
    db.delete(entry)
    db.commit()


def list_all(db: Session) -> list[OptOut]:
    """Every opted-out number, most recent first."""
    return list(db.scalars(select(OptOut).order_by(OptOut.opted_out_at.desc())))
