"""String enums shared by the ORM models, schemas and services."""

from enum import StrEnum


class Role(StrEnum):
    """Web app user role."""

    EMPLOYEE = "employee"
    ADMIN = "admin"


class InspectionStatus(StrEnum):
    """Lifecycle of an inspection and its survey."""

    PENDING_CONFIRMATION = "pending_confirmation"
    SCHEDULED = "scheduled"
    SENT = "sent"
    ANSWERED = "answered"
    EXPIRED = "expired"
    FAILED = "failed"
    DISCARDED = "discarded"
    OPTED_OUT = "opted_out"


# Statuses that block a duplicate resubmission of the same inspection.
ACTIVE_STATUSES: frozenset[InspectionStatus] = frozenset(
    {
        InspectionStatus.PENDING_CONFIRMATION,
        InspectionStatus.SCHEDULED,
        InspectionStatus.SENT,
        InspectionStatus.ANSWERED,
    }
)


class ExtractionStatus(StrEnum):
    """State of the photo extraction step."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    UNREADABLE = "unreadable"
    FAILED = "failed"


class SurveyStage(StrEnum):
    """Where a sent survey is in the conversation."""

    AWAITING_RATING = "awaiting_rating"
    AWAITING_COMMENT = "awaiting_comment"
    COMPLETED = "completed"


class Rating(StrEnum):
    """Survey rating values."""

    GOOD = "good"
    OKAY = "okay"
    BAD = "bad"


class InboundStatus(StrEnum):
    """Processing state of an inbound customer message."""

    PENDING = "pending"
    DONE = "done"
    IGNORED = "ignored"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class InterpretationSource(StrEnum):
    """Who decided what an inbound message meant."""

    RULE = "rule"
    LLM = "llm"
    ADMIN = "admin"


class OptOutSource(StrEnum):
    """How a number ended up on the opt-out list."""

    BUTTON = "button"
    INTERPRETED = "interpreted"
    ADMIN = "admin"


class ReportStatus(StrEnum):
    """State of a monthly email report."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
