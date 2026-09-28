from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.config import settings

def get_next_business_time(dt: datetime) -> datetime:
    """
    Takes a timezone-aware datetime and rolls it forward to the next business hour 
    if it's outside the configured business hours.
    """
    tz = ZoneInfo(settings.timezone)
    dt_tz = dt.astimezone(tz)
    
    start_hour, start_min = map(int, settings.business_hours_start.split(':'))
    end_hour, end_min = map(int, settings.business_hours_end.split(':'))
    valid_days = [int(d) for d in settings.business_days.split(',')]
    
    while True:
        # Check day of week
        if dt_tz.weekday() not in valid_days:
            # Move to next day at start time
            dt_tz += timedelta(days=1)
            dt_tz = dt_tz.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            continue
            
        # Check time of day
        current_time = dt_tz.time()
        start_time = dt_tz.replace(hour=start_hour, minute=start_min, second=0, microsecond=0).time()
        end_time = dt_tz.replace(hour=end_hour, minute=end_min, second=0, microsecond=0).time()
        
        if current_time < start_time:
            # Too early today, fast forward to start time
            dt_tz = dt_tz.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            return dt_tz
        elif current_time > end_time:
            # Too late today, move to start time tomorrow
            dt_tz += timedelta(days=1)
            dt_tz = dt_tz.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            continue
        
        # Valid business time
        return dt_tz

def calculate_schedule_time(base_time_utc: datetime) -> datetime:
    """
    Adds the survey delay and ensures the result falls within business hours.
    Returns a naive datetime representing UTC (for DB storage).
    """
    # Force base time to be timezone aware UTC if it's naive
    if base_time_utc.tzinfo is None:
        base_time_utc = base_time_utc.replace(tzinfo=ZoneInfo("UTC"))
        
    target = base_time_utc + timedelta(hours=settings.survey_delay_hours)
    tz = ZoneInfo(settings.timezone)
    target_tz = target.astimezone(tz)
    
    valid_tz = get_next_business_time(target_tz)
    
    # Convert back to naive UTC for DB storage compatibility
    return valid_tz.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
