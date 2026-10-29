"""Session handling, role checks and CSRF protection for the web app."""

import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.db_models import User
from app.models.enums import Role
from app.timeutils import utcnow

CSRF_FIELD = "csrf_token"


class LoginRequired(Exception):
    """Raised when a page needs a logged-in user; turned into a redirect to /login."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated user of the current request."""

    id: int
    username: str
    role: Role

    @property
    def is_admin(self) -> bool:
        """True for admins."""
        return self.role == Role.ADMIN


def csrf_token(request: Request) -> str:
    """Return the session's CSRF token, creating it if needed."""
    token = request.session.get(CSRF_FIELD)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_FIELD] = token
    return token


async def verify_csrf(request: Request) -> None:
    """Dependency for unsafe methods: the form's CSRF token must match the session."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    form = await request.form()
    sent = form.get(CSRF_FIELD)
    expected = request.session.get(CSRF_FIELD)
    if not expected or not isinstance(sent, str) or not secrets.compare_digest(sent, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token inválido")


def start_session(request: Request, user: User) -> None:
    """Store the user in a fresh session (rotating the CSRF token)."""
    now = utcnow().timestamp()
    request.session.clear()
    request.session.update(
        {"user_id": user.id, "username": user.username, "role": user.role, "login_at": now, "last_seen": now}
    )
    csrf_token(request)


def end_session(request: Request) -> None:
    """Log out."""
    request.session.clear()


def _session_expired(request: Request, role: Role, settings: Settings) -> bool:
    now = utcnow().timestamp()
    login_at = float(request.session.get("login_at", 0))
    last_seen = float(request.session.get("last_seen", 0))
    if now - login_at > timedelta(hours=settings.employee_session_hours).total_seconds():
        return True
    if role == Role.ADMIN and now - last_seen > timedelta(minutes=settings.admin_session_minutes).total_seconds():
        return True
    return False


def current_user(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> CurrentUser:
    """Dependency: the logged-in, active user. Redirects to /login otherwise."""
    user_id = request.session.get("user_id")
    if user_id is None:
        raise LoginRequired(request.url.path)
    user = db.get(User, int(user_id))
    if user is None or not user.is_active or _session_expired(request, Role(user.role), settings):
        end_session(request)
        raise LoginRequired(request.url.path)
    request.session["last_seen"] = utcnow().timestamp()
    return CurrentUser(id=user.id, username=user.username, role=Role(user.role))


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Dependency: the logged-in user must be an admin."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo para administradores")
    return user
