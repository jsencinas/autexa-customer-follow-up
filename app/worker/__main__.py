"""Run the worker: `uv run python -m app.worker`."""

from app.config import get_settings
from app.db import init_db
from app.integrations.email_client import EmailClient
from app.integrations.extraction_client import ExtractionClient
from app.integrations.llm_client import ReplyInterpreterClient
from app.integrations.ollama import OllamaClient
from app.integrations.whatsapp import WhatsAppClient
from app.logging_utils import configure_logging
from app.services.messages import load_messages
from app.worker.runner import Worker, WorkerDeps


def main() -> None:
    """Build the real clients and run the worker loop."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ollama = OllamaClient(settings.ollama_url, settings.ollama_timeout_seconds)
    deps = WorkerDeps(
        session_factory=init_db(settings.database_url),
        settings=settings,
        messages=load_messages(settings.messages_file),
        sender=WhatsAppClient(
            settings.whatsapp_access_token,
            settings.whatsapp_phone_number_id,
            settings.whatsapp_api_version,
            settings.whatsapp_api_base_url,
        ),
        extractor=ExtractionClient(ollama, settings.extraction_model),
        interpreter=ReplyInterpreterClient(ollama, settings.reply_model),
        mailer=EmailClient(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password,
            settings.smtp_from,
            settings.smtp_starttls,
        ),
    )
    Worker(deps).run_forever()


if __name__ == "__main__":
    main()
