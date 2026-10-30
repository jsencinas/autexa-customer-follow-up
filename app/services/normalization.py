"""Normalisation of extracted or typed values (phone, name, service text, dates)."""

import re
from datetime import date, datetime

import phonenumbers

SERVICE_DISPLAY_MAX = 60


def canonical_mexico(e164: str) -> str:
    """Drop the legacy mobile "1" of Mexican numbers: +52 1 XXXXXXXXXX -> +52 XXXXXXXXXX.

    WhatsApp still identifies Mexican mobiles as 521 + 10 digits, while forms are written
    without the 1; both must map to the same number so replies match their survey.
    """
    digits = e164.lstrip("+")
    if digits.startswith("521") and len(digits) == 13:
        return "+52" + digits[3:]
    return e164


def normalize_phone(raw: str | None, default_region: str) -> str | None:
    """Return the number in E.164 format, or None if it is missing or invalid."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+"):
        cleaned = canonical_mexico("+" + "".join(ch for ch in cleaned if ch.isdigit()))
    try:
        parsed = phonenumbers.parse(cleaned, default_region.upper())
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def clean_name(raw: str | None) -> str | None:
    """Trim and collapse whitespace in a customer name."""
    if not raw:
        return None
    name = " ".join(raw.split())
    return name or None


def name_key(name: str) -> str:
    """Case-insensitive, trimmed form of a name used for duplicate detection."""
    return " ".join(name.split()).casefold()


def service_key(service: str) -> str:
    """Normalised service text used for duplicate detection."""
    return " ".join(service.split()).casefold()


def first_name(name: str) -> str:
    """First word of the customer's name, for message personalisation."""
    return name.split()[0] if name.split() else name


def service_display(raw: str | None) -> str | None:
    """Single-line, short service text that is safe as a WhatsApp template parameter.

    Template parameters cannot contain newlines, tabs or more than four consecutive spaces.
    """
    if not raw:
        return None
    text = " ".join(raw.split())
    if len(text) <= SERVICE_DISPLAY_MAX:
        return text or None
    cut = text[: SERVICE_DISPLAY_MAX - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y")


def parse_date(raw: str | None) -> date | None:
    """Parse a written date (day-first, as used in Spain). Returns None if unparseable."""
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
