"""Shared dependencies for web routes (overridable in tests)."""

from fastapi import Depends

from app.config import Settings, get_settings
from app.integrations.whatsapp import WhatsAppClient
from app.services.messages import Messages, load_messages
from app.services.surveys import MessageSender


def get_messages(settings: Settings = Depends(get_settings)) -> Messages:
    """The customer-facing messages file."""
    return load_messages(settings.messages_file)


def get_sender(settings: Settings = Depends(get_settings)) -> MessageSender:
    """WhatsApp client for messages triggered from the admin section (e.g. opt-out confirmations)."""
    return WhatsAppClient(
        settings.whatsapp_access_token,
        settings.whatsapp_phone_number_id,
        settings.whatsapp_api_version,
        settings.whatsapp_api_base_url,
    )
