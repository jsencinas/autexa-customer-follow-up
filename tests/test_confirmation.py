from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.ollama import OllamaUnavailable
from app.models.db_models import Inspection, OptOut
from app.models.enums import ExtractionStatus, InspectionStatus
from app.services import confirmation, extraction, health, inspections
from app.services.confirmation import ReviewForm
from app.timeutils import utcnow
from tests.conftest import csrf_from
from tests.test_extraction import JPEG, FakeExtractor, good_result

VALID = ReviewForm(
    customer_name="María García",
    customer_phone="612 345 678",
    service_description="Cambio de aceite",
    service_display="",
    service_date="03/10/2026",
)


@pytest.fixture
def pending(db: Session, settings: Settings) -> Inspection:
    return inspections.create_from_upload(db, settings, JPEG, user_id=None)


def test_confirm_blocked_without_name_or_phone(db: Session, settings: Settings, pending: Inspection) -> None:
    result = confirmation.confirm(
        db, pending.id, VALID.model_copy(update={"customer_name": "", "customer_phone": ""}), settings, None
    )
    assert not result.confirmed
    assert set(result.errors) == {"customer_name", "customer_phone"}
    assert result.inspection.status == InspectionStatus.PENDING_CONFIRMATION


def test_confirm_blocked_with_invalid_phone(db: Session, settings: Settings, pending: Inspection) -> None:
    result = confirmation.confirm(db, pending.id, VALID.model_copy(update={"customer_phone": "6123"}), settings, None)
    assert not result.confirmed and "customer_phone" in result.errors
    assert result.inspection.phone_raw == "6123"  # kept so the form shows what was typed


def test_confirm_schedules_survey(db: Session, settings: Settings, pending: Inspection) -> None:
    now = utcnow()
    result = confirmation.confirm(db, pending.id, VALID, settings, user_id=None, now=now)
    i = result.inspection
    assert result.confirmed and i.status == InspectionStatus.SCHEDULED
    assert i.confirmed_at == now and i.scheduled_at == now + timedelta(hours=settings.survey_delay_hours)
    assert i.customer_phone == "+34612345678" and i.service_display == "Cambio de aceite"
    assert i.service_date == date(2026, 10, 3) and i.dedup_key


def test_confirm_opted_out_customer(db: Session, settings: Settings, pending: Inspection) -> None:
    db.add(OptOut(phone="+34612345678", source="admin"))
    db.commit()
    result = confirmation.confirm(db, pending.id, VALID, settings, None)
    assert result.opted_out and result.inspection.status == InspectionStatus.OPTED_OUT
    assert result.inspection.scheduled_at is None and result.inspection.dedup_key is None


def test_cannot_confirm_twice_or_after_expiry(db: Session, settings: Settings, pending: Inspection) -> None:
    confirmation.confirm(db, pending.id, VALID, settings, None)
    with pytest.raises(inspections.InvalidTransition):
        confirmation.confirm(db, pending.id, VALID, settings, None)
    other = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    with pytest.raises(inspections.InvalidTransition):
        confirmation.confirm(db, other.id, VALID, settings, None, now=utcnow() + timedelta(days=2))


def test_edits_that_create_a_duplicate_discard_the_record(db: Session, settings: Settings) -> None:
    first = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    confirmation.confirm(db, first.id, VALID, settings, None)
    second = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    result = confirmation.confirm(db, second.id, VALID.model_copy(update={"customer_name": " maría GARCÍA"}), settings, None)
    assert not result.confirmed and result.duplicate_of is not None and result.duplicate_of.id == first.id
    assert result.inspection.status == InspectionStatus.DISCARDED


def test_manual_entry_works_when_model_failed(db: Session, settings: Settings, pending: Inspection) -> None:
    fake = FakeExtractor([OllamaUnavailable("down")] * settings.max_extraction_attempts)
    now = utcnow()
    storage = inspections.storage_for(settings)
    for _ in range(settings.max_extraction_attempts):
        now += timedelta(hours=2)
        for inspection_id in extraction.claim_due_extractions(db, now):
            extraction.process_extraction(db, inspection_id, fake, storage, settings, now)
    db.refresh(pending)
    assert pending.extraction_status == ExtractionStatus.FAILED
    result = confirmation.confirm(db, pending.id, VALID, settings, None)
    assert result.confirmed and result.inspection.status == InspectionStatus.SCHEDULED


def test_manual_entry_while_processing_stops_extraction(db: Session, settings: Settings, pending: Inspection) -> None:
    confirmation.save_edits(db, pending.id, VALID, settings)
    db.refresh(pending)
    assert pending.extraction_status == ExtractionStatus.FAILED
    assert extraction.claim_due_extractions(db) == []


def test_discard(db: Session, settings: Settings, pending: Inspection) -> None:
    assert confirmation.discard(db, pending.id).status == InspectionStatus.DISCARDED
    with pytest.raises(inspections.InvalidTransition):
        confirmation.discard(db, pending.id)


# --- pages ---------------------------------------------------------------


def _extract(db: Session, settings: Settings, result=None) -> None:  # type: ignore[no-untyped-def]
    fake = FakeExtractor([result or good_result()])
    for inspection_id in extraction.claim_due_extractions(db):
        extraction.process_extraction(db, inspection_id, fake, inspections.storage_for(settings), settings)


def test_review_page_shows_fields_and_confirms(
    employee_client: TestClient, db: Session, settings: Settings, pending: Inspection
) -> None:
    _extract(db, settings)
    page = employee_client.get(f"/inspections/{pending.id}/confirm")
    assert "María García" in page.text and "+34612345678" in page.text
    assert "field-warn" in page.text  # service was read with low confidence
    response = employee_client.post(
        f"/inspections/{pending.id}/confirm",
        data={**VALID.model_dump(), "action": "confirm", "csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(pending)
    assert pending.status == InspectionStatus.SCHEDULED
    assert "Encuesta programada" in employee_client.get(f"/inspections/{pending.id}/confirm").text


def test_review_page_rejects_missing_phone(
    employee_client: TestClient, db: Session, settings: Settings, pending: Inspection
) -> None:
    _extract(db, settings)
    page = employee_client.get(f"/inspections/{pending.id}/confirm")
    response = employee_client.post(
        f"/inspections/{pending.id}/confirm",
        data={**VALID.model_dump(), "customer_phone": "", "action": "confirm", "csrf_token": csrf_from(page.text)},
    )
    assert response.status_code == 400 and "El teléfono es obligatorio" in response.text


def test_unreadable_page_offers_retake_and_manual(
    employee_client: TestClient, db: Session, settings: Settings, pending: Inspection
) -> None:
    _extract(db, settings, good_result(is_inspection_form=False))
    page = employee_client.get(f"/inspections/{pending.id}/confirm").text
    assert "No hemos podido leer esta foto" in page
    assert "/retake" in page and 'id="review"' in page


def test_processing_page_offers_manual_entry(employee_client: TestClient, db: Session, pending: Inspection) -> None:
    health.beat(db)  # a running worker
    page = employee_client.get(f"/inspections/{pending.id}/confirm").text
    assert "Leyendo la foto" in page and "manual=1" in page and 'id="review"' not in page
    manual = employee_client.get(f"/inspections/{pending.id}/confirm?manual=1").text
    assert 'id="review"' in manual


def test_duplicate_page_mentions_original(
    employee_client: TestClient, db: Session, settings: Settings
) -> None:
    first = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    second = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    fake = FakeExtractor([good_result(), good_result()])
    for inspection_id in extraction.claim_due_extractions(db):
        extraction.process_extraction(db, inspection_id, fake, inspections.storage_for(settings), settings)
    page = employee_client.get(f"/inspections/{second.id}/confirm").text
    assert "ya estaba registrada" in page and f"#{first.id}" in page and "Pendiente de confirmar" in page
