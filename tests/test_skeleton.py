from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application import create_app
from app.config import Settings
from app.logging_utils import mask_phone
from app.timeutils import ensure_utc


def test_healthz(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_settings_defaults_and_day_validation(settings: Settings) -> None:
    assert settings.sending_days == ["mon", "tue", "wed", "thu", "fri", "sat"]
    assert settings.default_sending_start == time(9, 0)
    with pytest.raises(ValidationError):
        Settings(app_secret_key="x" * 16, default_sending_days="mon,funday", _env_file=None)  # type: ignore[call-arg]


def test_mask_phone_hides_middle_digits() -> None:
    assert mask_phone("+34612345678") == "+34******678"
    assert mask_phone(None) == "<none>"
    assert "612345" not in mask_phone("+34612345678")


def test_ensure_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    assert ensure_utc(naive).tzinfo is UTC
    madrid = datetime(2026, 1, 1, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    assert ensure_utc(madrid) == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_missing_config_gives_readable_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as info:
        get_settings()
    assert "APP_SECRET_KEY: missing" in str(info.value)
