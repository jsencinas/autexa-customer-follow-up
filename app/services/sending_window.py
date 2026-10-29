"""When surveys may be sent: weekly windows in the business timezone."""

from datetime import datetime, timedelta

from app.config import WEEKDAYS
from app.services.admin_settings import SendingHours
from app.timeutils import ensure_utc

# Look a little more than one week ahead so every weekday is considered.
_LOOKAHEAD_DAYS = 8


def is_open(moment: datetime, hours: SendingHours) -> bool:
    """True if `moment` falls inside an enabled window."""
    local = ensure_utc(moment).astimezone(hours.tz)
    day = hours.days[WEEKDAYS[local.weekday()]]
    return day.enabled and day.start <= local.time() < day.end


def next_opening(moment: datetime, hours: SendingHours) -> datetime | None:
    """`moment` itself if the window is open, otherwise the next opening time (UTC).

    Returns None when no weekday is enabled.
    """
    moment = ensure_utc(moment)
    if is_open(moment, hours):
        return moment
    tz = hours.tz
    local = moment.astimezone(tz)
    for offset in range(_LOOKAHEAD_DAYS):
        day_date = (local + timedelta(days=offset)).date()
        day = hours.days[WEEKDAYS[day_date.weekday()]]
        if not day.enabled:
            continue
        start = datetime.combine(day_date, day.start, tzinfo=tz)
        if start > local:
            return start.astimezone(moment.tzinfo)
    return None
