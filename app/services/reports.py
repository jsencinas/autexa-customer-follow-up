"""Monthly email report with charts, sent once per month to the configured recipients."""

import html
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.integrations.email_client import EmailError, OutgoingEmail  # noqa: E402
from app.models.db_models import Inspection, MonthlyReport  # noqa: E402
from app.models.enums import ReportStatus  # noqa: E402
from app.services import admin_settings, stats  # noqa: E402
from app.services.retry import backoff  # noqa: E402
from app.timeutils import utcnow  # noqa: E402

logger = logging.getLogger(__name__)

STALE_CLAIM = timedelta(minutes=30)
MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip
# Same status colours as the dashboard; every chart also carries labels and values.
COLORS = {"good": "#0ca30c", "okay": "#fab219", "bad": "#d03b3b"}
LABELS = {"good": "Bien", "okay": "Regular", "bad": "Mal"}


class Mailer(Protocol):
    """The email client (or a test fake)."""

    def send(self, email: OutgoingEmail) -> None:
        """Send or raise EmailError."""
        ...


@dataclass(frozen=True)
class Period:
    """A calendar month."""

    year: int
    month: int

    @property
    def first_day(self) -> date:
        """First day of the month."""
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        """Last day of the month."""
        return self.next().first_day - timedelta(days=1)

    def previous(self) -> "Period":
        """The month before."""
        return Period(self.year - 1, 12) if self.month == 1 else Period(self.year, self.month - 1)

    def next(self) -> "Period":
        """The month after."""
        return Period(self.year + 1, 1) if self.month == 12 else Period(self.year, self.month + 1)

    @property
    def label(self) -> str:
        """Spanish name, e.g. "septiembre de 2026"."""
        return f"{MONTHS_ES[self.month - 1]} de {self.year}"


def due_period(now: datetime, report: admin_settings.ReportSettings, tz: ZoneInfo) -> Period | None:
    """The month to report on, once this month's send day/time has passed (business timezone)."""
    local = now.astimezone(tz)
    if (local.day, local.time()) < (report.day, report.send_time):
        return None
    return Period(local.year, local.month).previous()


# --- content ---------------------------------------------------------------------------


def _png(fig: Any) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=144, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _style(ax: Any) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))


def distribution_chart(distribution: dict[str, int]) -> bytes:
    """Bar chart of ratings with values written on each bar."""
    fig, ax = plt.subplots(figsize=(5, 3))
    keys = list(stats.RATINGS)
    values = [distribution.get(k, 0) for k in keys]
    bars = ax.bar([LABELS[k] for k in keys], values, color=[COLORS[k] for k in keys], width=0.55, edgecolor="white", linewidth=2)
    ax.bar_label(bars, padding=3, color="#0b0b0b")
    ax.set_title("Valoraciones del mes", loc="left", color="#0b0b0b")
    ax.set_ylim(0, max(values + [1]) * 1.2)
    _style(ax)
    return _png(fig)


def weekly_chart(weekly: list[dict[str, Any]]) -> bytes:
    """Stacked weekly ratings."""
    fig, ax = plt.subplots(figsize=(6, 3))
    labels = [date.fromisoformat(w["week"]).strftime("%d/%m") for w in weekly] or ["—"]
    bottom = [0] * len(labels)
    for key in stats.RATINGS:
        values = [w[key] for w in weekly] or [0]
        ax.bar(labels, values, bottom=bottom, color=COLORS[key], label=LABELS[key], width=0.55, edgecolor="white", linewidth=2)
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    ax.set_title("Valoraciones por semana (inicio de semana)", loc="left", color="#0b0b0b")
    ax.legend(frameon=False, ncols=3, loc="upper left", bbox_to_anchor=(0, -0.15))
    _style(ax)
    return _png(fig)


def _delta(current: float, previous: float, pct: bool = False) -> str:
    diff = current - previous
    if pct:
        return f"{diff * 100:+.0f} pp"
    return f"{diff:+.0f}"


def build_report(db: Session, period: Period, tz: ZoneInfo, include_csv: bool) -> OutgoingEmail:
    """Assemble the report email for one month (comments included without phone numbers)."""
    current = stats.compute_stats(db, stats.SurveyFilter(period.first_day, period.last_day), tz)
    prev_period = period.previous()
    previous = stats.compute_stats(db, stats.SurveyFilter(prev_period.first_day, prev_period.last_day), tz)
    dist = current["distribution"]
    good_share = dist["good"] / current["answered"] if current["answered"] else 0.0
    prev_good = previous["distribution"]["good"] / previous["answered"] if previous["answered"] else 0.0

    rows = [
        ("Encuestas enviadas", current["sent"], _delta(current["sent"], previous["sent"])),
        ("Respondidas", current["answered"], _delta(current["answered"], previous["answered"])),
        ("Tasa de respuesta", f"{current['response_rate'] * 100:.0f} %",
         _delta(current["response_rate"], previous["response_rate"], pct=True)),
        ("Valoraciones «Bien»", f"{good_share * 100:.0f} %", _delta(good_share, prev_good, pct=True)),
    ]  # fmt: skip
    table = "".join(
        f"<tr><td>{html.escape(name)}</td><td><strong>{value}</strong></td>"
        f"<td style='color:#52514e'>{delta} vs. {html.escape(prev_period.label)}</td></tr>"
        for name, value, delta in rows
    )
    counts = "".join(f"<li>{LABELS[k]}: {dist[k]}</li>" for k in stats.RATINGS)
    comments = "".join(
        f"<li><span style='color:#52514e'>{c['date']} · {LABELS.get(c['rating'] or '', '—')} · "
        f"{html.escape(c['service'])}</span><br>{html.escape(c['comment'] or '')}</li>"
        for c in current["comments"]
    ) or "<li>Sin comentarios este mes.</li>"
    body = f"""<html><body style="font-family:Arial,sans-serif;color:#0b0b0b;max-width:640px">
<h2>Informe de encuestas · {html.escape(period.label)}</h2>
<table cellpadding="6" style="border-collapse:collapse">{table}</table>
<p><img src="cid:distribution" alt="Valoraciones del mes" width="480"></p>
<ul>{counts}</ul>
<p><img src="cid:weekly" alt="Valoraciones por semana" width="560"></p>
<h3>Comentarios</h3><ul>{comments}</ul>
</body></html>"""
    text = (
        f"Informe de encuestas · {period.label}\n"
        + "\n".join(f"{name}: {value} ({delta})" for name, value, delta in rows)
        + "\n"
        + "\n".join(f"{LABELS[k]}: {dist[k]}" for k in stats.RATINGS)
    )
    attachments: dict[str, tuple[bytes, str]] = {}
    if include_csv:
        csv_text = stats.to_csv(stats.survey_rows(db, stats.SurveyFilter(period.first_day, period.last_day), tz))
        attachments[f"encuestas-{period.year}-{period.month:02d}.csv"] = (csv_text.encode("utf-8"), "text/csv")
    return OutgoingEmail(
        to=[],
        subject=f"Informe de encuestas · {period.label}",
        html=body,
        text=text,
        inline_images={"distribution": distribution_chart(dist), "weekly": weekly_chart(current["weekly"])},
        attachments=attachments,
    )


# --- sending -------------------------------------------------------------------------------


def _ensure_row(db: Session, period: Period, now: datetime) -> MonthlyReport | None:
    row = db.scalar(select(MonthlyReport).where(MonthlyReport.year == period.year, MonthlyReport.month == period.month))
    if row is not None:
        return row
    db.add(MonthlyReport(year=period.year, month=period.month, status=ReportStatus.PENDING, next_attempt_at=now))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # created concurrently
    return db.scalar(select(MonthlyReport).where(MonthlyReport.year == period.year, MonthlyReport.month == period.month))


def _has_history(db: Session, period: Period, tz: ZoneInfo) -> bool:
    """True if any survey was sent before the end of the period (no empty report on first deploy)."""
    end = datetime.combine(period.next().first_day, datetime.min.time(), tzinfo=tz)
    return db.scalar(select(Inspection.id).where(Inspection.sent_at.is_not(None), Inspection.sent_at < end)) is not None


def _send_row(db: Session, row: MonthlyReport, mailer: Mailer, settings: Settings, now: datetime) -> None:
    report_settings = admin_settings.get_report_settings(db, settings)
    tz = admin_settings.get_sending_hours(db, settings).tz
    claimed = db.execute(
        update(MonthlyReport)
        .where(MonthlyReport.id == row.id, MonthlyReport.status == ReportStatus.PENDING)
        .values(status=ReportStatus.SENDING, claimed_at=now)
    )
    db.commit()
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        return
    db.refresh(row)
    period = Period(row.year, row.month)
    row.attempts += 1
    if not report_settings.recipients:
        row.status = ReportStatus.FAILED
        row.last_error = "no recipients configured"
        db.commit()
        return
    email = build_report(db, period, tz, report_settings.include_csv)
    email.to = list(report_settings.recipients)
    row.recipients = email.to
    try:
        mailer.send(email)
    except EmailError as exc:
        row.last_error = str(exc)[:500]
        if exc.retryable and row.attempts < settings.max_report_attempts:
            row.status = ReportStatus.PENDING
            row.next_attempt_at = now + backoff(row.attempts, settings.retry_base_seconds)
        else:
            row.status = ReportStatus.FAILED
        db.commit()
        logger.warning("monthly report %s-%02d not sent: %s", period.year, period.month, exc)
        return
    row.status = ReportStatus.SENT
    row.sent_at = now
    row.last_error = None
    db.commit()
    logger.info("monthly report %s-%02d sent", period.year, period.month)


def run_monthly_report(db: Session, mailer: Mailer, settings: Settings, now: datetime | None = None) -> list[MonthlyReport]:
    """Create the due month's report and send every pending one, each at most once."""
    now = now or utcnow()
    report_settings = admin_settings.get_report_settings(db, settings)
    tz = admin_settings.get_sending_hours(db, settings).tz

    # A crash while sending leaves the outcome unknown: mark it failed (the admin can resend).
    for stale in db.scalars(
        select(MonthlyReport).where(MonthlyReport.status == ReportStatus.SENDING, MonthlyReport.claimed_at < now - STALE_CLAIM)
    ):
        stale.status = ReportStatus.FAILED
        stale.last_error = "worker stopped while sending; not retried automatically"
    db.commit()

    period = due_period(now, report_settings, tz)
    if period is not None and report_settings.recipients and _has_history(db, period, tz):
        _ensure_row(db, period, now)

    pending = list(
        db.scalars(
            select(MonthlyReport).where(
                MonthlyReport.status == ReportStatus.PENDING,
                (MonthlyReport.next_attempt_at.is_(None)) | (MonthlyReport.next_attempt_at <= now),
            )
        )
    )
    for row in pending:
        _send_row(db, row, mailer, settings, now)
    return pending


def request_resend(db: Session, year: int, month: int, user_id: int | None, now: datetime | None = None) -> MonthlyReport:
    """Queue a report to be sent again by the worker (manual resend from the admin section)."""
    now = now or utcnow()
    period = Period(year, month)
    row = _ensure_row(db, period, now)
    assert row is not None
    if row.status == ReportStatus.SENDING:
        return row
    row.status = ReportStatus.PENDING
    row.attempts = 0
    row.next_attempt_at = now
    row.last_error = None
    admin_settings.record_audit(db, user_id, "resend_report", f"{year}-{month:02d}", None, None)
    db.commit()
    return row


def recent_reports(db: Session, limit: int = 12) -> list[MonthlyReport]:
    """Latest report rows, newest first."""
    return list(
        db.scalars(select(MonthlyReport).order_by(MonthlyReport.year.desc(), MonthlyReport.month.desc()).limit(limit))
    )
