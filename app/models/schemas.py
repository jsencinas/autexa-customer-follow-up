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


# --- WhatsApp webhook payloads ---------------------------------------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class WaText(_Lenient):
    """Text message body."""

    body: str = ""


class WaButton(_Lenient):
    """Quick-reply button tapped on a template."""

    payload: str | None = None
    text: str | None = None


class WaButtonReply(_Lenient):
    """Reply button of an interactive message."""

    id: str
    title: str | None = None


class WaInteractive(_Lenient):
    """Interactive reply."""

    type: str
    button_reply: WaButtonReply | None = None


class WaMessage(_Lenient):
    """An incoming customer message."""

    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: WaText | None = None
    button: WaButton | None = None
    interactive: WaInteractive | None = None


class WaStatus(_Lenient):
    """Delivery status of a message the business sent."""

    id: str
    status: str
    timestamp: str
    recipient_id: str | None = None


class WaEcho(_Lenient):
    """A message sent by an employee from the WhatsApp Business app (Coexistence)."""

    from_: str = Field(alias="from")
    to: str
    id: str
    timestamp: str
    type: str | None = None


class WaMetadata(_Lenient):
    """Which business number the event belongs to."""

    phone_number_id: str | None = None


class WaChangeValue(_Lenient):
    """The `value` of a webhook change."""

    metadata: WaMetadata | None = None
    messages: list[WaMessage] = Field(default_factory=list)
    statuses: list[WaStatus] = Field(default_factory=list)
    message_echoes: list[WaEcho] = Field(default_factory=list)


class WaChange(_Lenient):
    """One change in a webhook entry."""

    field: str
    value: WaChangeValue


class WaEntry(_Lenient):
    """One webhook entry."""

    id: str | None = None
    changes: list[WaChange] = Field(default_factory=list)


class WebhookPayload(_Lenient):
    """Top-level WhatsApp webhook body."""

    object: str
    entry: list[WaEntry] = Field(default_factory=list)
