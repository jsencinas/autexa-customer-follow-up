"""Sending due surveys, expiring unconfirmed inspections and purging old photos.

Sending is idempotent: an inspection is claimed atomically before the send, and a send whose
outcome is unknown (crash, timeout) is marked `failed` instead of being retried, so a survey is
never sent twice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.whatsapp import WhatsAppError
from app.logging_utils import mask_phone
from app.models.db_models import ContactActivity, Inspection
from app.models.enums import InspectionStatus, SurveyStage
from app.services import admin_settings, duplicates, opt_out, sending_window, surveys
from app.services.messages import Messages
from app.services.retry import backoff
from app.services.storage import ImageStorage
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

STALE_SEND_CLAIM = timedelta(minutes=10)
NO_WINDOW_RECHECK = timedelta(hours=1)


class SendOutcome(StrEnum):
    """What happened to a due survey."""

    SENT = "sent"
    POSTPONED = "postponed"
    OPTED_OUT = "opted_out"
    RETRY = "retry"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SendResult:
    """Outcome for one inspection."""

    inspection_id: int
    outcome: SendOutcome


def due_survey_ids(db: Session, now: datetime, limit: int = 50) -> list[int]:
    """Scheduled, unclaimed surveys whose time has come."""
    query = (
        select(Inspection.id)
        .where(
            Inspection.status == InspectionStatus.SCHEDULED,
            Inspection.scheduled_at <= now,
            Inspection.send_claimed_at.is_(None),
        )
        .order_by(Inspection.scheduled_at)
        .limit(limit)
    )
    return list(db.scalars(query))


def _mark_failed(inspection: Inspection, error: str) -> None:
    inspection.status = InspectionStatus.FAILED
    inspection.last_error = error[:500]
    inspection.send_claimed_at = None
    duplicates.release(inspection)


def _quiet_until(db: Session, phone: str, settings: Settings) -> datetime | None:
    """End of the quiet period after the latest conversation activity with this number, if any."""
    activity = db.get(ContactActivity, phone)
    if activity is None:
        return None
    stamps = [t for t in (activity.last_customer_message_at, activity.last_employee_message_at) if t]
    if not stamps:
        return None
    return max(stamps) + timedelta(minutes=settings.survey_quiet_period_minutes)


def process_due_survey(
    db: Session,
    inspection_id: int,
    sender: surveys.MessageSender,
    messages: Messages,
    settings: Settings,
    now: datetime | None = None,
) -> SendResult:
    """Check opt-out, sending window and quiet period, then send one survey exactly once."""
    now = now or utcnow()
    inspection = db.get(Inspection, inspection_id)
    if inspection is None or inspection.status != InspectionStatus.SCHEDULED or inspection.send_claimed_at:
        return SendResult(inspection_id, SendOutcome.SKIPPED)
    phone = inspection.customer_phone
    if not phone:
        _mark_failed(inspection, "no phone number")
        db.commit()
        return SendResult(inspection_id, SendOutcome.FAILED)

    # 1. Opt-out is re-checked right before every send.
    if opt_out.is_opted_out(db, phone):
        inspection.status = InspectionStatus.OPTED_OUT
        duplicates.release(inspection)
        db.commit()
        return SendResult(inspection_id, SendOutcome.OPTED_OUT)

    # 2. Sending window, using the current admin settings.
    hours = admin_settings.get_sending_hours(db, settings)
    opening = sending_window.next_opening(now, hours)
    if opening is None:
        logger.warning("no sending day is enabled; survey %s postponed", inspection.id)
        inspection.scheduled_at = now + NO_WINDOW_RECHECK
        db.commit()
        return SendResult(inspection_id, SendOutcome.POSTPONED)
    if opening > now:
        inspection.scheduled_at = opening
        db.commit()
        return SendResult(inspection_id, SendOutcome.POSTPONED)

    # 3. Quiet period: do not interrupt a live conversation.
    quiet_until = _quiet_until(db, phone, settings)
    if quiet_until and quiet_until > now:
        inspection.scheduled_at = sending_window.next_opening(quiet_until, hours) or quiet_until
        db.commit()
        return SendResult(inspection_id, SendOutcome.POSTPONED)

    # 4. Claim atomically so concurrent workers cannot both send.
    claimed = db.execute(
        update(Inspection)
        .where(
            Inspection.id == inspection.id,
            Inspection.status == InspectionStatus.SCHEDULED,
            Inspection.send_claimed_at.is_(None),
        )
        .values(send_claimed_at=now)
    )
    db.commit()
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        return SendResult(inspection_id, SendOutcome.SKIPPED)
    db.refresh(inspection)

    template = surveys.select_template(db, inspection, messages)
    inspection.send_attempts += 1
    try:
        message_id = surveys.send_survey_template(sender, inspection, template, messages)
    except WhatsAppError as exc:
        if exc.retryable and inspection.send_attempts < settings.max_send_attempts:
            inspection.send_claimed_at = None
            inspection.last_error = str(exc)[:500]
            inspection.scheduled_at = now + backoff(inspection.send_attempts, settings.retry_base_seconds)
            db.commit()
            logger.info("survey %s send failed, retry scheduled: %s", inspection.id, exc)
            return SendResult(inspection_id, SendOutcome.RETRY)
        reason = f"ambiguous send, not retried to avoid a duplicate: {exc}" if exc.ambiguous else str(exc)
        _mark_failed(inspection, reason)
        db.commit()
        logger.warning("survey %s failed: %s", inspection.id, reason)
        return SendResult(inspection_id, SendOutcome.FAILED)

    inspection.status = InspectionStatus.SENT
    inspection.sent_at = now
    inspection.wa_message_id = message_id
    inspection.template_name = template.name
    inspection.survey_stage = SurveyStage.AWAITING_RATING
    inspection.send_claimed_at = None
    inspection.last_error = None
    db.commit()
    logger.info("survey %s sent to %s with %s", inspection.id, mask_phone(phone), template.name)
    return SendResult(inspection_id, SendOutcome.SENT)


def recover_stale_sends(db: Session, now: datetime | None = None) -> int:
    """Claims left behind by a crash mid-send: the outcome is unknown, so mark them failed."""
    now = now or utcnow()
    stale = list(
        db.scalars(
            select(Inspection).where(
                Inspection.status == InspectionStatus.SCHEDULED,
                Inspection.send_claimed_at.is_not(None),
                Inspection.send_claimed_at < now - STALE_SEND_CLAIM,
            )
        )
    )
    for inspection in stale:
        _mark_failed(inspection, "worker stopped during send; not retried to avoid a duplicate")
    db.commit()
    return len(stale)


def expire_unconfirmed(db: Session, now: datetime | None = None) -> int:
    """Mark pending inspections past their confirmation deadline as `expired`."""
    now = now or utcnow()
    rows = list(
        db.scalars(
            select(Inspection).where(
                Inspection.status == InspectionStatus.PENDING_CONFIRMATION,
                Inspection.expires_at <= now,
            )
        )
    )
    for inspection in rows:
        inspection.status = InspectionStatus.EXPIRED
        duplicates.release(inspection)
    db.commit()
    return len(rows)


def purge_old_images(
    db: Session, storage: ImageStorage, retention_days: int, now: datetime | None = None
) -> int:
    """Delete photos older than the retention period (the inspection record is kept)."""
    now = now or utcnow()
    cutoff = now - timedelta(days=retention_days)
    rows = list(
        db.scalars(
            select(Inspection).where(Inspection.image_path.is_not(None), Inspection.image_uploaded_at < cutoff)
        )
    )
    for inspection in rows:
        assert inspection.image_path
        storage.delete(inspection.image_path)
        inspection.image_path = None
        inspection.image_deleted_at = now
    db.commit()
    return len(rows)
