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
    _extract(db, settings, good_result(customer_name={"value": None}, customer_phone={"value": None}, service_description={"value": None}))
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


# --- editing after confirmation ---------------------------------------------------


@pytest.fixture
def scheduled(db: Session, settings: Settings, pending: Inspection) -> Inspection:
    return confirmation.confirm(db, pending.id, VALID, settings, None).inspection


def test_scheduled_inspection_can_be_edited(db: Session, settings: Settings, scheduled: Inspection) -> None:
    when = scheduled.scheduled_at
    result = confirmation.save_edits(
        db, scheduled.id, VALID.model_copy(update={"customer_name": "María López", "customer_phone": "699 111 222"}), settings
    )
    assert not result.errors
    db.refresh(scheduled)
    assert scheduled.customer_name == "María López" and scheduled.customer_phone == "+34699111222"
    assert scheduled.status == InspectionStatus.SCHEDULED and scheduled.scheduled_at == when
    assert scheduled.dedup_key  # recomputed for the new data


def test_invalid_edit_of_scheduled_keeps_old_data(db: Session, settings: Settings, scheduled: Inspection) -> None:
    result = confirmation.save_edits(db, scheduled.id, VALID.model_copy(update={"customer_phone": "123"}), settings)
    assert "customer_phone" in result.errors
    db.refresh(scheduled)
    assert scheduled.customer_phone == "+34612345678"  # a scheduled survey never loses its valid phone


def test_edit_that_matches_another_inspection_is_rejected(db: Session, settings: Settings, scheduled: Inspection) -> None:
    other = inspections.create_from_upload(db, settings, JPEG, user_id=None)
    other_form = VALID.model_copy(update={"customer_name": "Pedro"})
    confirmation.confirm(db, other.id, other_form, settings, None)
    result = confirmation.save_edits(db, scheduled.id, other_form, settings)
    assert "form" in result.errors and f"#{other.id}" in result.errors["form"]
    db.refresh(scheduled)
    assert scheduled.customer_name == "María García" and scheduled.status == InspectionStatus.SCHEDULED


def test_edit_to_opted_out_number_cancels_survey(db: Session, settings: Settings, scheduled: Inspection) -> None:
    db.add(OptOut(phone="+34699111222", source="admin"))
    db.commit()
    result = confirmation.save_edits(db, scheduled.id, VALID.model_copy(update={"customer_phone": "699111222"}), settings)
    assert result.opted_out
    db.refresh(scheduled)
    assert scheduled.status == InspectionStatus.OPTED_OUT and scheduled.dedup_key is None


def test_cannot_edit_while_sending_or_after_sent(db: Session, settings: Settings, scheduled: Inspection) -> None:
    scheduled.send_claimed_at = utcnow()  # the worker is sending it right now
    db.commit()
    with pytest.raises(inspections.InvalidTransition):
        confirmation.save_edits(db, scheduled.id, VALID, settings)
    with pytest.raises(inspections.InvalidTransition):
        confirmation.discard(db, scheduled.id)
    scheduled.send_claimed_at = None
    scheduled.status = InspectionStatus.SENT
    db.commit()
    with pytest.raises(inspections.InvalidTransition):
        confirmation.save_edits(db, scheduled.id, VALID, settings)


def test_cancel_scheduled_survey(db: Session, settings: Settings, scheduled: Inspection) -> None:
    cancelled = confirmation.discard(db, scheduled.id)
    assert cancelled.status == InspectionStatus.DISCARDED and cancelled.dedup_key is None


def test_review_page_for_scheduled_offers_edit_and_cancel(
    employee_client: TestClient, db: Session, scheduled: Inspection
) -> None:
    page = employee_client.get(f"/inspections/{scheduled.id}/confirm").text
    assert 'id="review"' in page and "Cancelar encuesta" in page and 'value="confirm"' not in page
    response = employee_client.post(
        f"/inspections/{scheduled.id}/confirm",
        data={**VALID.model_dump(), "customer_name": "Otro Nombre", "action": "save", "csrf_token": csrf_from(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(scheduled)
    assert scheduled.customer_name == "Otro Nombre"
    bad = employee_client.post(
        f"/inspections/{scheduled.id}/confirm",
        data={**VALID.model_dump(), "customer_phone": "12", "customer_name": "Typed", "action": "save",
              "csrf_token": csrf_from(page)},
    )
    assert bad.status_code == 400 and 'value="Typed"' in bad.text  # what was typed is kept on screen


def test_sent_inspection_is_read_only(employee_client: TestClient, db: Session, scheduled: Inspection) -> None:
    scheduled.status = InspectionStatus.SENT
    scheduled.sent_at = utcnow()
    db.commit()
    page = employee_client.get(f"/inspections/{scheduled.id}/confirm").text
    assert 'id="review"' not in page and "Ya no se puede modificar" in page


# --- status window ------------------------------------------------------------------


def test_status_window_lists_all_with_counts_and_search(
    employee_client: TestClient, db: Session, settings: Settings, scheduled: Inspection
) -> None:
    inspections.create_from_upload(db, settings, JPEG, user_id=None)
    page = employee_client.get("/inspections").text
    assert "Estado de inspecciones" in page and "María García" in page
    assert "Encuesta programada <span class=\"count\">1</span>" in page
    assert "Pendiente de confirmar <span class=\"count\">1</span>" in page
    only_scheduled = employee_client.get("/inspections", params={"status": "scheduled"}).text
    assert only_scheduled.count("/confirm\">#") == 1
    assert "María" in employee_client.get("/inspections", params={"q": "345678"}).text
    assert "No hay inspecciones" in employee_client.get("/inspections", params={"q": "zzz"}).text
    assert employee_client.get("/inspections", params={"status": "bogus"}).status_code == 400


def test_status_window_live_signature_changes(
    employee_client: TestClient, db: Session, settings: Settings, scheduled: Inspection
) -> None:
    page = employee_client.get("/inspections").text
    assert 'data-status-url="/inspections/live"' in page
    before = employee_client.get("/inspections/live").json()["signature"]
    assert f'data-signature="{before}"' in page
    inspections.create_from_upload(db, settings, JPEG, user_id=None)
    assert employee_client.get("/inspections/live").json()["signature"] != before
