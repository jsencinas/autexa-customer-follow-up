"""The opt-out list: numbers that must never receive bot messages again."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import OptOut


def is_opted_out(db: Session, phone: str | None) -> bool:
    """True if the (E.164) number is on the opt-out list."""
    if not phone:
        return False
    return db.scalar(select(OptOut.id).where(OptOut.phone == phone)) is not None
