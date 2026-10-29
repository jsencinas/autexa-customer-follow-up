from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus, InspectionStatus
from app.services import inspections, scheduler
from app.services.messages import load_messages
from app.worker.runner import Worker, WorkerDeps
from tests.fakes import FakeSender, scheduled_inspection
from tests.test_extraction import JPEG, FakeExtractor, good_result

OPEN = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


def make_worker(session_factory: sessionmaker[Session], settings: Settings, sender=None, extractor=None) -> Worker:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    deps = WorkerDeps(
        session_factory=session_factory,
        settings=settings,
        messages=load_messages(settings.messages_file),
        sender=sender or FakeSender(),
        extractor=extractor or FakeExtractor([]),
        sleep=sleeps.append,
    )
    worker = Worker(deps)
    worker.sleeps = sleeps  # type: ignore[attr-defined]
    return worker


def test_worker_runs_extraction_and_sends_surveys(
    session_factory: sessionmaker[Session], db: Session, settings: Settings
) -> None:
    sender = FakeSender()
    pending = inspections.create_from_upload(db, settings, JPEG, None, now=OPEN)
    due = [scheduled_inspection(db, OPEN, phone=f"+3461234567{n}") for n in range(2)]
    worker = make_worker(session_factory, settings, sender, FakeExtractor([good_result()]))
    worker.run_once(OPEN)
    db.expire_all()
    assert db.get(Inspection, pending.id).extraction_status == ExtractionStatus.DONE  # type: ignore[union-attr]
    assert all(db.get(Inspection, i.id).status == InspectionStatus.SENT for i in due)  # type: ignore[union-attr]
    assert worker.sleeps == [1.0, 1.0]  # type: ignore[attr-defined]  # send rate respected


def test_survey_job_runs_once_per_minute(session_factory: sessionmaker[Session], db: Session, settings: Settings) -> None:
    sender = FakeSender()
    worker = make_worker(session_factory, settings, sender)
    worker.run_once(OPEN)
    scheduled_inspection(db, OPEN)
    worker.run_once(OPEN + timedelta(seconds=5))
    assert not sender.templates
    worker.run_once(OPEN + timedelta(minutes=1))
    assert len(sender.templates) == 1


def test_bad_record_does_not_stop_the_worker(
    session_factory: sessionmaker[Session], db: Session, settings: Settings, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    sender = FakeSender()
    bad = scheduled_inspection(db, OPEN, phone="+34612345670")
    good = scheduled_inspection(db, OPEN, phone="+34612345671")
    original = scheduler.process_due_survey

    def flaky(db_, inspection_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        if inspection_id == bad.id:
            raise RuntimeError("boom")
        return original(db_, inspection_id, *args, **kwargs)

    monkeypatch.setattr("app.services.scheduler.process_due_survey", flaky)
    make_worker(session_factory, settings, sender).run_once(OPEN)
    db.expire_all()
    assert db.get(Inspection, good.id).status == InspectionStatus.SENT  # type: ignore[union-attr]
    assert db.get(Inspection, bad.id).status == InspectionStatus.SCHEDULED  # type: ignore[union-attr]
