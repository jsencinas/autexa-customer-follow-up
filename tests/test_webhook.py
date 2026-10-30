import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.db_models import ContactActivity, EmployeeMessage, InboundMessage, Inspection
from app.models.enums import InboundStatus, InspectionStatus, SurveyStage
from app.models.schemas import WebhookPayload
from app.services import inbound

TS = int(datetime(2026, 10, 14, 10, 0, tzinfo=UTC).timestamp())


def sign(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(value: dict[str, Any], field: str = "messages") -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": field, "value": {"metadata": {"phone_number_id": "123"}, **value}}]}],
    }


def text_message(msg_id: str = "wamid.in1", body: str = "Bien, gracias", ts: int = TS) -> dict[str, Any]:
    return payload({"messages": [{"from": "34612345678", "id": msg_id, "timestamp": str(ts), "type": "text", "text": {"body": body}}]})


def post(client: TestClient, data: dict[str, Any], secret: str = "app-secret"):  # type: ignore[no-untyped-def]
    body = json.dumps(data).encode()
    return client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body, secret), "Content-Type": "application/json"})


def test_verification_handshake(client: TestClient) -> None:
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_rejects_bad_signature(client: TestClient, db: Session) -> None:
    assert post(client, text_message(), secret="wrong").status_code == 403
    assert db.query(InboundMessage).count() == 0


def test_stores_text_message_and_acks(client: TestClient, db: Session) -> None:
    response = post(client, text_message())
    assert response.status_code == 200
    message = db.query(InboundMessage).one()
    assert message.from_phone == "+34612345678" and message.text == "Bien, gracias"
    assert message.status == InboundStatus.PENDING and message.sent_at == datetime.fromtimestamp(TS, UTC)
    assert db.get(ContactActivity, "+34612345678").last_customer_message_at == message.sent_at  # type: ignore[union-attr]


def test_repeated_delivery_is_stored_once(client: TestClient, db: Session) -> None:
    post(client, text_message())
    post(client, text_message())
    assert db.query(InboundMessage).count() == 1


def test_button_reply_payload(client: TestClient, db: Session) -> None:
    msg = {"from": "34612345678", "id": "wamid.b", "timestamp": str(TS), "type": "button",
           "button": {"payload": "survey:7:good", "text": "Bien"}}
    post(client, payload({"messages": [msg]}))
    stored = db.query(InboundMessage).one()
    assert stored.button_payload == "survey:7:good" and stored.text == "Bien"


def test_echo_records_employee_activity_only(client: TestClient, db: Session) -> None:
    echo = {"from": "34900000000", "to": "34612345678", "id": "wamid.e1", "timestamp": str(TS), "type": "text",
            "text": {"body": "Le paso el presupuesto"}}
    assert post(client, payload({"message_echoes": [echo]}, field="smb_message_echoes")).status_code == 200
    stored = db.query(EmployeeMessage).one()
    assert stored.phone == "+34612345678" and not hasattr(stored, "text")
    assert db.query(InboundMessage).count() == 0
    assert db.get(ContactActivity, "+34612345678").last_employee_message_at is not None  # type: ignore[union-attr]


def test_unparseable_payload_is_acknowledged(client: TestClient) -> None:
    assert post(client, {"unexpected": True}).json() == {"status": "ignored"}


def test_other_phone_number_ids_are_ignored(db: Session) -> None:
    data = text_message()
    data["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "999"
    assert inbound.store_webhook(db, WebhookPayload.model_validate(data), "123").messages == 0


def test_failed_delivery_marks_survey_failed(db: Session) -> None:
    inspection = Inspection(status=InspectionStatus.SENT, survey_stage=SurveyStage.AWAITING_RATING,
                            wa_message_id="wamid.out", dedup_key="k", customer_phone="+34612345678")
    db.add(inspection)
    db.commit()
    data = payload({"statuses": [{"id": "wamid.out", "status": "failed", "timestamp": str(TS), "recipient_id": "34612345678"}]})
    inbound.store_webhook(db, WebhookPayload.model_validate(data), "123")
    db.refresh(inspection)
    assert inspection.status == InspectionStatus.FAILED and inspection.dedup_key is None


def test_delivered_status_changes_nothing(db: Session) -> None:
    inspection = Inspection(status=InspectionStatus.SENT, survey_stage=SurveyStage.AWAITING_RATING, wa_message_id="wamid.out")
    db.add(inspection)
    db.commit()
    data = payload({"statuses": [{"id": "wamid.out", "status": "delivered", "timestamp": str(TS)}]})
    inbound.store_webhook(db, WebhookPayload.model_validate(data), "123")
    db.refresh(inspection)
    assert inspection.status == InspectionStatus.SENT


def test_to_e164() -> None:
    assert inbound.to_e164("34612345678") == "+34612345678"
    assert inbound.to_e164("5215580231722") == "+525580231722"  # Mexican mobiles arrive as 521...
    assert inbound._timestamp("bad", datetime(2026, 1, 1, tzinfo=UTC)) == datetime(2026, 1, 1, tzinfo=UTC)
