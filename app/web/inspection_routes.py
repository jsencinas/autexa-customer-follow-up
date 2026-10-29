"""Employee pages: upload a photo, review and confirm the extracted data."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.enums import InspectionStatus
from app.services import inspections
from app.services.storage import InvalidImage
from app.web.security import CurrentUser, current_user, verify_csrf
from app.web.templating import render

router = APIRouter(prefix="/inspections")


def _read_upload(file: UploadFile, settings: Settings) -> bytes:
    # Read one byte past the limit so oversize files are rejected without loading everything.
    return file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)


@router.get("/new", response_class=HTMLResponse)
def new_page(request: Request, user: CurrentUser = Depends(current_user)) -> HTMLResponse:
    """Upload form (opens the camera on phones)."""
    return render(request, "inspections/new.html", user=user)


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
        return render(request, "inspections/new.html", {"error": str(exc)}, user=user, status_code=400)
    return RedirectResponse(f"/inspections/{inspection.id}/confirm", status_code=303)


@router.get("", response_class=HTMLResponse)
def list_page(
    request: Request,
    status: InspectionStatus | None = None,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """List inspections, e.g. the ones still waiting for confirmation."""
    items = inspections.list_by_status(db, status)
    return render(request, "inspections/list.html", {"items": items, "status": status}, user=user)


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


@router.get("/{inspection_id}/confirm", response_class=HTMLResponse)
def confirm_page(
    request: Request,
    inspection_id: int,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Review page; shows a processing notice until extraction finishes."""
    try:
        inspection = inspections.get_inspection(db, inspection_id)
    except inspections.InspectionNotFound:
        raise HTTPException(404) from None
    return render(request, "inspections/confirm.html", {"inspection": inspection}, user=user)


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
        current = inspections.get_inspection(db, inspection_id)
        ctx = {"inspection": current, "error": str(exc)}
        return render(request, "inspections/confirm.html", ctx, user=user, status_code=400)
    return RedirectResponse(f"/inspections/{inspection.id}/confirm", status_code=303)
