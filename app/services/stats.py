"""Survey results: dashboard statistics, the `/surveys` listing and CSV export.

Aggregation is done in Python over the filtered rows (small volumes), which keeps it portable
between SQLite and PostgreSQL.
"""

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import Inspection
from app.models.enums import Rating
from app.services import review

RATINGS: tuple[str, ...] = (Rating.GOOD, Rating.OKAY, Rating.BAD)
TOP_SERVICES = 8
LATEST_COMMENTS = 10


@dataclass
class SurveyFilter:
    """Filters shared by the dashboard, `/surveys`, the CSV export and the monthly report."""

    date_from: date | None = None
    date_to: date | None = None
    service: str | None = None
    rating: Rating | None = None


def _bounds(f: SurveyFilter, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(f.date_from, time(0), tzinfo=tz) if f.date_from else None
    end = datetime.combine(f.date_to + timedelta(days=1), time(0), tzinfo=tz) if f.date_to else None
    return start, end


def sent_surveys(db: Session, f: SurveyFilter, tz: ZoneInfo) -> list[Inspection]:
    """Surveys that were sent, filtered by send date (business timezone), service text and rating."""
    query = select(Inspection).where(Inspection.sent_at.is_not(None)).order_by(Inspection.sent_at.desc())
    start, end = _bounds(f, tz)
    if start:
        query = query.where(Inspection.sent_at >= start)
    if end:
        query = query.where(Inspection.sent_at < end)
    if f.rating:
        query = query.where(Inspection.rating == f.rating)
    rows = list(db.scalars(query))
    if f.service:
        needle = f.service.casefold()
        rows = [r for r in rows if needle in (r.service_display or r.service_description or "").casefold()]
    return rows


def _week_start(moment: datetime, tz: ZoneInfo) -> date:
    local = moment.astimezone(tz).date()
    return local - timedelta(days=local.weekday())


def compute_stats(db: Session, f: SurveyFilter, tz: ZoneInfo) -> dict[str, Any]:
    """Everything the dashboard charts need (no phone numbers)."""
    rows = sent_surveys(db, f, tz)
    rated = [r for r in rows if r.rating]
    distribution = Counter(r.rating for r in rated)

    weekly: dict[date, Counter[str]] = defaultdict(Counter)
    for r in rated:
        weekly[_week_start(r.rating_at or r.sent_at, tz)][r.rating] += 1  # type: ignore[arg-type, index]

    by_service: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rated:
        by_service[r.service_display or r.service_description or "—"][r.rating] += 1  # type: ignore[index]
    top = sorted(by_service.items(), key=lambda kv: -sum(kv[1].values()))[:TOP_SERVICES]

    comments = sorted((r for r in rows if r.comment), key=lambda r: r.comment_at or r.sent_at, reverse=True)  # type: ignore[arg-type, return-value]
    return {
        "sent": len(rows),
        "answered": len(rated),
        "response_rate": round(len(rated) / len(rows), 3) if rows else 0.0,
        "distribution": {k: distribution.get(k, 0) for k in RATINGS},
        "weekly": [
            {"week": week.isoformat(), **{k: counts.get(k, 0) for k in RATINGS}} for week, counts in sorted(weekly.items())
        ],
        "by_service": [{"service": name, **{k: counts.get(k, 0) for k in RATINGS}} for name, counts in top],
        "comments": [
            {
                "date": (r.comment_at or r.sent_at).astimezone(tz).date().isoformat(),  # type: ignore[union-attr]
                "rating": r.rating,
                "service": r.service_display or "",
                "customer": (r.customer_name or "").split(" ")[0],
                "comment": r.comment,
            }
            for r in comments[:LATEST_COMMENTS]
        ],
        "pending_review": review.count_pending(db),
    }


def survey_rows(db: Session, f: SurveyFilter, tz: ZoneInfo) -> list[dict[str, Any]]:
    """Flat rows for `/surveys` and the CSV export."""
    return [
        {
            "id": r.id,
            "status": r.status,
            "customer_name": r.customer_name,
            "customer_phone": r.customer_phone,
            "service": r.service_display or r.service_description,
            "service_date": r.service_date.isoformat() if r.service_date else None,
            "sent_at": r.sent_at.astimezone(tz).isoformat() if r.sent_at else None,
            "rating": r.rating,
            "rating_source": r.rating_source,
            "comment": r.comment,
            "template": r.template_name,
        }
        for r in sent_surveys(db, f, tz)
    ]


CSV_COLUMNS = (
    "id",
    "status",
    "customer_name",
    "customer_phone",
    "service",
    "service_date",
    "sent_at",
    "rating",
    "rating_source",
    "comment",
    "template",
)


def _csv_safe(value: Any) -> Any:
    # Prevent spreadsheet formula injection from customer-written text.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") and not value[1:2].isdigit():
        return "'" + value
    return value


def to_csv(rows: list[dict[str, Any]]) -> str:
    """CSV text (UTF-8 with BOM so Excel shows accents correctly)."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_safe(v) for k, v in row.items()})
    return buffer.getvalue()
