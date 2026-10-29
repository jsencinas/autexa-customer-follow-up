"""WhatsApp Cloud API client (sending messages, signature verification)."""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.logging_utils import mask_phone

logger = logging.getLogger(__name__)

# Meta error codes that mean "try again later".
_RETRYABLE_CODES = {4, 80007, 130429, 131016, 131048, 131056, 133004}


class WhatsAppError(Exception):
    """A send failed.

    retryable: safe to try again later (the message was certainly not sent).
    ambiguous: the request may have reached Meta; retrying could send the message twice.
    """

    def __init__(self, message: str, *, retryable: bool = False, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's `X-Hub-Signature-256` header against the raw request body."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class WhatsAppClient:
    """Sends template and text messages from the business number."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v21.0",
        base_url: str = "https://graph.facebook.com",
        http: httpx.Client | None = None,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}/messages"
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        body_params: list[str],
        button_payloads: list[str],
    ) -> str:
        """Send an approved template with body parameters and quick-reply payloads; return the message id."""
        components: list[dict[str, Any]] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]}
        ]
        for index, payload in enumerate(button_payloads):
            components.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(index),
                    "parameters": [{"type": "payload", "payload": payload}],
                }
            )
        return self._send(
            to,
            {"type": "template", "template": {"name": template_name, "language": {"code": language}, "components": components}},
        )

    def send_text(self, to: str, text: str) -> str:
        """Send a free-form text (only valid inside the 24h customer service window)."""
        return self._send(to, {"type": "text", "text": {"preview_url": False, "body": text}})

    def _send(self, to: str, body: dict[str, Any]) -> str:
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"), **body}
        try:
            response = self.http.post(self.url, json=payload, headers=self.headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise WhatsAppError(f"connection failed: {type(exc).__name__}", retryable=True) from exc
        except httpx.TransportError as exc:
            # The request may have been delivered (e.g. read timeout): never retry automatically.
            raise WhatsAppError(f"uncertain delivery: {type(exc).__name__}", ambiguous=True) from exc

        if response.status_code == 200:
            try:
                message_id = str(response.json()["messages"][0]["id"])
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise WhatsAppError("unexpected success response", ambiguous=True) from exc
            logger.info("whatsapp %s sent to %s", body["type"], mask_phone(to))
            return message_id

        code = None
        try:
            code = response.json().get("error", {}).get("code")
        except ValueError:
            pass
        retryable = response.status_code in (429, 503) or code in _RETRYABLE_CODES
        # Other 5xx answers do not prove the message was rejected, so they are not retried.
        ambiguous = response.status_code >= 500 and not retryable
        raise WhatsAppError(f"HTTP {response.status_code} (code {code})", retryable=retryable, ambiguous=ambiguous)
