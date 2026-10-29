"""Loads the customer-facing messages file and picks message variants."""

import random
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

ButtonKind = Literal["good", "okay", "bad", "opt_out"]


class SurveyTemplate(BaseModel):
    """A Meta-approved survey template."""

    name: str
    language: str = "es"
    text: str
    buttons: list[ButtonKind]


class Messages(BaseModel):
    """Contents of the messages file."""

    survey_templates: list[SurveyTemplate] = Field(min_length=1)
    button_labels: dict[str, str]
    service_fallback: str
    followup_question: list[str] = Field(min_length=1)
    thanks: list[str] = Field(min_length=1)
    opt_out_confirmation: list[str] = Field(min_length=1)
    rating_phrases: dict[Literal["good", "okay", "bad"], list[str]]
    opt_out_phrases: list[str] = Field(min_length=1)

    @field_validator("survey_templates")
    @classmethod
    def _unique_names(cls, value: list[SurveyTemplate]) -> list[SurveyTemplate]:
        names = [t.name for t in value]
        if len(names) != len(set(names)):
            raise ValueError("template names must be unique")
        return value

    def template(self, name: str) -> SurveyTemplate | None:
        """Find a template by name."""
        return next((t for t in self.survey_templates if t.name == name), None)


@lru_cache
def load_messages(path: Path) -> Messages:
    """Read and validate the messages file (cached per path)."""
    with path.open(encoding="utf-8") as fh:
        return Messages.model_validate(yaml.safe_load(fh))


def pick_variant(options: list[str], recently_used: list[str], rng: random.Random | None = None) -> str:
    """Pick a random option not in `recently_used`, keeping at least one candidate.

    `recently_used` is ordered most recent first; older entries are dropped until a candidate remains.
    """
    rng = rng or random.Random()
    recent = list(recently_used)
    while True:
        candidates = [o for o in options if o not in recent]
        if candidates:
            return rng.choice(candidates)
        recent.pop()  # forget the oldest use and try again
