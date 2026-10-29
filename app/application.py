"""FastAPI application factory."""

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.db import init_db
from app.logging_utils import configure_logging
from app.web import admin_routes, auth_routes, inspection_routes
from app.web.security import LoginRequired

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app: configure logging, the database and all routers."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_db(settings.database_url)

    app = FastAPI(title="Autexa customer follow-up", docs_url=None, redoc_url=None, openapi_url=None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        session_cookie="session",
        max_age=int(settings.employee_session_hours * 3600),
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_required(_request: Request, exc: LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/inspections/new", status_code=303)

    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(inspection_routes.router)
    return app
