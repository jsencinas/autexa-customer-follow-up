"""Worker heartbeat, so the web app can tell when the background worker is not running."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.db_models import Setting
from app.timeutils import utcnow

HEARTBEAT_KEY = "worker_heartbeat"
# The worker beats every tick (a few seconds); allow for slow ticks such as a long extraction.
MAX_SILENCE = timedelta(minutes=3)


def beat(db: Session, now: datetime | None = None) -> None:
    """Record that the worker is alive."""
    stamp = (now or utcnow()).isoformat()
    row = db.get(Setting, HEARTBEAT_KEY)
    if row is None:
        db.add(Setting(key=HEARTBEAT_KEY, value={"at": stamp}))
    else:
        row.value = {"at": stamp}
    db.commit()


def last_beat(db: Session) -> datetime | None:
    """When the worker last reported in, if ever."""
    row = db.get(Setting, HEARTBEAT_KEY)
    if row is None or not isinstance(row.value, dict) or "at" not in row.value:
        return None
    return datetime.fromisoformat(row.value["at"])


def worker_alive(db: Session, now: datetime | None = None) -> bool:
    """True if the worker reported in recently."""
    last = last_beat(db)
    return last is not None and (now or utcnow()) - last <= MAX_SILENCE
