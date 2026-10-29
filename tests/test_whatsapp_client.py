import hashlib
import hmac
import json

import httpx
import pytest

from app.integrations.whatsapp import WhatsAppClient, WhatsAppError, verify_signature


def client(handler) -> WhatsAppClient:  # type: ignore[no-untyped-def]
    return WhatsAppClient("tok", "999", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_send_template_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    message_id = client(handler).send_template("+34612345678", "tpl", "es", ["María", "Cambio"], ["p0", "p1"])
    assert message_id == "wamid.1"
    assert seen["url"].endswith("/v21.0/999/messages") and seen["auth"] == "Bearer tok"  # type: ignore[union-attr]
    body = seen["body"]
    assert body["to"] == "34612345678" and body["type"] == "template"  # type: ignore[index]
    components = body["template"]["components"]  # type: ignore[index]
    assert components[0]["parameters"][1]["text"] == "Cambio"
    assert components[2] == {
        "type": "button",
        "sub_type": "quick_reply",
        "index": "1",
        "parameters": [{"type": "payload", "payload": "p1"}],
    }


def test_send_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["text"]["body"] == "hola"
        return httpx.Response(200, json={"messages": [{"id": "wamid.2"}]})

    assert client(handler).send_text("+34612345678", "hola") == "wamid.2"


@pytest.mark.parametrize(
    "response,retryable,ambiguous",
    [
        (httpx.Response(429, json={"error": {"code": 130429}}), True, False),
        (httpx.Response(503), True, False),
        (httpx.Response(500, json={"error": {"code": 1}}), False, True),
        (httpx.Response(400, json={"error": {"code": 131026}}), False, False),
        (httpx.Response(400, json={"error": {"code": 131048}}), True, False),
    ],
)
def test_error_classification(response: httpx.Response, retryable: bool, ambiguous: bool) -> None:
    with pytest.raises(WhatsAppError) as info:
        client(lambda r: response).send_text("+34612345678", "x")
    assert info.value.retryable is retryable and info.value.ambiguous is ambiguous


def test_connect_error_is_retryable_but_read_timeout_is_ambiguous() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(WhatsAppError) as info:
        client(refuse).send_text("+34600000000", "x")
    assert info.value.retryable and not info.value.ambiguous
    with pytest.raises(WhatsAppError) as info:
        client(slow).send_text("+34600000000", "x")
    assert info.value.ambiguous and not info.value.retryable


def test_verify_signature() -> None:
    body = b'{"x":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body + b" ", good)
    assert not verify_signature("secret", body, None)
    assert not verify_signature("", body, good)
