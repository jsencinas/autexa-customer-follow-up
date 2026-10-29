"""Jinja2 templates with the helpers every page needs."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.web.security import CurrentUser, csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    user: CurrentUser | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render a template with the CSRF token and current user available."""
    ctx = {"csrf_token": csrf_token(request), "user": user, **(context or {})}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def safe_next(url: str | None) -> str:
    """Only allow local redirect targets."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return "/"


STATUS_LABELS: dict[str, str] = {
    "pending_confirmation": "Pendiente de confirmar",
    "scheduled": "Encuesta programada",
    "sent": "Encuesta enviada",
    "answered": "Respondida",
    "expired": "Caducada",
    "failed": "Error de envío",
    "discarded": "Descartada",
    "opted_out": "Cliente dado de baja",
}

RATING_LABELS: dict[str, str] = {"good": "Bien", "okay": "Regular", "bad": "Mal"}

templates.env.filters["status_label"] = lambda s: STATUS_LABELS.get(str(s), str(s))
templates.env.filters["rating_label"] = lambda r: RATING_LABELS.get(str(r), "—") if r else "—"
