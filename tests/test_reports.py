from datetime import UTC, date, datetime, time, timedelta
from email import message_from_bytes
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.email_client import EmailClient, EmailError, OutgoingEmail
from app.models.db_models import MonthlyReport
from app.models.enums import InspectionStatus, Rating, ReportStatus
from app.services import admin_settings, reports
from app.services.reports import Period
from tests.conftest import csrf_from
from tests.fakes import FakeMailer, scheduled_inspection

TZ = ZoneInfo("Europe/Madrid")
# 1 November 2026, 09:00 Madrid (CET) = 08:00 UTC: after the default 08:00 send time.
DUE = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def configured(db: Session) -> None:
    admin_settings.update_report_settings(db, admin_settings.ReportSettings(recipients=["jefe@taller.es"]), None)


def october_surveys(db: Session) -> None:
    for n, rating in enumerate([Rating.GOOD, Rating.GOOD, Rating.BAD]):
        sent = datetime(2026, 10, 5 + n, 10, tzinfo=UTC)
        scheduled_inspection(db, sent, phone=f"+3461234560{n}", status=InspectionStatus.ANSWERED, sent_at=sent,
                             rating=rating, rating_at=sent, comment="Muy bien <b>todo</b>" if n == 0 else None,
                             comment_at=sent if n == 0 else None, service_description=f"s{n}")


def test_period_helpers() -> None:
    assert Period(2026, 1).previous() == Period(2025, 12)
    assert Period(2026, 12).next() == Period(2027, 1)
    assert Period(2026, 2).last_day == date(2026, 2, 28)
    assert Period(2026, 9).label == "septiembre de 2026"


def test_due_period() -> None:
    report = admin_settings.ReportSettings(day=1, send_time=time(8, 0))
    assert reports.due_period(DUE, report, TZ) == Period(2026, 10)
    assert reports.due_period(DUE - timedelta(hours=2), report, TZ) is None  # 07:00 Madrid on the 1st
    assert reports.due_period(datetime(2026, 11, 20, tzinfo=UTC), report, TZ) == Period(2026, 10)


def test_build_report_has_charts_and_no_phones(db: Session) -> None:
    october_surveys(db)
    email = reports.build_report(db, Period(2026, 10), TZ, include_csv=False)
    assert "octubre de 2026" in email.subject
    assert set(email.inline_images) == {"distribution", "weekly"}
    assert all(img.startswith(b"\x89PNG") for img in email.inline_images.values())
    assert "+3461234560" not in email.html and "Muy bien &lt;b&gt;todo&lt;/b&gt;" in email.html
    assert "67 %" in email.html  # 2 of 3 rated "good"
    assert not email.attachments
    with_csv = reports.build_report(db, Period(2026, 10), TZ, include_csv=True)
    assert list(with_csv.attachments) == ["encuestas-2026-10.csv"]


def test_report_sent_once_even_after_restart(db: Session, settings: Settings, configured: None) -> None:
    october_surveys(db)
    mailer = FakeMailer()
    reports.run_monthly_report(db, mailer, settings, DUE)
    reports.run_monthly_report(db, mailer, settings, DUE + timedelta(minutes=1))
    reports.run_monthly_report(db, FakeMailer(), settings, DUE + timedelta(days=3))  # a "restarted" worker
    assert len(mailer.sent) == 1
    row = db.query(MonthlyReport).one()
    assert row.status == ReportStatus.SENT and row.recipients == ["jefe@taller.es"]


def test_no_report_without_recipients_or_history(db: Session, settings: Settings) -> None:
    october_surveys(db)
    mailer = FakeMailer()
    reports.run_monthly_report(db, mailer, settings, DUE)
    assert not mailer.sent and db.query(MonthlyReport).count() == 0
    admin_settings.update_report_settings(db, admin_settings.ReportSettings(recipients=["a@b.es"]), None)
    reports.run_monthly_report(db, mailer, settings, datetime(2026, 10, 1, 8, tzinfo=UTC))  # September: nothing sent yet
    assert db.query(MonthlyReport).count() == 0


def test_retry_then_fail(db: Session, settings: Settings, configured: None) -> None:
    october_surveys(db)
    mailer = FakeMailer(errors=[EmailError("down", retryable=True)] * settings.max_report_attempts)
    now = DUE
    reports.run_monthly_report(db, mailer, settings, now)
    row = db.query(MonthlyReport).one()
    assert row.status == ReportStatus.PENDING and row.next_attempt_at > now  # type: ignore[operator]
    for _ in range(settings.max_report_attempts - 1):
        now = row.next_attempt_at  # type: ignore[assignment]
        reports.run_monthly_report(db, mailer, settings, now)
        db.refresh(row)
    assert row.status == ReportStatus.FAILED and not mailer.sent


def test_crash_while_sending_is_not_resent(db: Session, settings: Settings, configured: None) -> None:
    october_surveys(db)
    db.add(MonthlyReport(year=2026, month=10, status=ReportStatus.SENDING, claimed_at=DUE))
    db.commit()
    mailer = FakeMailer()
    reports.run_monthly_report(db, mailer, settings, DUE + timedelta(minutes=31))
    assert db.query(MonthlyReport).one().status == ReportStatus.FAILED and not mailer.sent


def test_manual_resend(db: Session, settings: Settings, configured: None) -> None:
    october_surveys(db)
    mailer = FakeMailer()
    reports.run_monthly_report(db, mailer, settings, DUE)
    reports.request_resend(db, 2026, 10, None, DUE + timedelta(hours=1))
    reports.run_monthly_report(db, mailer, settings, DUE + timedelta(hours=1))
    assert len(mailer.sent) == 2


def test_email_client_builds_mime_with_inline_images() -> None:
    client = EmailClient("smtp.example.com", 587, "u", "p", "bot@taller.es")
    email = OutgoingEmail(to=["a@b.es"], subject="Informe", html='<img src="cid:chart">', text="Informe",
                          inline_images={"chart": b"\x89PNGdata"}, attachments={"x.csv": (b"a,b", "text/csv")})
    parsed = message_from_bytes(client.build(email).as_bytes())
    types = [p.get_content_type() for p in parsed.walk()]
    assert "image/png" in types and "text/html" in types and "text/csv" in types
    assert parsed["To"] == "a@b.es"


def test_email_client_requires_configuration() -> None:
    with pytest.raises(EmailError) as info:
        EmailClient("", 587, "", "", "").send(OutgoingEmail(to=["a@b.es"], subject="s", html="h", text="t"))
    assert not info.value.retryable


def test_resend_from_settings_page(admin_client: TestClient, db: Session) -> None:
    page = admin_client.get("/admin/settings")
    token = csrf_from(page.text)
    assert admin_client.post("/admin/reports/resend", data={"period": "2026-13", "csrf_token": token}).status_code == 400
    response = admin_client.post("/admin/reports/resend", data={"period": "2026-10", "csrf_token": token},
                                 follow_redirects=False)
    assert response.status_code == 303
    assert db.query(MonthlyReport).one().status == ReportStatus.PENDING
    assert "2026-10" in admin_client.get("/admin/settings").text
