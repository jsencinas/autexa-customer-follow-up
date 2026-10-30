"""Background worker: polls the database for due work. Safe to restart at any time."""

import logging
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.services import duplicates, extraction, health, inspections, reply_interpretation, reports, scheduler
from app.services.reports import Mailer
from app.services.extraction import Extractor
from app.services.messages import Messages
from app.services.reply_interpretation import ReplyInterpreter
from app.services.scheduler import SendOutcome
from app.services.surveys import MessageSender
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

SURVEY_INTERVAL = timedelta(minutes=1)
RETENTION_INTERVAL = timedelta(hours=1)


@dataclass
class WorkerDeps:
    """External clients and configuration used by the worker (fakes in tests)."""

    session_factory: sessionmaker[Session]
    settings: Settings
    messages: Messages
    sender: MessageSender
    extractor: Extractor
    interpreter: ReplyInterpreter
    mailer: Mailer
    sleep: Callable[[float], None] = time_module.sleep


@dataclass
class Worker:
    """Runs each job on its own interval; a failing job or record never stops the loop."""

    deps: WorkerDeps
    _last_run: dict[str, datetime] = field(default_factory=dict)

    def _due(self, name: str, interval: timedelta, now: datetime) -> bool:
        last = self._last_run.get(name)
        if last is not None and now - last < interval:
            return False
        self._last_run[name] = now
        return True

    def _job(self, name: str, fn: Callable[[Session, datetime], None], now: datetime) -> None:
        with self.deps.session_factory() as db:
            try:
                fn(db, now)
            except Exception:
                db.rollback()
                logger.exception("worker job %s failed", name)

    def run_once(self, now: datetime | None = None) -> None:
        """Run every job that is due at `now`."""
        now = now or utcnow()
        self._job("heartbeat", health.beat, now)
        self._job("extraction", self.run_extractions, now)
        self._job("replies", self.interpret_replies, now)
        if self._due("surveys", SURVEY_INTERVAL, now):
            self._job("recover_sends", self.recover_sends, now)
            self._job("surveys", self.send_due_surveys, now)
            self._job("expire", self.expire_unconfirmed, now)
            self._job("monthly_report", self.send_monthly_report, now)
        if self._due("retention", RETENTION_INTERVAL, now):
            self._job("retention", self.purge_images, now)

    def run_extractions(self, db: Session, now: datetime) -> None:
        """Read the photos of newly uploaded inspections."""
        storage = inspections.storage_for(self.deps.settings)
        for inspection_id in extraction.claim_due_extractions(db, now):
            try:
                extraction.process_extraction(db, inspection_id, self.deps.extractor, storage, self.deps.settings, now)
            except Exception:
                db.rollback()
                logger.exception("extraction of inspection %s crashed", inspection_id)

    def interpret_replies(self, db: Session, now: datetime) -> None:
        """Interpret stored customer messages."""
        for message_id in reply_interpretation.claim_due_messages(db, now):
            try:
                reply_interpretation.process_message(
                    db, message_id, self.deps.interpreter, self.deps.sender, self.deps.messages, self.deps.settings, now
                )
            except Exception:
                db.rollback()
                logger.exception("interpreting message %s crashed", message_id)

    def send_due_surveys(self, db: Session, now: datetime) -> None:
        """Send due surveys, respecting the configured send rate."""
        pause = 1.0 / self.deps.settings.send_rate_per_second if self.deps.settings.send_rate_per_second > 0 else 0
        for inspection_id in scheduler.due_survey_ids(db, now):
            try:
                result = scheduler.process_due_survey(
                    db, inspection_id, self.deps.sender, self.deps.messages, self.deps.settings, now
                )
            except Exception:
                db.rollback()
                logger.exception("sending survey %s crashed", inspection_id)
                continue
            if result.outcome == SendOutcome.SENT and pause:
                self.deps.sleep(pause)

    def recover_sends(self, db: Session, now: datetime) -> None:
        """Fail sends interrupted by a crash (never resend them)."""
        if count := scheduler.recover_stale_sends(db, now):
            logger.warning("%s interrupted sends marked failed", count)

    def expire_unconfirmed(self, db: Session, now: datetime) -> None:
        """Expire inspections nobody confirmed in time."""
        scheduler.expire_unconfirmed(db, now)

    def send_monthly_report(self, db: Session, now: datetime) -> None:
        """Send the monthly report when due (and any manual resends)."""
        reports.run_monthly_report(db, self.deps.mailer, self.deps.settings, now)

    def purge_images(self, db: Session, now: datetime) -> None:
        """Apply the photo retention period."""
        storage = inspections.storage_for(self.deps.settings)
        scheduler.purge_old_images(db, storage, self.deps.settings.image_retention_days, now)

    def run_forever(self) -> None:
        """Main loop."""
        logger.info("worker started")
        self._job("backfill_dedup_keys", lambda db, _now: duplicates.backfill_keys(db) and None, utcnow())
        while True:
            self.run_once()
            self.deps.sleep(self.deps.settings.worker_tick_seconds)
