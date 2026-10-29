"""Deterministic interpretation of the obvious replies (buttons, plain ratings, clear opt-outs)."""

import re
import unicodedata
from dataclasses import dataclass, field

from app.models.enums import InterpretationSource, Rating
from app.services.messages import Messages
from app.services.surveys import parse_button_payload


@dataclass
class Interpretation:
    """What an inbound message means."""

    intents: set[str]
    source: InterpretationSource
    confidence: float
    rating: Rating | None = None
    comment: str | None = None
    survey_id: int | None = None
    from_button: bool = False
    model: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces (emoji are kept)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    no_punct = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in no_accents)
    return " ".join(no_punct.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def interpret_by_rules(text: str | None, button_payload: str | None, messages: Messages) -> Interpretation | None:
    """Return an interpretation for obvious cases, or None to defer to the LLM."""
    button = parse_button_payload(button_payload)
    if button is not None:
        survey_id, kind = button
        if kind == "opt_out":
            return Interpretation({"opt_out"}, InterpretationSource.RULE, 1.0, survey_id=survey_id, from_button=True)
        return Interpretation(
            {"rating"}, InterpretationSource.RULE, 1.0, rating=Rating(kind), survey_id=survey_id, from_button=True
        )
    if not text:
        return None
    norm = normalize_text(text)
    if any(_contains_phrase(norm, normalize_text(p)) for p in messages.opt_out_phrases):
        return Interpretation({"opt_out"}, InterpretationSource.RULE, 1.0)
    for rating, phrases in messages.rating_phrases.items():
        if norm in {normalize_text(p) for p in phrases}:
            return Interpretation({"rating"}, InterpretationSource.RULE, 1.0, rating=Rating(rating))
    return None
