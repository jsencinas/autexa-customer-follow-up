"""SQLAlchemy ORM models."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models.enums import (
    ExtractionStatus,
    InboundStatus,
    InspectionStatus,
    ReportStatus,
    Role,
)
from app.timeutils import utcnow


class User(Base):
    """A web app account (employee or admin)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.EMPLOYEE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LoginAttempt(Base):
    """A login attempt, used for rate limiting."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_lookup", "username", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Inspection(Base):
    """An uploaded inspection form and the survey that follows it."""

    __tablename__ = "inspections"
    __table_args__ = (
        Index("ix_inspections_status_scheduled", "status", "scheduled_at"),
        Index("ix_inspections_phone_sent", "customer_phone", "sent_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default=InspectionStatus.PENDING_CONFIRMATION, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Photo
    image_path: Mapped[str | None] = mapped_column(String(512))
    image_uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    image_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Extraction
    extraction_status: Mapped[str] = mapped_column(String(16), default=ExtractionStatus.PENDING, index=True)
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0)
    extraction_next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    extraction_claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    raw_extraction: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    field_confidence: Mapped[dict[str, str] | None] = mapped_column(JSON)

    # Customer data (editable by the employee)
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_phone: Mapped[str | None] = mapped_column(String(20))
    phone_raw: Mapped[str | None] = mapped_column(String(64))
    service_description: Mapped[str | None] = mapped_column(Text)
    service_display: Mapped[str | None] = mapped_column(String(60))
    service_date: Mapped[date | None] = mapped_column(Date)
    extra_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Duplicate detection: set only while the record is active and complete; unique in the DB.
    dedup_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id"))

    # Confirmation
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Sending
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)
    send_claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    template_name: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    wa_message_id: Mapped[str | None] = mapped_column(String(128), index=True)

    # Survey conversation
    survey_stage: Mapped[str | None] = mapped_column(String(32))
    rating: Mapped[str | None] = mapped_column(String(8))
    rating_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rating_source: Mapped[str | None] = mapped_column(String(8))
    comment: Mapped[str | None] = mapped_column(Text)
    comment_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    followup_variant: Mapped[int | None] = mapped_column(Integer)
    followup_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    comment_window_closes_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    thanks_variant: Mapped[int | None] = mapped_column(Integer)
    thanks_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class InboundMessage(Base):
    """A message received from a customer through the webhook, interpreted asynchronously."""

    __tablename__ = "inbound_messages"
    __table_args__ = (Index("ix_inbound_status_next", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    from_phone: Mapped[str] = mapped_column(String(20), index=True)
    message_type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text)
    button_payload: Mapped[str | None] = mapped_column(String(256))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    status: Mapped[str] = mapped_column(String(16), default=InboundStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id"), index=True)
    interpretation_source: Mapped[str | None] = mapped_column(String(8))
    intents: Mapped[list[str] | None] = mapped_column(JSON)
    interpreted_rating: Mapped[str | None] = mapped_column(String(8))
    interpreted_comment: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    model_name: Mapped[str | None] = mapped_column(String(64))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    review_decision: Mapped[str | None] = mapped_column(String(32))


class ContactActivity(Base):
    """Latest conversation activity per customer number (for quiet periods and human hand-off)."""

    __tablename__ = "contact_activity"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_customer_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_employee_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class EmployeeMessage(Base):
    """A message an employee sent by hand from the Business app (from `smb_message_echoes`).

    Only the timestamp is kept: it tells the bot that a human joined the conversation.
    """

    __tablename__ = "employee_messages"
    __table_args__ = (Index("ix_employee_messages_phone_sent", "phone", "sent_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    phone: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime)


class OptOut(Base):
    """A number that must never receive bot messages again. Never removed by retention."""

    __tablename__ = "opt_outs"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    opted_out_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16))
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id"))
    inbound_message_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_messages.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmation_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Setting(Base):
    """An admin-editable setting stored as JSON."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base):
    """Who changed what in the admin section."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    old_value: Mapped[Any] = mapped_column(JSON)
    new_value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MonthlyReport(Base):
    """One monthly email report; the unique (year, month) prevents double sends."""

    __tablename__ = "monthly_reports"
    __table_args__ = (UniqueConstraint("year", "month", name="uq_monthly_reports_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ReportStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    recipients: Mapped[list[str] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


__all__ = [
    "AuditLog",
    "ContactActivity",
    "EmployeeMessage",
    "InboundMessage",
    "Inspection",
    "LoginAttempt",
    "MonthlyReport",
    "OptOut",
    "Setting",
    "User",
]
