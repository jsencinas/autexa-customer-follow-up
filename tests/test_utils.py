import pytest
from app.services.utils import normalize_phone_number


class TestNormalizePhoneNumber:
    """Tests for E.164 phone number normalization."""

    def test_ten_digit_local_number(self):
        """A 10-digit number gets the default country code prepended."""
        result = normalize_phone_number("6141234567")
        assert result == "+526141234567"

    def test_number_with_country_code(self):
        """A number already including country code digits is kept as-is."""
        result = normalize_phone_number("526141234567")
        assert result == "+526141234567"

    def test_strips_non_numeric_characters(self):
        """Dashes, spaces, and parentheses are removed."""
        result = normalize_phone_number("(614) 123-4567")
        assert result == "+526141234567"

    def test_number_with_plus_prefix(self):
        """Leading '+' is handled correctly."""
        result = normalize_phone_number("+526141234567")
        assert result == "+526141234567"

    def test_empty_string_returns_none(self):
        result = normalize_phone_number("")
        assert result is None

    def test_none_returns_none(self):
        result = normalize_phone_number(None)
        assert result is None

    def test_garbage_text_returns_none(self):
        result = normalize_phone_number("not a number")
        assert result is None
