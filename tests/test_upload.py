import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus, InspectionStatus
from app.services import health, inspections
from app.services.storage import ImageStorage, InvalidImage, detect_image_type
from tests.conftest import csrf_from

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def test_detect_image_type() -> None:
    assert detect_image_type(JPEG) == "jpg"
    assert detect_image_type(PNG) == "png"
    assert detect_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert detect_image_type(b"%PDF-1.7") is None


def test_storage_rejects_bad_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = ImageStorage(tmp_path, max_bytes=50)
    with pytest.raises(InvalidImage):
        storage.save(b"")
    with pytest.raises(InvalidImage):
        storage.save(JPEG)  # too large
    with pytest.raises(InvalidImage):
        ImageStorage(tmp_path, 10_000).save(b"not an image")
    with pytest.raises(InvalidImage):
        storage.path("../etc/passwd")


def test_storage_save_read_delete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = ImageStorage(tmp_path / "img", max_bytes=10_000)
    name = storage.save(PNG)
    assert name.endswith(".png") and storage.read(name) == PNG
    storage.delete(name)
    assert not storage.path(name).exists()


def test_create_from_upload_queues_extraction(db: Session, settings: Settings) -> None:
    inspection = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    assert inspection.status == InspectionStatus.PENDING_CONFIRMATION
    assert inspection.extraction_status == ExtractionStatus.PENDING
    assert inspection.expires_at is not None and inspection.expires_at > inspection.created_at
    assert (settings.storage_dir / inspection.image_path).read_bytes() == JPEG


def test_replace_image_resets_extraction(db: Session, settings: Settings) -> None:
    inspection = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    old = inspection.image_path
    inspection.extraction_status = ExtractionStatus.UNREADABLE
    inspection.extraction_attempts = 3
    db.commit()
    updated = inspections.replace_image(db, settings, inspection.id, PNG)
    assert updated.image_path != old and not (settings.storage_dir / old).exists()
    assert updated.extraction_status == ExtractionStatus.PENDING and updated.extraction_attempts == 0


def test_replace_image_only_when_pending(db: Session, settings: Settings) -> None:
    inspection = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    inspection.status = InspectionStatus.SCHEDULED
    db.commit()
    with pytest.raises(inspections.InvalidTransition):
        inspections.replace_image(db, settings, inspection.id, PNG)


def test_list_by_status(db: Session, settings: Settings) -> None:
    a = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    b = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    b.status = InspectionStatus.SCHEDULED
    db.commit()
    assert [i.id for i in inspections.list_by_status(db, InspectionStatus.PENDING_CONFIRMATION)] == [a.id]
    assert len(inspections.list_by_status(db, None)) == 2


def test_upload_page_flow(employee_client: TestClient, db: Session) -> None:
    health.beat(db)  # a running worker
    page = employee_client.get("/inspections/new")
    assert 'capture="environment"' in page.text
    response = employee_client.post(
        "/inspections/new",
        data={"csrf_token": csrf_from(page.text)},
        files={"photo": ("form.jpg", JPEG, "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    inspection = db.query(Inspection).one()
    assert response.headers["location"] == f"/inspections/{inspection.id}/confirm"
    confirm = employee_client.get(response.headers["location"])
    assert "Leyendo la foto" in confirm.text
    image = employee_client.get(f"/inspections/{inspection.id}/image")
    assert image.status_code == 200 and image.content == JPEG


def test_upload_rejects_non_images(employee_client: TestClient) -> None:
    page = employee_client.get("/inspections/new")
    response = employee_client.post(
        "/inspections/new",
        data={"csrf_token": csrf_from(page.text)},
        files={"photo": ("doc.pdf", b"%PDF-1.7 ...", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Formato no admitido" in response.text


def test_upload_requires_login(client: TestClient) -> None:
    assert client.get("/inspections/new", follow_redirects=False).status_code == 303


def test_upload_form_has_progress_bar(employee_client: TestClient) -> None:
    page = employee_client.get("/inspections/new").text
    assert "data-upload" in page and "<progress" in page and 'data-max-bytes="10485760"' in page
    assert '<script src="/static/app.js"' in page
    assert employee_client.get("/static/app.js").status_code == 200


def test_status_endpoint_signature_changes(employee_client: TestClient, db: Session, settings: Settings) -> None:
    inspection = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    health.beat(db)
    page = employee_client.get(f"/inspections/{inspection.id}/confirm").text
    assert f'data-status-url="/inspections/{inspection.id}/status"' in page
    assert 'http-equiv="refresh"' not in page
    first = employee_client.get(f"/inspections/{inspection.id}/status").json()
    assert first["extraction_status"] == "pending" and first["worker_alive"] is True
    assert f'data-signature="{first["signature"]}"' in page
    inspection.extraction_status = ExtractionStatus.DONE
    db.commit()
    assert employee_client.get(f"/inspections/{inspection.id}/status").json()["signature"] != first["signature"]
    # Once finished, the page no longer polls.
    assert "data-status-url" not in employee_client.get(f"/inspections/{inspection.id}/confirm").text
