"""Logging setup and helpers that keep personal data out of INFO logs."""

import logging


def configure_logging(level: str) -> None:
    """Configure root logging once with a compact format."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for logs, keeping only the prefix and the last three digits."""
    if not phone:
        return "<none>"
    if len(phone) <= 6:
        return "*" * len(phone)
    return f"{phone[:3]}{'*' * (len(phone) - 6)}{phone[-3:]}"
