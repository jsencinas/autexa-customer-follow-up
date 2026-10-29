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
