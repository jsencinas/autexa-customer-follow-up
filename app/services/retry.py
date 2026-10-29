"""Retry backoff helpers."""

from datetime import timedelta

MAX_BACKOFF = timedelta(hours=1)


def backoff(attempt: int, base_seconds: float) -> timedelta:
    """Exponential backoff for the given attempt number (1-based), capped at one hour."""
    delay = timedelta(seconds=base_seconds * (2 ** max(attempt - 1, 0)))
    return min(delay, MAX_BACKOFF)
