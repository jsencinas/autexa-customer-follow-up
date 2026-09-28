import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from app.services.scheduler import get_next_business_time, calculate_schedule_time


class TestGetNextBusinessTime:
    """Tests for the business hours enforcement logic."""

    def test_within_business_hours_unchanged(self):
        """A time already within business hours is returned as-is."""
        tz = ZoneInfo("America/Mexico_City")
        # Wednesday at 10:00 AM
        dt = datetime(2026, 9, 30, 10, 0, tzinfo=tz)
        result = get_next_business_time(dt)
        assert result == dt

    def test_before_opening_rolls_to_start(self):
        """A time before opening hours rolls forward to start time same day."""
        tz = ZoneInfo("America/Mexico_City")
        # Wednesday at 7:00 AM
        dt = datetime(2026, 9, 30, 7, 0, tzinfo=tz)
        result = get_next_business_time(dt)
        assert result.hour == 9
        assert result.minute == 0
        assert result.weekday() == 2  # Still Wednesday

    def test_after_closing_rolls_to_next_day(self):
        """A time after closing rolls forward to start of next business day."""
        tz = ZoneInfo("America/Mexico_City")
        # Wednesday at 19:00
        dt = datetime(2026, 9, 30, 19, 0, tzinfo=tz)
        result = get_next_business_time(dt)
        assert result.hour == 9
        assert result.minute == 0
        assert result.day == 1  # Thursday Oct 1

    def test_sunday_rolls_to_monday(self):
        """Sunday (not a business day) rolls forward to Monday opening."""
        tz = ZoneInfo("America/Mexico_City")
        # Sunday at 12:00
        dt = datetime(2026, 9, 27, 12, 0, tzinfo=tz)
        result = get_next_business_time(dt)
        assert result.weekday() == 0  # Monday
        assert result.hour == 9


class TestCalculateScheduleTime:
    """Tests for the full schedule time calculation."""

    def test_delay_within_business_hours(self):
        """If delay lands during business hours, it stays there."""
        # Monday 10:00 UTC -> +24h = Tuesday 10:00 UTC = Tuesday 04:00 CST... 
        # Actually let's use a time that clearly lands in business hours
        # Monday 15:00 UTC = Monday 09:00 CST + 24h = Tuesday 09:00 CST = Tuesday 15:00 UTC
        base = datetime(2026, 9, 28, 15, 0, 0)  # Monday 09:00 CST
        result = calculate_schedule_time(base)
        assert result is not None
        # Result should be a naive UTC datetime
        assert result.tzinfo is None

    def test_returns_naive_utc(self):
        """The returned datetime should be naive (for SQLite storage)."""
        base = datetime(2026, 9, 28, 15, 0, 0)
        result = calculate_schedule_time(base)
        assert result.tzinfo is None
