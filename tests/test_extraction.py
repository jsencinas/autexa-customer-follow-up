import io
import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.extraction_client import ExtractionClient
from app.integrations.ollama import OllamaBadResponse, OllamaClient, OllamaUnavailable
from app.models.db_models import Inspection
from app.models.enums import ExtractionStatus
from app.models.schemas import ExtractionResult
from app.services import extraction, inspections
from app.services.normalization import (
    clean_name,
    first_name,
    name_key,
    normalize_phone,
    parse_date,
    service_display,
)
from app.timeutils import utcnow

def _tiny_jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="JPEG")
    return out.getvalue()


JPEG = _tiny_jpeg()


def good_result(**overrides: Any) -> ExtractionResult:
    data: dict[str, Any] = {
        "is_inspection_form": True,
        "customer_name": {"value": "  María   García ", "confidence": "high"},
        "customer_phone": {"value": "612 345 678", "confidence": "high"},
        "service_description": {"value": "Cambio de aceite\ny filtros", "confidence": "low"},
        "date": {"value": "03/10/2026", "confidence": "high"},
        "other_fields": {"Matrícula": {"value": "1234ABC", "confidence": "high"}, "KM": {"value": None}},
    }
    data.update(overrides)
    return ExtractionResult.model_validate(data)


class FakeExtractor:
    def __init__(self, results: list[ExtractionResult | Exception]) -> None:
        self.results = results
        self.calls = 0

    def extract(self, image: bytes) -> tuple[ExtractionResult, dict[str, Any]]:
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, item.model_dump()


# --- normalization -------------------------------------------------------


def test_normalize_mexican_phone() -> None:
    assert normalize_phone("55-80-23-17-22", "MX") == "+525580231722"
    # WhatsApp's legacy 521 mobile prefix maps to the same number.
    assert normalize_phone("+52 1 55 8023 1722", "MX") == "+525580231722"
    assert normalize_phone("5580231722", "ES") is None


def test_normalize_phone() -> None:
    assert normalize_phone("612 345 678", "ES") == "+34612345678"
    assert normalize_phone("0034 612345678", "ES") == "+34612345678"
    assert normalize_phone("+44 7911 123456", "ES") == "+447911123456"
    assert normalize_phone("12345", "ES") is None
    assert normalize_phone("abc", "ES") is None
    assert normalize_phone(None, "ES") is None


def test_name_helpers() -> None:
    assert clean_name("  María   García ") == "María García"
    assert name_key(" MARÍA  garcía") == name_key("maría garcía")
    assert first_name("María García") == "María"


def test_service_display_is_template_safe() -> None:
    assert service_display("Cambio de aceite\ny\tfiltros") == "Cambio de aceite y filtros"
    long = "Revisión completa " * 10
    shown = service_display(long)
    assert shown is not None and len(shown) <= 60 and shown.endswith("…") and "\n" not in shown
    assert service_display(None) is None


def test_parse_date() -> None:
    assert parse_date("03/10/2026") == date(2026, 10, 3)
    assert parse_date("2026-10-03") == date(2026, 10, 3)
    assert parse_date("3-10-26") == date(2026, 10, 3)
    assert parse_date("ayer") is None


# --- clients -------------------------------------------------------------


def _ollama(handler) -> OllamaClient:  # type: ignore[no-untyped-def]
    return OllamaClient("http://ollama", 5, http=httpx.Client(transport=httpx.MockTransport(handler)))


COMPACT = {
    "customer_name": "Azteca",
    "customer_phone": "55-80-23-17-22",
    "service_description": None,
    "date": "09/09/26",
    "vehicle_brand": "Chevrolet",
    "vehicle_model": "Aveo",
    "plates": "GCZ999D",
    "unsure_fields": ["customer_phone"],
}


def test_ollama_client_sends_compact_schema_and_images() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(COMPACT)}})

    client = ExtractionClient(_ollama(handler), "vision-model")
    result, raw = client.extract(JPEG)
    assert result.customer_phone.value == "55-80-23-17-22" and result.customer_phone.confidence == "low"
    assert result.customer_name.confidence == "high" and result.service_description.value is None
    assert result.other_fields["vehicle_brand"].value == "Chevrolet"
    assert raw == COMPACT
    assert seen["model"] == "vision-model" and seen["stream"] is False and seen["keep_alive"] == "30m"
    assert seen["options"]["num_predict"] == 400
    assert set(seen["format"]["properties"]) == set(COMPACT)
    assert seen["messages"][1]["images"]


def test_ollama_client_error_classification() -> None:
    with pytest.raises(OllamaUnavailable):
        _ollama(lambda r: httpx.Response(503)).chat_json("m", "s", "u", {})

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaUnavailable):
        _ollama(boom).chat_json("m", "s", "u", {})
    with pytest.raises(OllamaBadResponse):
        _ollama(lambda r: httpx.Response(200, json={"message": {"content": "not json"}})).chat_json("m", "s", "u", {})


def test_extraction_client_rejects_invalid_output() -> None:
    bad = {"message": {"content": json.dumps({"customer_name": "x", "unsure_fields": "not-a-list"})}}
    client = ExtractionClient(_ollama(lambda r: httpx.Response(200, json=bad)), "m")
    with pytest.raises(OllamaBadResponse):
        client.extract(JPEG)


# --- service -------------------------------------------------------------


@pytest.fixture
def pending(db: Session, settings: Settings) -> Inspection:
    return inspections.create_from_upload(db, settings, JPEG, user_id=None)


def _run(db: Session, settings: Settings, inspection: Inspection, fake: FakeExtractor, now=None) -> Inspection:  # type: ignore[no-untyped-def]
    now = now or utcnow()
    assert extraction.claim_due_extractions(db, now) == [inspection.id]
    return extraction.process_extraction(db, inspection.id, fake, inspections.storage_for(settings), settings, now)


def test_successful_extraction_normalizes_fields(db: Session, settings: Settings, pending: Inspection) -> None:
    result = _run(db, settings, pending, FakeExtractor([good_result()]))
    assert result.extraction_status == ExtractionStatus.DONE
    assert result.customer_name == "María García"
    assert result.customer_phone == "+34612345678"
    assert result.service_display == "Cambio de aceite y filtros"
    assert result.service_date == date(2026, 10, 3)
    assert result.extra_fields == {"Matrícula": "1234ABC"}
    assert result.raw_extraction is not None
    assert result.field_confidence == {
        "customer_name": "high",
        "customer_phone": "high",
        "service_description": "low",
        "date": "high",
    }
    assert result.raw_extraction is not None


def test_invalid_phone_is_flagged(db: Session, settings: Settings, pending: Inspection) -> None:
    fake = FakeExtractor([good_result(customer_phone={"value": "61234", "confidence": "low"})])
    result = _run(db, settings, pending, fake)
    assert result.customer_phone is None and result.phone_raw == "61234"
    assert result.field_confidence is not None and result.field_confidence["customer_phone"] == "invalid"


def test_missing_date_defaults_to_upload_day(db: Session, settings: Settings, pending: Inspection) -> None:
    result = _run(db, settings, pending, FakeExtractor([good_result(date={"value": None})]))
    assert result.service_date == pending.created_at.date()
    assert result.field_confidence is not None and result.field_confidence["date"] == "missing"


def test_unreadable_photo(db: Session, settings: Settings, pending: Inspection) -> None:
    unreadable = good_result(
        customer_name={"value": None}, customer_phone={"value": None}, service_description={"value": None}
    )
    assert _run(db, settings, pending, FakeExtractor([unreadable])).extraction_status == ExtractionStatus.UNREADABLE


def test_vehicle_is_the_fallback_service_text(db: Session, settings: Settings, pending: Inspection) -> None:
    result = good_result(
        service_description={"value": None},
        other_fields={"vehicle_brand": {"value": "Chevrolet"}, "vehicle_model": {"value": "Aveo"}},
    )
    done = _run(db, settings, pending, FakeExtractor([result]))
    assert done.service_display == "tu Chevrolet Aveo" and done.service_description is None


def test_model_failure_retries_then_falls_back_to_manual(db: Session, settings: Settings, pending: Inspection) -> None:
    fake = FakeExtractor([OllamaUnavailable("down")] * settings.max_extraction_attempts)
    now = utcnow()
    result = _run(db, settings, pending, fake, now)
    assert result.extraction_status == ExtractionStatus.PENDING
    assert result.extraction_next_attempt_at is not None and result.extraction_next_attempt_at > now
    # Not due yet: nothing to claim.
    assert extraction.claim_due_extractions(db, now) == []
    for _ in range(settings.max_extraction_attempts - 1):
        now = now + timedelta(hours=2)
        result = _run(db, settings, pending, fake, now)
    assert result.extraction_status == ExtractionStatus.FAILED
    assert fake.calls == settings.max_extraction_attempts


def test_extraction_does_not_overwrite_manual_edits(db: Session, settings: Settings, pending: Inspection) -> None:
    pending.customer_name = "Typed By Hand"
    pending.edited_at = utcnow()
    db.commit()
    result = _run(db, settings, pending, FakeExtractor([good_result()]))
    assert result.customer_name == "Typed By Hand"
    assert result.extraction_status == ExtractionStatus.DONE


def test_stale_claim_is_reclaimed(db: Session, settings: Settings, pending: Inspection) -> None:
    now = utcnow()
    assert extraction.claim_due_extractions(db, now) == [pending.id]
    assert extraction.claim_due_extractions(db, now + timedelta(minutes=1)) == []
    assert extraction.claim_due_extractions(db, now + timedelta(minutes=11)) == [pending.id]
