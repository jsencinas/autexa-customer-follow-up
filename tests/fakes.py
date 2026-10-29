"""Test doubles for the external clients."""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.integrations.whatsapp import WhatsAppError
from app.models.db_models import Inspection
from app.models.enums import InspectionStatus


@dataclass
class FakeSender:
    """Records sent messages; can be told to fail."""

    templates: list[dict[str, object]] = field(default_factory=list)
    texts: list[tuple[str, str]] = field(default_factory=list)
    errors: list[WhatsAppError] = field(default_factory=list)
    counter: int = 0

    def _next_id(self) -> str:
        self.counter += 1
        return f"wamid.{self.counter}"

    def send_template(
        self, to: str, template_name: str, language: str, body_params: list[str], button_payloads: list[str]
    ) -> str:
        if self.errors:
            raise self.errors.pop(0)
        self.templates.append(
            {"to": to, "name": template_name, "language": language, "params": body_params, "buttons": button_payloads}
        )
        return self._next_id()

    def send_text(self, to: str, text: str) -> str:
        if self.errors:
            raise self.errors.pop(0)
        self.texts.append((to, text))
        return self._next_id()


def scheduled_inspection(db: Session, scheduled_at, phone: str = "+34612345678", **fields) -> Inspection:  # type: ignore[no-untyped-def]
    """A confirmed inspection waiting to be sent."""
    values: dict[str, object] = {
        "status": InspectionStatus.SCHEDULED,
        "customer_name": "María García",
        "customer_phone": phone,
        "service_description": "Cambio de aceite",
        "service_display": "Cambio de aceite",
        "service_date": date(2026, 10, 3),
        "scheduled_at": scheduled_at,
        "confirmed_at": scheduled_at,
    }
    values.update(fields)
    inspection = Inspection(**values)
    db.add(inspection)
    db.commit()
    return inspection
