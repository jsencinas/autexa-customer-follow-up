"""Login and logout pages."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.services import auth
from app.web.security import end_session, start_session, verify_csrf
from app.web.templating import render, safe_next

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    """Show the login form."""
    return render(request, "login.html", {"next": safe_next(next)})


@router.post("/login", dependencies=[Depends(verify_csrf)], response_model=None)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Check credentials and start a session."""
    ip = request.client.host if request.client else "unknown"
    try:
        user = auth.authenticate(
            db,
            username,
            password,
            ip,
            max_attempts=settings.login_max_attempts,
            lockout_minutes=settings.login_lockout_minutes,
        )
    except auth.LoginLocked:
        error = "Demasiados intentos fallidos. Inténtalo de nuevo más tarde."
        return render(request, "login.html", {"error": error, "next": safe_next(next)}, status_code=429)
    except auth.InvalidCredentials:
        error = "Usuario o contraseña incorrectos."
        return render(request, "login.html", {"error": error, "next": safe_next(next)}, status_code=401)
    start_session(request, user)
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request) -> RedirectResponse:
    """End the session."""
    end_session(request)
    return RedirectResponse("/login", status_code=303)
