"""Admin section routes (admin role only)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.enums import Role
from app.services import auth
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
