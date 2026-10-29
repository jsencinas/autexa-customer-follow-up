"""Admin-editable settings (sending hours, monthly report) stored in the database, with an audit log.

`.env` values only seed the defaults until an admin saves the settings for the first time.
"""

import re
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import WEEKDAYS, Settings
from app.models.db_models import AuditLog, Setting

SENDING_HOURS_KEY = "sending_hours"
REPORT_KEY = "monthly_report"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DayHours(BaseModel):
    """Sending window for one weekday (local business time)."""

    enabled: bool = False
    start: time = time(9, 0)
    end: time = time(18, 0)

    @model_validator(mode="after")
    def _start_before_end(self) -> "DayHours":
        if self.enabled and self.start >= self.end:
            raise ValueError("la hora de inicio debe ser anterior a la de fin")
        return self


class SendingHours(BaseModel):
    """Weekly sending windows plus the timezone they are expressed in."""

    timezone: str
    days: dict[str, DayHours]

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"zona horaria desconocida: {value}") from exc
        return value

    @field_validator("days")
    @classmethod
    def _all_weekdays(cls, value: dict[str, DayHours]) -> dict[str, DayHours]:
        unknown = set(value) - set(WEEKDAYS)
        if unknown:
            raise ValueError(f"días desconocidos: {sorted(unknown)}")
        return {day: value.get(day, DayHours()) for day in WEEKDAYS}

    @property
    def tz(self) -> ZoneInfo:
        """The timezone object."""
        return ZoneInfo(self.timezone)


class ReportSettings(BaseModel):
    """Monthly report recipients and send day/time (local business time)."""

    recipients: list[str] = Field(default_factory=list)
    day: int = Field(default=1, ge=1, le=28)
    send_time: time = time(8, 0)

    @field_validator("recipients")
    @classmethod
    def _valid_emails(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip().lower() for v in value if v.strip()]
        bad = [v for v in cleaned if not _EMAIL_RE.match(v)]
        if bad:
            raise ValueError(f"correos no válidos: {', '.join(bad)}")
        return list(dict.fromkeys(cleaned))


def default_sending_hours(settings: Settings) -> SendingHours:
    """Sending hours built from the `.env` defaults."""
    days = {
        day: DayHours(
            enabled=day in settings.sending_days,
            start=settings.default_sending_start,
            end=settings.default_sending_end,
        )
        for day in WEEKDAYS
    }
    return SendingHours(timezone=settings.business_timezone, days=days)


def default_report_settings(settings: Settings) -> ReportSettings:
    """Report settings built from the `.env` defaults."""
    return ReportSettings(day=settings.default_report_day, send_time=settings.default_report_time)


def _get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    return None if row is None else row.value


def record_audit(
    db: Session,
    user_id: int | None,
    action: str,
    target: str | None,
    old: Any,
    new: Any,
    reason: str | None = None,
) -> None:
    """Add an audit log entry (the caller commits)."""
    db.add(AuditLog(user_id=user_id, action=action, target=target, old_value=old, new_value=new, reason=reason))


def _put(db: Session, key: str, value: dict[str, Any], user_id: int | None) -> None:
    row = db.get(Setting, key)
    old = None if row is None else row.value
    if row is None:
        db.add(Setting(key=key, value=value, updated_by_id=user_id))
    else:
        row.value = value
        row.updated_by_id = user_id
    record_audit(db, user_id, "update_setting", key, old, value)
    db.commit()


def get_sending_hours(db: Session, settings: Settings) -> SendingHours:
    """Current sending hours (database value, or the `.env` defaults)."""
    stored = _get(db, SENDING_HOURS_KEY)
    return SendingHours.model_validate(stored) if stored else default_sending_hours(settings)


def update_sending_hours(db: Session, hours: SendingHours, user_id: int | None) -> None:
    """Save new sending hours and log the change."""
    _put(db, SENDING_HOURS_KEY, hours.model_dump(mode="json"), user_id)


def get_report_settings(db: Session, settings: Settings) -> ReportSettings:
    """Current monthly report settings."""
    stored = _get(db, REPORT_KEY)
    return ReportSettings.model_validate(stored) if stored else default_report_settings(settings)


def update_report_settings(db: Session, report: ReportSettings, user_id: int | None) -> None:
    """Save new report settings and log the change."""
    _put(db, REPORT_KEY, report.model_dump(mode="json"), user_id)


def audit_entries(db: Session, limit: int = 50) -> list[AuditLog]:
    """Most recent audit log entries."""
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)))
