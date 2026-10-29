"""FastAPI application factory."""

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import init_db
from app.logging_utils import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app: configure logging, the database and all routers."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_db(settings.database_url)

    app = FastAPI(title="Autexa customer follow-up", docs_url=None, redoc_url=None)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
