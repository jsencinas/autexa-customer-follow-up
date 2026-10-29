"""Building and sending the survey messages."""

import random
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import Inspection
from app.services.messages import Messages, SurveyTemplate, pick_variant
from app.services.normalization import first_name


class MessageSender(Protocol):
    """The subset of the WhatsApp client the services use (mockable in tests)."""

    def send_template(
        self, to: str, template_name: str, language: str, body_params: list[str], button_payloads: list[str]
    ) -> str:
        """Send a template; return the message id."""
        ...

    def send_text(self, to: str, text: str) -> str:
        """Send a free-form text; return the message id."""
        ...


def button_payload(inspection_id: int, kind: str) -> str:
    """Payload attached to a quick-reply button, e.g. `survey:12:good`."""
    return f"survey:{inspection_id}:{kind}"


def parse_button_payload(payload: str | None) -> tuple[int, str] | None:
    """Inverse of `button_payload`; None for anything else."""
    if not payload:
        return None
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "survey" or not parts[1].isdigit():
        return None
    if parts[2] not in {"good", "okay", "bad", "opt_out"}:
        return None
    return int(parts[1]), parts[2]


def recent_template_names(db: Session, phone: str, limit: int, exclude_id: int | None = None) -> list[str]:
    """Templates used in this number's most recent surveys, most recent first."""
    if limit <= 0:
        return []
    query = (
        select(Inspection.template_name)
        .where(Inspection.customer_phone == phone, Inspection.template_name.is_not(None))
        .order_by(Inspection.sent_at.desc().nulls_last(), Inspection.id.desc())
        .limit(limit)
    )
    if exclude_id is not None:
        query = query.where(Inspection.id != exclude_id)
    return [name for name in db.scalars(query) if name]


def select_template(
    db: Session, inspection: Inspection, messages: Messages, rng: random.Random | None = None
) -> SurveyTemplate:
    """Pick a random template not used in this number's last N surveys (N = templates − 1)."""
    names = [t.name for t in messages.survey_templates]
    recent = recent_template_names(db, inspection.customer_phone or "", len(names) - 1, exclude_id=inspection.id)
    chosen = pick_variant(names, recent, rng)
    template = messages.template(chosen)
    assert template is not None
    return template


def template_params(inspection: Inspection, messages: Messages) -> list[str]:
    """Body parameters: customer first name and the customer-facing service text."""
    return [first_name(inspection.customer_name or ""), inspection.service_display or messages.service_fallback]


def send_survey_template(
    sender: MessageSender, inspection: Inspection, template: SurveyTemplate, messages: Messages
) -> str:
    """Send the survey template for an inspection; return the WhatsApp message id."""
    assert inspection.customer_phone
    return sender.send_template(
        inspection.customer_phone,
        template.name,
        template.language,
        template_params(inspection, messages),
        [button_payload(inspection.id, kind) for kind in template.buttons],
    )
