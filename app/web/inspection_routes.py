"""Employee pages: upload a photo, review and confirm the extracted data, and the status window."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.db_models import Inspection
from app.models.enums import InspectionStatus
from app.services import admin_settings, confirmation, health, inspections, opt_out
from app.services.storage import InvalidImage
from app.web.security import CurrentUser, current_user, verify_csrf
from app.web.templating import render

router = APIRouter(prefix="/inspections")


def _read_upload(file: UploadFile, settings: Settings) -> bytes:
    # Read one byte past the limit so oversize files are rejected without loading everything.
    return file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)


def _max_bytes(settings: Settings) -> int:
    return settings.max_upload_mb * 1024 * 1024


@router.get("/new", response_class=HTMLResponse)
def new_page(
    request: Request, user: CurrentUser = Depends(current_user), settings: Settings = Depends(get_settings)
) -> HTMLResponse:
    """Upload form (opens the camera on phones)."""
    return render(request, "inspections/new.html", {"max_bytes": _max_bytes(settings)}, user=user)


@router.post("/new", dependencies=[Depends(verify_csrf)], response_model=None)
def upload(
    request: Request,
    photo: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Store the photo and go to the review page while extraction runs."""
    try:
        inspection = inspections.create_from_upload(db, settings, _read_upload(photo, settings), user.id)
    except InvalidImage as exc:
        ctx = {"error": str(exc), "max_bytes": _max_bytes(settings)}
        return render(request, "inspections/new.html", ctx, user=user, status_code=400)
    return RedirectResponse(f"/inspections/{inspection.id}/confirm", status_code=303)


def _status_filter(status: str | None) -> InspectionStatus | None:
    if not status:
        return None
    try:
        return InspectionStatus(status)
    except ValueError:
        raise HTTPException(400, "Estado no válido") from None


@router.get("", response_class=HTMLResponse)
def list_page(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Status window: every inspection, filterable by status and searchable; updates itself."""
    selected = _status_filter(status)
    def url(path: str, status_value: str | None) -> str:
        query = urlencode({k: v for k, v in (("status", status_value), ("q", q)) if v})
        return f"{path}?{query}" if query else path

    ctx = {
        "items": inspections.list_by_status(db, selected, search=q),
        "status": selected,
        "q": q or "",
        "counts": inspections.count_by_status(db, q),
        "statuses": list(InspectionStatus),
        "tz": admin_settings.get_sending_hours(db, settings).tz,
        "live_url": url("/inspections/live", status),
        "tab_urls": {"": url("/inspections", None), **{s.value: url("/inspections", s.value) for s in InspectionStatus}},
        "signature": inspections.list_signature(db, selected, q),
    }
    return render(request, "inspections/list.html", ctx, user=user)


@router.get("/live")
def list_live(
    status: str | None = None,
    q: str | None = None,
    _user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Polled by the status window; the signature changes when the list should be redrawn."""
    return {"signature": inspections.list_signature(db, _status_filter(status), q)}


@router.get("/{inspection_id}/image")
def image(
    inspection_id: int,
    _user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve the inspection photo to logged-in users."""
    try:
        inspection = inspections.get_inspection(db, inspection_id)
    except inspections.InspectionNotFound:
        raise HTTPException(404) from None
    if not inspection.image_path:
        raise HTTPException(404)
    storage = inspections.storage_for(settings)
    path = storage.path(inspection.image_path)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type=storage.media_type(inspection.image_path))


def _review_context(
    db: Session, settings: Settings, inspection: Inspection, manual: bool = False, **extra: object
) -> dict[str, object]:
    duplicate_of = db.get(Inspection, inspection.duplicate_of_id) if inspection.duplicate_of_id else None
    worker_alive = health.worker_alive(db)
    return {
        "inspection": inspection,
        "manual": manual,
        "duplicate_of": duplicate_of,
        "opted_out": opt_out.is_opted_out(db, inspection.customer_phone),
        "worker_alive": worker_alive,
        "max_attempts": settings.max_extraction_attempts,
        "max_bytes": _max_bytes(settings),
        "signature": inspections.progress(inspection, worker_alive)["signature"],
        "tz": admin_settings.get_sending_hours(db, settings).tz,
        "errors": {},
        **extra,
    }


def _load(db: Session, inspection_id: int) -> Inspection:
    try:
        return inspections.get_inspection(db, inspection_id)
    except inspections.InspectionNotFound:
        raise HTTPException(404) from None


@router.get("/{inspection_id}/confirm", response_class=HTMLResponse)
def confirm_page(
    request: Request,
    inspection_id: int,
    manual: bool = False,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Review page: processing notice, unreadable/failed fallbacks, or the editable form."""
    inspection = _load(db, inspection_id)
    return render(request, "inspections/confirm.html", _review_context(db, settings, inspection, manual), user=user)


@router.get("/{inspection_id}/status")
def status(
    inspection_id: int, _user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Small JSON polled by the review page while the photo is being read."""
    return inspections.progress(_load(db, inspection_id), health.worker_alive(db))


@router.post("/{inspection_id}/confirm", dependencies=[Depends(verify_csrf)], response_model=None)
def confirm(
    request: Request,
    inspection_id: int,
    action: str = Form("confirm"),
    customer_name: str = Form(""),
    customer_phone: str = Form(""),
    service_description: str = Form(""),
    service_display: str = Form(""),
    service_date: str = Form(""),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Save corrections (action=save) or save and confirm (action=confirm)."""
    _load(db, inspection_id)
    form = confirmation.ReviewForm(
        customer_name=customer_name,
        customer_phone=customer_phone,
        service_description=service_description,
        service_display=service_display,
        service_date=service_date,
    )
    try:
        if action == "save":
            result = confirmation.save_edits(db, inspection_id, form, settings)
        else:
            result = confirmation.confirm(db, inspection_id, form, settings, user.id)
    except inspections.InvalidTransition as exc:
        ctx = _review_context(db, settings, _load(db, inspection_id), error=str(exc))
        return render(request, "inspections/confirm.html", ctx, user=user, status_code=409)
    if result.errors and result.duplicate_of is None:
        db.refresh(result.inspection)
        ctx = _review_context(db, settings, result.inspection, manual=True, errors=result.errors, form=form)
        return render(request, "inspections/confirm.html", ctx, user=user, status_code=400)
    return RedirectResponse(f"/inspections/{inspection_id}/confirm", status_code=303)


@router.post("/{inspection_id}/discard", dependencies=[Depends(verify_csrf)], response_model=None)
def discard(
    request: Request,
    inspection_id: int,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Discard a pending inspection."""
    _load(db, inspection_id)
    try:
        confirmation.discard(db, inspection_id)
    except inspections.InvalidTransition as exc:
        ctx = _review_context(db, settings, _load(db, inspection_id), error=str(exc))
        return render(request, "inspections/confirm.html", ctx, user=user, status_code=409)
    return RedirectResponse(f"/inspections/{inspection_id}/confirm", status_code=303)


@router.post("/{inspection_id}/retake", dependencies=[Depends(verify_csrf)], response_model=None)
def retake(
    request: Request,
    inspection_id: int,
    photo: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """Replace the photo with a new one and extract again."""
    try:
        inspection = inspections.replace_image(db, settings, inspection_id, _read_upload(photo, settings))
    except inspections.InspectionNotFound:
        raise HTTPException(404) from None
    except (InvalidImage, inspections.InvalidTransition) as exc:
        ctx = _review_context(db, settings, _load(db, inspection_id), error=str(exc))
        return render(request, "inspections/confirm.html", ctx, user=user, status_code=400)
    return RedirectResponse(f"/inspections/{inspection.id}/confirm", status_code=303)
