"""Admin review queue for replies the bot was not sure about."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import InboundMessage, Inspection
from app.models.enums import InboundStatus, InterpretationSource, OptOutSource, Rating
from app.services import opt_out, survey_flow
from app.services.admin_settings import record_audit
from app.services.messages import Messages
from app.services.surveys import MessageSender
from app.timeutils import utcnow


class ReviewDecision(StrEnum):
    """What the admin decided a reply meant."""

    RATING = "rating"
    UNRELATED = "unrelated"
    OPT_OUT = "opt_out"


class ReviewError(ValueError):
    """Invalid review action."""


def pending_reviews(db: Session) -> list[tuple[InboundMessage, Inspection | None]]:
    """Messages waiting for an admin, oldest first, with their survey."""
    rows = db.scalars(
        select(InboundMessage).where(InboundMessage.status == InboundStatus.NEEDS_REVIEW).order_by(InboundMessage.id)
    )
    return [(m, db.get(Inspection, m.inspection_id) if m.inspection_id else None) for m in rows]


def count_pending(db: Session) -> int:
    """Number of messages waiting for review."""
    return db.scalar(select(func.count(InboundMessage.id)).where(InboundMessage.status == InboundStatus.NEEDS_REVIEW)) or 0


def resolve(
    db: Session,
    message_id: int,
    decision: ReviewDecision,
    user_id: int,
    sender: MessageSender,
    messages: Messages,
    settings: Settings,
    rating: Rating | None = None,
    now: datetime | None = None,
) -> InboundMessage:
    """Apply the admin's decision and record who made it."""
    now = now or utcnow()
    message = db.get(InboundMessage, message_id)
    if message is None or message.status != InboundStatus.NEEDS_REVIEW:
        raise ReviewError("Este mensaje ya no está pendiente de revisión.")
    inspection = db.get(Inspection, message.inspection_id) if message.inspection_id else None

    if decision == ReviewDecision.RATING:
        if rating is None:
            raise ReviewError("Elige una valoración.")
        if inspection is None or not survey_flow.can_rate(inspection):
            raise ReviewError("La encuesta ya no admite valoración.")
        # Decided later by a person: store it without messaging the customer again.
        survey_flow.record_rating(
            db, inspection, rating, InterpretationSource.ADMIN, sender, messages, settings, now, notify=False
        )
    elif decision == ReviewDecision.OPT_OUT:
        entry = opt_out.add(
            db,
            message.from_phone,
            OptOutSource.ADMIN,
            inspection_id=inspection.id if inspection else None,
            inbound_message_id=message.id,
            created_by_id=user_id,
            now=now,
        )
        opt_out.send_confirmation(db, entry, sender, messages, now)

    message.status = InboundStatus.DONE
    message.interpretation_source = InterpretationSource.ADMIN
    message.reviewed_by_id = user_id
    message.reviewed_at = now
    message.review_decision = decision if decision != ReviewDecision.RATING else f"rating:{rating}"
    record_audit(db, user_id, "review_reply", f"message:{message.id}", None, message.review_decision)
    db.commit()
    return message
