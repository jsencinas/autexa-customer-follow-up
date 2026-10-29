"""Shared pytest fixtures."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789")

from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a temporary SQLite database and storage dir."""
    return Settings(
        app_secret_key="test-secret-key-0123456789",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "images",
        session_cookie_secure=False,
        whatsapp_app_secret="app-secret",
        whatsapp_verify_token="verify-token",
        whatsapp_access_token="token",
        whatsapp_phone_number_id="123",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def session_factory(settings: Settings) -> sessionmaker[Session]:
    """A fresh database for each test."""
    return init_db(settings.database_url)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A database session for one test."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


import re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.models.enums import Role  # noqa: E402
from app.services import auth  # noqa: E402

PASSWORD = "correct-horse-1"


def csrf_from(html: str) -> str:
    """Extract the CSRF token from a rendered page."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token in page"
    return match.group(1)


def login(client: TestClient, username: str, password: str = PASSWORD) -> None:
    """Log a test client in through the real login form."""
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login", data={"username": username, "password": password, "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303, response.text


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    """The FastAPI app wired to the test settings."""
    from app.application import create_app

    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:  # type: ignore[no-untyped-def]
    """An anonymous test client."""
    return TestClient(app)


@pytest.fixture
def employee(app, db: Session):  # type: ignore[no-untyped-def]
    """An employee account."""
    return auth.create_user(db, "empleado", PASSWORD, Role.EMPLOYEE)


@pytest.fixture
def admin_user(app, db: Session):  # type: ignore[no-untyped-def]
    """An admin account."""
    return auth.create_user(db, "jefe", PASSWORD, Role.ADMIN)


@pytest.fixture
def employee_client(app, employee) -> TestClient:  # type: ignore[no-untyped-def]
    """A client logged in as an employee."""
    c = TestClient(app)
    login(c, employee.username)
    return c


@pytest.fixture
def admin_client(app, admin_user) -> TestClient:  # type: ignore[no-untyped-def]
    """A client logged in as an admin."""
    c = TestClient(app)
    login(c, admin_user.username)
    return c
