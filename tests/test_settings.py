from datetime import UTC, datetime, time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import AuditLog
from app.services import admin_settings, sending_window
from app.services.admin_settings import DayHours, ReportSettings, SendingHours
from tests.conftest import PASSWORD, csrf_from

# Europe/Madrid is UTC+2 in summer (CEST) and UTC+1 in winter (CET).


def hours(settings: Settings) -> SendingHours:
    return admin_settings.default_sending_hours(settings)  # Mon-Sat 09:00-18:00 Europe/Madrid


def test_defaults_come_from_env(db: Session, settings: Settings) -> None:
    h = admin_settings.get_sending_hours(db, settings)
    assert h.timezone == "Europe/Madrid"
    assert h.days["mon"].enabled and not h.days["sun"].enabled
    assert h.days["sat"].start == time(9, 0) and h.days["sat"].end == time(18, 0)


def test_validation() -> None:
    with pytest.raises(ValidationError):
        DayHours(enabled=True, start=time(18), end=time(9))
    with pytest.raises(ValidationError):
        SendingHours(timezone="Mars/Olympus", days={})
    with pytest.raises(ValidationError):
        ReportSettings(recipients=["not-an-email"])
    assert ReportSettings(recipients=[" A@b.com", "a@b.com", ""]).recipients == ["a@b.com"]


def test_update_is_stored_and_audited(db: Session, settings: Settings) -> None:
    new = hours(settings)
    new.days["sun"] = DayHours(enabled=True, start=time(10), end=time(14))
    admin_settings.update_sending_hours(db, new, user_id=None)
    assert admin_settings.get_sending_hours(db, settings).days["sun"].enabled
    entry = db.query(AuditLog).one()
    assert entry.target == "sending_hours" and entry.old_value is None
    admin_settings.update_report_settings(db, ReportSettings(recipients=["x@y.com"], day=2), user_id=None)
    assert admin_settings.get_report_settings(db, settings).day == 2
    assert len(admin_settings.audit_entries(db)) == 2


def test_is_open(settings: Settings) -> None:
    h = hours(settings)
    # Wednesday 2026-10-14 08:30 UTC = 10:30 Madrid (CEST)
    assert sending_window.is_open(datetime(2026, 10, 14, 8, 30, tzinfo=UTC), h)
    # 16:30 UTC = 18:30 Madrid: closed
    assert not sending_window.is_open(datetime(2026, 10, 14, 16, 30, tzinfo=UTC), h)


def test_next_opening_same_day_later(settings: Settings) -> None:
    # Wednesday 05:00 UTC = 07:00 Madrid -> opens 09:00 Madrid = 07:00 UTC
    assert sending_window.next_opening(datetime(2026, 10, 14, 5, 0, tzinfo=UTC), hours(settings)) == datetime(
        2026, 10, 14, 7, 0, tzinfo=UTC
    )


def test_next_opening_skips_sunday(settings: Settings) -> None:
    # Saturday 2026-10-17 17:00 UTC = 19:00 Madrid -> Monday 09:00 Madrid
    assert sending_window.next_opening(datetime(2026, 10, 17, 17, 0, tzinfo=UTC), hours(settings)) == datetime(
        2026, 10, 19, 7, 0, tzinfo=UTC
    )


def test_next_opening_across_dst_change(settings: Settings) -> None:
    # Saturday 2026-10-24 19:00 Madrid; DST ends Sunday 25th -> Monday 26th 09:00 CET = 08:00 UTC
    assert sending_window.next_opening(datetime(2026, 10, 24, 17, 0, tzinfo=UTC), hours(settings)) == datetime(
        2026, 10, 26, 8, 0, tzinfo=UTC
    )


def test_next_opening_returns_moment_when_open(settings: Settings) -> None:
    moment = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert sending_window.next_opening(moment, hours(settings)) == moment


def test_next_opening_none_when_no_day_enabled(settings: Settings) -> None:
    h = hours(settings)
    h.days = {d: DayHours(enabled=False) for d in h.days}
    assert sending_window.next_opening(datetime(2026, 10, 14, tzinfo=UTC), h) is None


def _hours_form(token: str, password: str = PASSWORD) -> dict[str, str]:
    data = {"csrf_token": token, "timezone": "Europe/Madrid", "current_password": password}
    for day in ("mon", "tue", "wed", "thu", "fri"):
        data.update({f"{day}_enabled": "on", f"{day}_start": "10:00", f"{day}_end": "14:00"})
    return data


def test_admin_can_change_hours(admin_client: TestClient, db: Session, settings: Settings) -> None:
    page = admin_client.get("/admin/settings")
    assert page.status_code == 200 and "Horario de envío" in page.text
    response = admin_client.post("/admin/settings/hours", data=_hours_form(csrf_from(page.text)), follow_redirects=False)
    assert response.status_code == 303
    h = admin_settings.get_sending_hours(db, settings)
    assert h.days["mon"].start == time(10) and not h.days["sat"].enabled
    assert "update_setting" in admin_client.get("/admin/settings").text


def test_changing_settings_requires_password(admin_client: TestClient, db: Session, settings: Settings) -> None:
    page = admin_client.get("/admin/settings")
    response = admin_client.post("/admin/settings/hours", data=_hours_form(csrf_from(page.text), "wrong"))
    assert response.status_code == 403
    assert admin_settings.get_sending_hours(db, settings).days["sat"].enabled  # unchanged


def test_invalid_hours_are_rejected(admin_client: TestClient) -> None:
    page = admin_client.get("/admin/settings")
    data = _hours_form(csrf_from(page.text))
    data["mon_start"], data["mon_end"] = "18:00", "09:00"
    response = admin_client.post("/admin/settings/hours", data=data)
    assert response.status_code == 400 and "anterior" in response.text


def test_admin_can_change_report(admin_client: TestClient, db: Session, settings: Settings) -> None:
    page = admin_client.get("/admin/settings")
    data = {
        "csrf_token": csrf_from(page.text),
        "recipients": "jefe@taller.es\nsocio@taller.es",
        "day": "3",
        "send_time": "07:30",
        "current_password": PASSWORD,
    }
    assert admin_client.post("/admin/settings/report", data=data, follow_redirects=False).status_code == 303
    report = admin_settings.get_report_settings(db, settings)
    assert report.recipients == ["jefe@taller.es", "socio@taller.es"] and report.day == 3


def test_settings_page_rejects_employee(employee_client: TestClient) -> None:
    assert employee_client.get("/admin/settings").status_code == 403
