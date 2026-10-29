"""Pydantic schemas for validating external input (model output, webhooks, forms)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["high", "low"]


class ExtractedField(BaseModel):
    """One field read from the photo; value is None when the model could not read it."""

    model_config = ConfigDict(extra="ignore")

    value: str | None = None
    confidence: Confidence = "low"

    @field_validator("value", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class ExtractionResult(BaseModel):
    """Validated output of the extraction model."""

    model_config = ConfigDict(extra="ignore")

    is_inspection_form: bool = True
    customer_name: ExtractedField = Field(default_factory=ExtractedField)
    customer_phone: ExtractedField = Field(default_factory=ExtractedField)
    service_description: ExtractedField = Field(default_factory=ExtractedField)
    date: ExtractedField = Field(default_factory=ExtractedField)
    other_fields: dict[str, ExtractedField] = Field(default_factory=dict)


def extraction_json_schema() -> dict[str, object]:
    """JSON schema handed to Ollama's `format` parameter for extraction."""
    field = {
        "type": "object",
        "properties": {
            "value": {"type": ["string", "null"]},
            "confidence": {"type": "string", "enum": ["high", "low"]},
        },
        "required": ["value", "confidence"],
    }
    return {
        "type": "object",
        "properties": {
            "is_inspection_form": {"type": "boolean"},
            "customer_name": field,
            "customer_phone": field,
            "service_description": field,
            "date": field,
            "other_fields": {"type": "object", "additionalProperties": field},
        },
        "required": ["is_inspection_form", "customer_name", "customer_phone", "service_description", "date"],
    }
