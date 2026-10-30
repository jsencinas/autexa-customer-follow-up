"""Client for the local vision model that reads inspection forms."""

from typing import Any

from pydantic import ValidationError

from app.integrations.ollama import OllamaBadResponse, OllamaClient
from app.models.schemas import CompactExtraction, ExtractionResult, extraction_json_schema

SYSTEM_PROMPT = """You read photos of the Autexa car-service "ORDEN DE SERVICIO" form (Mexico, Spanish),
partly handwritten. Return ONLY the JSON requested, with these fields:

- customer_name: handwritten after "NOMBRE" in the first customer box (not "DATOS DE FACTURACION").
- customer_phone: handwritten after "TEL" in the same box. Digits only as written.
- service_description: handwritten lines under "SERVICIOS SOLICITADO POR EL CLIENTE". null if empty.
- date: handwritten after "FECHA RECEPCION" at the top (not the date near the signatures).
- vehicle_brand: after "MARCA"; vehicle_model: after "TIPO (MODELO)"; plates: after "PLACAS".
- unsure_fields: names of the fields whose handwriting you are not sure about.

Rules:
- If a field is empty or you cannot read it, use null. NEVER guess, especially phone digits:
  one wrong digit would message a stranger. If any phone digit is unclear, use null.
- Copy text as written; do not translate or invent.
- If the photo is not this form, or is too blurry, dark or cut off to read, return null for everything."""

USER_PROMPT = "Read the form."


class ExtractionClient:
    """Sends a photo to the extraction model and returns validated, nullable fields."""

    def __init__(self, ollama: OllamaClient, model: str) -> None:
        self.ollama = ollama
        self.model = model

    def extract(self, image: bytes) -> tuple[ExtractionResult, dict[str, Any]]:
        """Return the validated result and the raw model output (for debugging)."""
        raw = self.ollama.chat_json(self.model, SYSTEM_PROMPT, USER_PROMPT, extraction_json_schema(), images=[image])
        try:
            return CompactExtraction.model_validate(raw).to_result(), raw
        except ValidationError as exc:
            raise OllamaBadResponse(f"extraction output failed validation: {exc.error_count()} errors") from exc
