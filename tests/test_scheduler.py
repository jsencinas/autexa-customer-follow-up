import random
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.whatsapp import WhatsAppError
from app.models.db_models import ContactActivity, Inspection, OptOut
from app.models.enums import InspectionStatus, SurveyStage
from app.services import admin_settings, inspections, scheduler, surveys
from app.services.messages import Messages, load_messages, pick_variant
from app.services.scheduler import SendOutcome
from tests.fakes import FakeSender, scheduled_inspection

# Wednesday 2026-10-14 10:00 UTC = 12:00 Madrid: inside default hours.
OPEN = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


@pytest.fixture
def messages(settings: Settings) -> Messages:
    return load_messages(settings.messages_file)


def send(db: Session, inspection: Inspection, sender: FakeSender, messages: Messages, settings: Settings, now=OPEN):  # type: ignore[no-untyped-def]
    return scheduler.process_due_survey(db, inspection.id, sender, messages, settings, now)


# --- messages & rotation ---------------------------------------------------


def test_messages_file_is_valid(messages: Messages) -> None:
    assert len(messages.survey_templates) >= 2
    assert all(t.buttons == ["good", "okay", "bad", "opt_out"] for t in messages.survey_templates)


def test_pick_variant_avoids_recent() -> None:
    rng = random.Random(1)
    for _ in range(50):
        assert pick_variant(["a", "b", "c"], ["a", "b"], rng) == "c"
    assert pick_variant(["a"], ["a"], rng) == "a"


def test_template_rotation_never_repeats_previous_variant(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    used: list[str] = []
    for n in range(8):
        inspection = scheduled_inspection(db, OPEN, service_date=None, service_description=f"servicio {n}")
        assert send(db, inspection, sender, messages, settings, OPEN + timedelta(days=7 * n)).outcome == SendOutcome.SENT
        used.append(sender.templates[-1]["name"])  # type: ignore[arg-type]
    names = [t.name for t in messages.survey_templates]
    window = len(names) - 1
    for i in range(1, len(used)):
        assert used[i] not in used[max(0, i - window) : i]


def test_template_params_and_buttons(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    inspection = scheduled_inspection(db, OPEN, service_display=None)
    send(db, inspection, sender, messages, settings)
    sent = sender.templates[0]
    assert sent["params"] == ["María", messages.service_fallback]
    assert sent["buttons"] == [f"survey:{inspection.id}:{k}" for k in ("good", "okay", "bad", "opt_out")]
    assert surveys.parse_button_payload(f"survey:{inspection.id}:bad") == (inspection.id, "bad")
    assert surveys.parse_button_payload("other:1:bad") is None


# --- sending ---------------------------------------------------------------


def test_sends_due_survey_once(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    inspection = scheduled_inspection(db, OPEN - timedelta(minutes=1))
    assert scheduler.due_survey_ids(db, OPEN) == [inspection.id]
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.SENT
    db.refresh(inspection)
    assert inspection.status == InspectionStatus.SENT and inspection.sent_at == OPEN
    assert inspection.wa_message_id == "wamid.1" and inspection.survey_stage == SurveyStage.AWAITING_RATING
    # Running again (e.g. after a restart) never sends a second time.
    assert scheduler.due_survey_ids(db, OPEN + timedelta(hours=1)) == []
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.SKIPPED
    assert len(sender.templates) == 1


def test_not_due_yet(db: Session, settings: Settings) -> None:
    scheduled_inspection(db, OPEN + timedelta(minutes=5))
    assert scheduler.due_survey_ids(db, OPEN) == []


def test_outside_hours_moves_to_next_opening(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    late = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)  # Saturday 19:00 Madrid
    inspection = scheduled_inspection(db, late)
    assert send(db, inspection, sender, messages, settings, late).outcome == SendOutcome.POSTPONED
    db.refresh(inspection)
    assert inspection.scheduled_at == datetime(2026, 10, 19, 7, 0, tzinfo=UTC)  # Monday 09:00 Madrid
    assert not sender.templates


def test_changed_hours_apply_to_already_scheduled_surveys(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    inspection = scheduled_inspection(db, OPEN - timedelta(hours=1))
    hours = admin_settings.get_sending_hours(db, settings)
    hours.days["wed"] = admin_settings.DayHours(enabled=True, start=time(16), end=time(18))
    admin_settings.update_sending_hours(db, hours, None)
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.POSTPONED
    db.refresh(inspection)
    assert inspection.scheduled_at == datetime(2026, 10, 14, 14, 0, tzinfo=UTC)  # 16:00 Madrid


def test_quiet_period_postpones_survey(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender()
    inspection = scheduled_inspection(db, OPEN)
    db.add(ContactActivity(phone="+34612345678", last_employee_message_at=OPEN - timedelta(minutes=30)))
    db.commit()
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.POSTPONED
    db.refresh(inspection)
    assert inspection.scheduled_at == OPEN + timedelta(minutes=settings.survey_quiet_period_minutes - 30)
    assert send(db, inspection, sender, messages, settings, inspection.scheduled_at).outcome == SendOutcome.SENT


def test_opted_out_number_is_never_messaged(db: Session, messages: Messages, settings: Settings) -> None:
    """Even a survey scheduled before the opt-out is not sent."""
    sender = FakeSender()
    inspection = scheduled_inspection(db, OPEN)
    db.add(OptOut(phone="+34612345678", source="interpreted"))
    db.commit()
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.OPTED_OUT
    db.refresh(inspection)
    assert inspection.status == InspectionStatus.OPTED_OUT and not sender.templates


def test_transient_error_retries_with_backoff_then_fails(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender(errors=[WhatsAppError("busy", retryable=True)] * settings.max_send_attempts)
    inspection = scheduled_inspection(db, OPEN)
    now = OPEN
    assert send(db, inspection, sender, messages, settings, now).outcome == SendOutcome.RETRY
    db.refresh(inspection)
    assert inspection.scheduled_at == now + timedelta(seconds=settings.retry_base_seconds)
    for _ in range(settings.max_send_attempts - 1):
        now = inspection.scheduled_at
        result = send(db, inspection, sender, messages, settings, now)
        db.refresh(inspection)
    assert result.outcome == SendOutcome.FAILED
    assert inspection.status == InspectionStatus.FAILED and inspection.dedup_key is None


def test_ambiguous_error_is_not_retried(db: Session, messages: Messages, settings: Settings) -> None:
    sender = FakeSender(errors=[WhatsAppError("timeout", ambiguous=True)])
    inspection = scheduled_inspection(db, OPEN)
    assert send(db, inspection, sender, messages, settings).outcome == SendOutcome.FAILED
    db.refresh(inspection)
    assert "avoid a duplicate" in (inspection.last_error or "")


def test_crash_mid_send_is_failed_not_resent(db: Session, messages: Messages, settings: Settings) -> None:
    inspection = scheduled_inspection(db, OPEN, send_claimed_at=OPEN)
    assert scheduler.due_survey_ids(db, OPEN + timedelta(minutes=1)) == []
    assert scheduler.recover_stale_sends(db, OPEN + timedelta(minutes=5)) == 0
    assert scheduler.recover_stale_sends(db, OPEN + timedelta(minutes=11)) == 1
    db.refresh(inspection)
    assert inspection.status == InspectionStatus.FAILED


def test_expire_unconfirmed(db: Session, settings: Settings) -> None:
    pending = Inspection(expires_at=OPEN, dedup_key="k")
    fresh = Inspection(expires_at=OPEN + timedelta(hours=1))
    db.add_all([pending, fresh])
    db.commit()
    assert scheduler.expire_unconfirmed(db, OPEN) == 1
    db.refresh(pending)
    db.refresh(fresh)
    assert pending.status == InspectionStatus.EXPIRED and pending.dedup_key is None
    assert fresh.status == InspectionStatus.PENDING_CONFIRMATION


def test_purge_old_images(db: Session, settings: Settings) -> None:
    old = inspections.create_from_upload(db, settings, b"\xff\xd8\xff" + b"0" * 10, None, now=OPEN - timedelta(days=91))
    new = inspections.create_from_upload(db, settings, b"\xff\xd8\xff" + b"1" * 10, None, now=OPEN - timedelta(days=10))
    old_path = settings.storage_dir / old.image_path  # type: ignore[operator]
    storage = inspections.storage_for(settings)
    assert scheduler.purge_old_images(db, storage, 90, OPEN) == 1
    db.refresh(old)
    db.refresh(new)
    assert old.image_path is None and old.image_deleted_at == OPEN and not old_path.exists()
    assert new.image_path is not None
