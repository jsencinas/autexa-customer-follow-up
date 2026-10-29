"""Admin section routes (admin role only)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.enums import Role
from app.services import admin_settings, auth
from app.web.security import CurrentUser, require_admin, verify_csrf
from app.web.templating import render

router = APIRouter(prefix="/admin")


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)
) -> HTMLResponse:
    """List accounts and show the create form."""
    return render(request, "admin/users.html", {"users": auth.list_users(db)}, user=user)


@router.post("/users", dependencies=[Depends(verify_csrf)], response_model=None)
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: Role = Form(Role.EMPLOYEE),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Create an employee (or admin) account."""
    try:
        auth.create_user(db, username, password, role)
    except auth.UserError as exc:
        ctx = {"users": auth.list_users(db), "error": str(exc)}
        return render(request, "admin/users.html", ctx, user=user, status_code=400)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/active", dependencies=[Depends(verify_csrf)])
def toggle_user(
    user_id: int,
    active: bool = Form(...),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Enable or disable an account (admins cannot disable themselves)."""
    if user_id != user.id:
        auth.set_user_active(db, user_id, active)
    return RedirectResponse("/admin/users", status_code=303)


DAY_LABELS = {
    "mon": "Lunes",
    "tue": "Martes",
    "wed": "Miércoles",
    "thu": "Jueves",
    "fri": "Viernes",
    "sat": "Sábado",
    "sun": "Domingo",
}


def _settings_context(db: Session, settings: Settings, **extra: object) -> dict[str, object]:
    return {
        "hours": admin_settings.get_sending_hours(db, settings),
        "report": admin_settings.get_report_settings(db, settings),
        "day_labels": DAY_LABELS,
        "audit": admin_settings.audit_entries(db, 20),
        "usernames": {u.id: u.username for u in auth.list_users(db)},
        **extra,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Sending hours and monthly report settings."""
    return render(request, "admin/settings.html", _settings_context(db, settings), user=user)


@router.post("/settings/hours", dependencies=[Depends(verify_csrf)], response_model=None)
async def save_hours(
    request: Request,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Save sending hours (requires the admin's password)."""
    form = await request.form()
    if not auth.verify_user_password(db, user.id, str(form.get("current_password", ""))):
        ctx = _settings_context(db, settings, error="Contraseña incorrecta.")
        return render(request, "admin/settings.html", ctx, user=user, status_code=403)
    try:
        hours = admin_settings.SendingHours(
            timezone=str(form.get("timezone", "")).strip(),
            days={
                day: admin_settings.DayHours(
                    enabled=form.get(f"{day}_enabled") == "on",
                    start=str(form.get(f"{day}_start") or "09:00"),
                    end=str(form.get(f"{day}_end") or "18:00"),
                )
                for day in DAY_LABELS
            },
        )
    except ValidationError as exc:
        ctx = _settings_context(db, settings, error=_first_error(exc))
        return render(request, "admin/settings.html", ctx, user=user, status_code=400)
    admin_settings.update_sending_hours(db, hours, user.id)
    return RedirectResponse("/admin/settings?saved=1", status_code=303)


@router.post("/settings/report", dependencies=[Depends(verify_csrf)], response_model=None)
async def save_report(
    request: Request,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Save monthly report recipients and schedule (requires the admin's password)."""
    form = await request.form()
    if not auth.verify_user_password(db, user.id, str(form.get("current_password", ""))):
        ctx = _settings_context(db, settings, error="Contraseña incorrecta.")
        return render(request, "admin/settings.html", ctx, user=user, status_code=403)
    raw = str(form.get("recipients", ""))
    try:
        report = admin_settings.ReportSettings(
            recipients=[r for r in raw.replace(",", "\n").splitlines()],
            day=int(str(form.get("day") or 1)),
            send_time=str(form.get("send_time") or "08:00"),
        )
    except (ValidationError, ValueError) as exc:
        message = _first_error(exc) if isinstance(exc, ValidationError) else "Día no válido."
        ctx = _settings_context(db, settings, error=message)
        return render(request, "admin/settings.html", ctx, user=user, status_code=400)
    admin_settings.update_report_settings(db, report, user.id)
    return RedirectResponse("/admin/settings?saved=1", status_code=303)


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    return str(err.get("msg", "Datos no válidos")).removeprefix("Value error, ")
