from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.db_models import Inspection
from app.services import health, inspections
from app.timeutils import utcnow
from tests.test_extraction import JPEG
from tests.test_worker import make_worker


def test_heartbeat(db: Session) -> None:
    now = utcnow()
    assert not health.worker_alive(db, now)
    health.beat(db, now)
    assert health.last_beat(db) == now
    assert health.worker_alive(db, now + timedelta(minutes=2))
    assert not health.worker_alive(db, now + timedelta(minutes=4))


def test_worker_beats_every_tick(session_factory: sessionmaker[Session], db: Session, settings: Settings) -> None:
    make_worker(session_factory, settings).run_once()
    assert health.worker_alive(db)


def test_processing_page_warns_when_worker_is_down(
    employee_client: TestClient, db: Session, settings: Settings
) -> None:
    pending = inspections.create_from_upload(db, settings, JPEG, None)
    page = employee_client.get(f"/inspections/{pending.id}/confirm").text
    assert "no está en marcha" in page and "uv run python -m app.worker" in page
    health.beat(db)
    page = employee_client.get(f"/inspections/{pending.id}/confirm").text
    assert "no está en marcha" not in page and "Leyendo la foto" in page


def test_processing_page_shows_model_retries(employee_client: TestClient, db: Session, settings: Settings) -> None:
    pending = inspections.create_from_upload(db, settings, JPEG, None)
    health.beat(db)
    row = db.get(Inspection, pending.id)
    assert row is not None
    row.extraction_attempts = 1
    db.commit()
    page = employee_client.get(f"/inspections/{pending.id}/confirm").text
    assert "Ollama" in page and "intento 2 de 3" in page
