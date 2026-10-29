"""Admin section routes (admin role only)."""

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.enums import OptOutSource, Rating, Role
from app.services import admin_settings, auth, opt_out, reports, review, stats
from app.services.messages import Messages
from app.services.normalization import normalize_phone
from app.services.surveys import MessageSender
from app.web.deps import get_messages, get_sender
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
        "reports": reports.recent_reports(db),
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
            include_csv=form.get("include_csv") == "on",
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


# --- Review queue ------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)
) -> HTMLResponse:
    """Replies the bot was unsure about."""
    return render(request, "admin/review.html", {"items": review.pending_reviews(db)}, user=user)


@router.post("/review/{message_id}", dependencies=[Depends(verify_csrf)], response_model=None)
def resolve_review(
    request: Request,
    message_id: int,
    decision: review.ReviewDecision = Form(...),
    rating: Rating | None = Form(None),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sender: MessageSender = Depends(get_sender),
    messages: Messages = Depends(get_messages),
) -> HTMLResponse | RedirectResponse:
    """Apply the admin's decision on one reply."""
    try:
        review.resolve(db, message_id, decision, user.id, sender, messages, settings, rating=rating)
    except review.ReviewError as exc:
        ctx = {"items": review.pending_reviews(db), "error": str(exc)}
        return render(request, "admin/review.html", ctx, user=user, status_code=400)
    return RedirectResponse("/admin/review", status_code=303)


# --- Opt-out list -------------------------------------------------------------


@router.get("/opt-outs", response_class=HTMLResponse)
def opt_outs_page(
    request: Request, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)
) -> HTMLResponse:
    """Numbers that never receive bot messages."""
    return render(request, "admin/opt_outs.html", {"items": opt_out.list_all(db)}, user=user)


@router.post("/opt-outs", dependencies=[Depends(verify_csrf)], response_model=None)
def add_opt_out(
    request: Request,
    phone: str = Form(...),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Add a number by hand (no message is sent to the customer)."""
    normalized = normalize_phone(phone, settings.default_country_code)
    if normalized is None:
        ctx = {"items": opt_out.list_all(db), "error": "Teléfono no válido."}
        return render(request, "admin/opt_outs.html", ctx, user=user, status_code=400)
    opt_out.add(db, normalized, OptOutSource.ADMIN, created_by_id=user.id)
    return RedirectResponse("/admin/opt-outs", status_code=303)


@router.post("/opt-outs/{opt_out_id}/remove", dependencies=[Depends(verify_csrf)], response_model=None)
def remove_opt_out(
    request: Request,
    opt_out_id: int,
    reason: str = Form(""),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Remove a number; a reason is required and audited."""
    try:
        opt_out.remove(db, opt_out_id, reason, user.id)
    except opt_out.OptOutError as exc:
        ctx = {"items": opt_out.list_all(db), "error": str(exc)}
        return render(request, "admin/opt_outs.html", ctx, user=user, status_code=400)
    return RedirectResponse("/admin/opt-outs", status_code=303)


# --- Dashboard and results ------------------------------------------------------

results_router = APIRouter()


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise HTTPException(400, "Fecha no válida (AAAA-MM-DD)") from None


def _filters(
    date_from: str | None = None,
    date_to: str | None = None,
    service: str | None = None,
    rating: str | None = None,
) -> stats.SurveyFilter:
    """Query-string filters; empty values (as sent by the filter form) mean "no filter"."""
    if rating and rating not in {r.value for r in Rating}:
        raise HTTPException(400, "Valoración no válida (good, okay, bad)")
    return stats.SurveyFilter(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        service=(service or "").strip() or None,
        rating=Rating(rating) if rating else None,
    )


def _tz(db: Session, settings: Settings) -> ZoneInfo:
    return admin_settings.get_sending_hours(db, settings).tz


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    f: stats.SurveyFilter = Depends(_filters),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Dashboard page; charts load from /admin/api/stats and refresh every 30 seconds."""
    data = stats.compute_stats(db, f, _tz(db, settings))
    return render(request, "admin/dashboard.html", {"filters": f, "stats": data}, user=user)


@router.get("/api/stats")
def stats_api(
    f: stats.SurveyFilter = Depends(_filters),
    _user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """JSON statistics polled by the dashboard."""
    return stats.compute_stats(db, f, _tz(db, settings))


@results_router.get("/surveys")
def list_surveys(
    f: stats.SurveyFilter = Depends(_filters),
    _user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Sent surveys and their answers, filterable by date and rating (admin only)."""
    return stats.survey_rows(db, f, _tz(db, settings))


@results_router.get("/surveys.csv")
def export_surveys(
    f: stats.SurveyFilter = Depends(_filters),
    _user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """CSV export of the same rows."""
    body = stats.to_csv(stats.survey_rows(db, f, _tz(db, settings)))
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="encuestas.csv"'},
    )


@router.post("/reports/resend", dependencies=[Depends(verify_csrf)])
def resend_report(
    period: str = Form(...),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Queue a monthly report (YYYY-MM) to be sent again by the worker."""
    try:
        year, month = (int(p) for p in period.split("-", 1))
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Mes no válido (AAAA-MM)") from None
    reports.request_resend(db, year, month, user.id)
    return RedirectResponse("/admin/settings?resent=1", status_code=303)
