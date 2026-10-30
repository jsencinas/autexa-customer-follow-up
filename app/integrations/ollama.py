"""Minimal client for the Ollama chat API with schema-constrained JSON output."""

import base64
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Any failure talking to Ollama."""


class OllamaUnavailable(OllamaError):
    """Transient failure (connection, timeout, 5xx): retry later."""


class OllamaBadResponse(OllamaError):
    """Ollama answered, but not with usable JSON."""


class OllamaClient:
    """Calls `POST /api/chat` and returns the parsed JSON content of the reply."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        http: httpx.Client | None = None,
        keep_alive: str = "30m",
        max_output_tokens: int = 400,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)
        # Keeping the model in memory avoids reloading several GB for every photo.
        self.keep_alive = keep_alive
        # Output tokens dominate the time on CPU; a cap stops a runaway answer from blocking the worker.
        self.max_output_tokens = max_output_tokens

    def chat_json(
        self,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> dict[str, Any]:
        """Send one system + user turn and return the JSON object the model produced."""
        message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            message["images"] = [base64.b64encode(img).decode("ascii") for img in images]
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, message],
            "format": schema,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_predict": self.max_output_tokens},
        }
        try:
            response = self.http.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise OllamaUnavailable(f"cannot reach Ollama: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise OllamaUnavailable(f"Ollama returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise OllamaBadResponse(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            content = response.json()["message"]["content"]
            data = json.loads(content)
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaBadResponse("Ollama reply is not valid JSON") from exc
        if not isinstance(data, dict):
            raise OllamaBadResponse("Ollama reply is not a JSON object")
        return data
