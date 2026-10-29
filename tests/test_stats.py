import csv
import io
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import InspectionStatus, Rating
from app.services import stats
from app.services.stats import SurveyFilter
from tests.fakes import scheduled_inspection

TZ = ZoneInfo("Europe/Madrid")
BASE = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)  # a Monday


def answered(db: Session, rating: str | None, days: int, service: str = "Cambio de aceite", comment: str | None = None, n: int = 0):  # type: ignore[no-untyped-def]
    sent = BASE + timedelta(days=days)
    return scheduled_inspection(
        db, sent, phone=f"+3461234{days:02d}{n:02d}"[:12], status=InspectionStatus.ANSWERED if rating else InspectionStatus.SENT,
        sent_at=sent, rating=rating, rating_at=sent if rating else None, service_display=service,
        service_description=f"{service} {days}-{n}", comment=comment, comment_at=sent if comment else None,
    )


def seed(db: Session) -> None:
    answered(db, Rating.GOOD, 0, comment="Muy rápidos")
    answered(db, Rating.GOOD, 1, service="Neumáticos")
    answered(db, Rating.BAD, 8, comment="=HYPERLINK(\"x\")")
    answered(db, None, 9)


def test_compute_stats(db: Session) -> None:
    seed(db)
    s = stats.compute_stats(db, SurveyFilter(), TZ)
    assert s["sent"] == 4 and s["answered"] == 3 and s["response_rate"] == 0.75
    assert s["distribution"] == {"good": 2, "okay": 0, "bad": 1}
    assert [w["week"] for w in s["weekly"]] == ["2026-10-05", "2026-10-12"]
    assert s["weekly"][0]["good"] == 2 and s["weekly"][1]["bad"] == 1
    assert s["by_service"][0] == {"service": "Cambio de aceite", "good": 1, "okay": 0, "bad": 1}
    assert s["comments"][0]["comment"].startswith("=HYPERLINK")
    assert all("phone" not in c for c in s["comments"])


def test_filters(db: Session) -> None:
    seed(db)
    assert stats.compute_stats(db, SurveyFilter(date_from=date(2026, 10, 12)), TZ)["sent"] == 2
    assert stats.compute_stats(db, SurveyFilter(date_to=date(2026, 10, 6)), TZ)["sent"] == 2
    assert stats.compute_stats(db, SurveyFilter(service="neumát"), TZ)["sent"] == 1
    assert len(stats.survey_rows(db, SurveyFilter(rating=Rating.GOOD), TZ)) == 2


def test_empty_stats(db: Session) -> None:
    s = stats.compute_stats(db, SurveyFilter(), TZ)
    assert s["sent"] == 0 and s["response_rate"] == 0.0 and s["weekly"] == []


def test_csv_escapes_formulas_and_keeps_phones(db: Session) -> None:
    seed(db)
    text = stats.to_csv(stats.survey_rows(db, SurveyFilter(), TZ))
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert len(rows) == 4
    assert any(r["comment"].startswith("'=HYPERLINK") for r in rows)
    assert all(r["customer_phone"].startswith("+34") for r in rows)


def test_dashboard_and_api(admin_client: TestClient, db: Session) -> None:
    seed(db)
    page = admin_client.get("/admin?date_from=&date_to=&service=")
    assert page.status_code == 200 and "chart.umd.min.js" in page.text and "Muy rápidos" in page.text
    data = admin_client.get("/admin/api/stats").json()
    assert data["answered"] == 3
    assert admin_client.get("/admin/api/stats?date_from=bad").status_code == 400
    assert admin_client.get("/static/vendor/chart.umd.min.js").status_code == 200


def test_surveys_endpoint_and_csv(admin_client: TestClient, db: Session) -> None:
    seed(db)
    rows = admin_client.get("/surveys", params={"rating": "good"}).json()
    assert len(rows) == 2 and {r["rating"] for r in rows} == {"good"}
    assert admin_client.get("/surveys", params={"rating": "great"}).status_code == 400
    export = admin_client.get("/surveys.csv", params={"date_from": "2026-10-12"})
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"]
    assert len(export.text.strip().splitlines()) == 3  # header + 2 rows


def test_results_require_admin(employee_client: TestClient, client: TestClient) -> None:
    assert employee_client.get("/admin").status_code == 403
    assert employee_client.get("/surveys").status_code == 403
    assert employee_client.get("/surveys.csv").status_code == 403
    assert client.get("/admin/api/stats", follow_redirects=False).status_code == 303
