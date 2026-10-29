"""Run the worker: `uv run python -m app.worker`."""

from app.config import get_settings
from app.db import init_db
from app.integrations.extraction_client import ExtractionClient
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
    )
    Worker(deps).run_forever()


if __name__ == "__main__":
    main()
