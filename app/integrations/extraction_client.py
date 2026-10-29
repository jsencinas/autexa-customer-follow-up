"""Client for the local vision model that reads inspection forms."""

from typing import Any

from pydantic import ValidationError

from app.integrations.ollama import OllamaBadResponse, OllamaClient
from app.models.schemas import ExtractionResult, extraction_json_schema

SYSTEM_PROMPT = """You read photos of a printed vehicle/service "initial inspection" form that is partly
handwritten (Spanish). Return ONLY the JSON requested.

Rules:
- For each field return {"value": ..., "confidence": "high" | "low"}.
- If you cannot read a field with certainty, return "value": null. NEVER guess, especially phone digits:
  a single wrong digit would message a stranger. If any phone digit is unclear, return null.
- Use "low" confidence when the handwriting is hard to read but you still give a value.
- Copy text as written; do not translate or invent.
- "date" is the date written on the form (any format as written).
- Put any other labelled fields on the form in "other_fields" keyed by their label.
- Set "is_inspection_form" to false if the photo is not such a form (or is too blurry, dark or cut off to read)."""

USER_PROMPT = "Extract customer_name, customer_phone, service_description, date and other_fields from this form."


class ExtractionClient:
    """Sends a photo to the extraction model and returns validated, nullable fields."""

    def __init__(self, ollama: OllamaClient, model: str) -> None:
        self.ollama = ollama
        self.model = model

    def extract(self, image: bytes) -> tuple[ExtractionResult, dict[str, Any]]:
        """Return the validated result and the raw model output (for debugging)."""
        raw = self.ollama.chat_json(self.model, SYSTEM_PROMPT, USER_PROMPT, extraction_json_schema(), images=[image])
        try:
            return ExtractionResult.model_validate(raw), raw
        except ValidationError as exc:
            raise OllamaBadResponse(f"extraction output failed validation: {exc.error_count()} errors") from exc
