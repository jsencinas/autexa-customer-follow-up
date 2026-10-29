"""The survey conversation after the template was sent: rating, optional comment, thanks."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.whatsapp import WhatsAppError
from app.models.db_models import EmployeeMessage, Inspection
from app.models.enums import InspectionStatus, Rating, SurveyStage
from app.services.messages import Messages, pick_variant
from app.services.surveys import MessageSender

logger = logging.getLogger(__name__)


def _recent_variants(db: Session, phone: str, column, exclude_id: int, limit: int) -> list[int]:  # type: ignore[no-untyped-def]
    query = (
        select(column)
        .where(Inspection.customer_phone == phone, column.is_not(None), Inspection.id != exclude_id)
        .order_by(Inspection.id.desc())
        .limit(limit)
    )
    return list(db.scalars(query))


def _pick_text(db: Session, inspection: Inspection, options: list[str], column) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    """Pick a text variant this customer did not get recently; returns (index, text)."""
    indexes = [str(i) for i in range(len(options))]
    recent = [str(i) for i in _recent_variants(db, inspection.customer_phone or "", column, inspection.id, len(options) - 1)]
    chosen = int(pick_variant(indexes, recent))
    return chosen, options[chosen]


def employee_joined(db: Session, phone: str, after: datetime, before: datetime) -> bool:
    """True if an employee messaged this customer by hand between the two moments."""
    return (
        db.scalar(
            select(EmployeeMessage.id).where(
                EmployeeMessage.phone == phone, EmployeeMessage.sent_at > after, EmployeeMessage.sent_at < before
            )
        )
        is not None
    )


def can_rate(inspection: Inspection) -> bool:
    """The survey is waiting for its rating."""
    return inspection.status == InspectionStatus.SENT and inspection.survey_stage == SurveyStage.AWAITING_RATING


def comment_window_open(db: Session, inspection: Inspection, message_sent_at: datetime) -> bool:
    """The bot asked for a comment, the window has not closed, and no employee has joined since."""
    if inspection.survey_stage != SurveyStage.AWAITING_COMMENT or inspection.comment_window_closes_at is None:
        return False
    if message_sent_at > inspection.comment_window_closes_at:
        return False
    asked_at = inspection.followup_sent_at or inspection.rating_at
    if asked_at is None:
        return False
    return not employee_joined(db, inspection.customer_phone or "", asked_at, message_sent_at)


def record_rating(
    db: Session,
    inspection: Inspection,
    rating: Rating,
    source: str,
    sender: MessageSender,
    messages: Messages,
    settings: Settings,
    now: datetime,
    *,
    comment: str | None = None,
) -> None:
    """Store the rating; then ask the optional question (or thank the customer if a comment came with it)."""
    if not can_rate(inspection):
        return
    inspection.rating = rating
    inspection.rating_at = now
    inspection.rating_source = source
    inspection.status = InspectionStatus.ANSWERED
    inspection.answered_at = now
    if comment:
        inspection.comment = comment
        inspection.comment_at = now
        inspection.survey_stage = SurveyStage.COMPLETED
        db.commit()
        send_thanks(db, inspection, sender, messages, now)
        return
    inspection.survey_stage = SurveyStage.AWAITING_COMMENT
    db.commit()
    index, text = _pick_text(db, inspection, messages.followup_question, Inspection.followup_variant)
    try:
        sender.send_text(inspection.customer_phone or "", text)
    except WhatsAppError as exc:
        # The rating is kept; without the question there is no comment to wait for.
        inspection.survey_stage = SurveyStage.COMPLETED
        inspection.last_error = f"follow-up question not sent: {exc}"[:500]
        db.commit()
        logger.warning("follow-up question for survey %s not sent: %s", inspection.id, exc)
        return
    inspection.followup_variant = index
    inspection.followup_sent_at = now
    inspection.comment_window_closes_at = now + timedelta(minutes=settings.free_text_window_minutes)
    db.commit()


def record_comment(
    db: Session, inspection: Inspection, comment: str, sender: MessageSender, messages: Messages, now: datetime
) -> None:
    """Store the optional comment and thank the customer."""
    if inspection.survey_stage != SurveyStage.AWAITING_COMMENT:
        return
    inspection.comment = comment
    inspection.comment_at = now
    inspection.survey_stage = SurveyStage.COMPLETED
    db.commit()
    send_thanks(db, inspection, sender, messages, now)


def send_thanks(db: Session, inspection: Inspection, sender: MessageSender, messages: Messages, now: datetime) -> None:
    """Send the thank-you message once."""
    if inspection.thanks_sent_at is not None:
        return
    index, text = _pick_text(db, inspection, messages.thanks, Inspection.thanks_variant)
    try:
        sender.send_text(inspection.customer_phone or "", text)
    except WhatsAppError as exc:
        logger.warning("thank-you for survey %s not sent: %s", inspection.id, exc)
        return
    inspection.thanks_variant = index
    inspection.thanks_sent_at = now
    db.commit()
