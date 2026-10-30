from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.models.db_models import User
from app.models.enums import Role
from app.services import auth
from app.timeutils import utcnow
from tests.conftest import PASSWORD, csrf_from, login


def test_password_hash_roundtrip() -> None:
    hashed = auth.hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert auth.verify_password(hashed, "s3cret-pass")
    assert not auth.verify_password(hashed, "wrong")
    assert not auth.verify_password("not-a-hash", "x")


def test_create_user_validation(db: Session) -> None:
    user = auth.create_user(db, "  Ana.Lopez ", "longenough", Role.ADMIN)
    assert user.username == "ana.lopez" and user.role == Role.ADMIN
    with pytest.raises(auth.UserError):
        auth.create_user(db, "ana.lopez", "longenough")
    with pytest.raises(auth.UserError):
        auth.create_user(db, "bob", "short")
    with pytest.raises(auth.UserError):
        auth.create_user(db, "bad name!", "longenough")


def test_authenticate_and_lockout(db: Session) -> None:
    auth.create_user(db, "ana", "longenough")
    assert auth.authenticate(db, "ANA", "longenough", "1.1.1.1", max_attempts=3, lockout_minutes=15).username == "ana"
    for _ in range(3):
        with pytest.raises(auth.InvalidCredentials):
            auth.authenticate(db, "ana", "nope", "1.1.1.1", max_attempts=3, lockout_minutes=15)
    with pytest.raises(auth.LoginLocked):
        auth.authenticate(db, "ana", "longenough", "1.1.1.1", max_attempts=3, lockout_minutes=15)
    # After the lockout window the correct password works again.
    later = utcnow() + timedelta(minutes=16)
    assert auth.authenticate(db, "ana", "longenough", "1.1.1.1", max_attempts=3, lockout_minutes=15, now=later)


def test_disabled_user_cannot_log_in(db: Session) -> None:
    user = auth.create_user(db, "ana", "longenough")
    auth.set_user_active(db, user.id, False)
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate(db, "ana", "longenough", "ip", max_attempts=5, lockout_minutes=15)
    assert not auth.verify_user_password(db, user.id, "longenough")


def test_pages_redirect_to_login_when_anonymous(client: TestClient) -> None:
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=/admin/users")


def test_login_requires_csrf(client: TestClient, employee: User) -> None:
    client.get("/login")
    response = client.post("/login", data={"username": employee.username, "password": PASSWORD})
    assert response.status_code == 403


def test_login_wrong_password(client: TestClient, employee: User) -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"username": employee.username, "password": "nope", "csrf_token": token})
    assert response.status_code == 401
    assert "incorrectos" in response.text


def test_admin_routes_reject_employee_role(employee_client: TestClient) -> None:
    assert employee_client.get("/admin/users").status_code == 403


def test_admin_can_create_employee(admin_client: TestClient, db: Session) -> None:
    page = admin_client.get("/admin/users")
    assert page.status_code == 200
    response = admin_client.post(
        "/admin/users",
        data={"username": "nuevo", "password": "longenough", "role": "employee", "csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert auth.authenticate(db, "nuevo", "longenough", "ip", max_attempts=5, lockout_minutes=15)


def test_logout_clears_session(app, employee: User) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(app)
    login(c, employee.username)
    token = csrf_from(c.get("/login").text)
    assert c.post("/logout", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert c.get("/admin/users", follow_redirects=False).status_code == 303


def test_admin_session_times_out(app, admin_user: User, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c = TestClient(app)
    login(c, admin_user.username)
    assert c.get("/admin/users").status_code == 200
    future = utcnow() + timedelta(minutes=31)
    monkeypatch.setattr("app.web.security.utcnow", lambda: future)
    assert c.get("/admin/users", follow_redirects=False).status_code == 303


def test_cli_create_user(settings, monkeypatch: pytest.MonkeyPatch, db: Session) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "longenough")
    assert cli_main(["create-user", "--username", "boss", "--role", "admin"]) == 0
    assert auth.authenticate(db, "boss", "longenough", "ip", max_attempts=5, lockout_minutes=15).role == "admin"


def test_set_password_replaces_hash_and_clears_lockout(db: Session) -> None:
    auth.create_user(db, "ana", "longenough")
    for _ in range(3):
        with pytest.raises(auth.InvalidCredentials):
            auth.authenticate(db, "ana", "nope", "ip", max_attempts=3, lockout_minutes=15)
    auth.set_password(db, "ANA", "brand-new-pass")
    assert auth.authenticate(db, "ana", "brand-new-pass", "ip", max_attempts=3, lockout_minutes=15)
    with pytest.raises(auth.UserError):
        auth.set_password(db, "ana", "short")
    with pytest.raises(auth.UserError):
        auth.set_password(db, "nobody", "longenough")


def test_cli_set_password(settings, monkeypatch: pytest.MonkeyPatch, db: Session) -> None:  # type: ignore[no-untyped-def]
    auth.create_user(db, "boss", "old-password", Role.ADMIN)
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "new-password")
    assert cli_main(["set-password", "--username", "boss"]) == 0
    assert auth.authenticate(db, "boss", "new-password", "ip", max_attempts=5, lockout_minutes=15)
    assert cli_main(["set-password", "--username", "ghost"]) == 1
