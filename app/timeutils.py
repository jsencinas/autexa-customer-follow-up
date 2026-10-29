"""Time helpers. All datetimes in the app are timezone-aware UTC unless stated otherwise."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
