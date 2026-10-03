import re
from datetime import datetime, timedelta, timezone

def parse_time_to_minutes(time_str: str) -> int:
    """
    Robustly parses 12-hour and 24-hour time strings and returns minutes past midnight.
    Handles '10:00 PM', '14:30', '08:00 AM', '2 PM', '14:30:00'.
    """
    if not time_str:
        return 0
    time_str = str(time_str).strip().upper()
    try:
        if "PM" in time_str or "AM" in time_str:
            # Handle '2 PM' -> '2:00 PM'
            if ":" not in time_str:
                time_str = re.sub(r'(\d+)\s*(AM|PM)', r'\1:00 \2', time_str)
            t = datetime.strptime(time_str, "%I:%M %p")
        else:
            # Handle HH:MM:SS
            if time_str.count(":") == 2:
                t = datetime.strptime(time_str, "%H:%M:%S")
            else:
                t = datetime.strptime(time_str, "%H:%M")
        return t.hour * 60 + t.minute
    except ValueError:
        pass
    
    # Fallback to regex
    match = re.search(r'(\d{1,2}):(\d{2})', time_str)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if "PM" in time_str and h < 12: h += 12
        if "AM" in time_str and h == 12: h = 0
        return h * 60 + m
    
    return 0

def normalize_datetime_string(dt_str: str, tz_offset_hours: float = 0.0) -> str:
    """
    Normalizes a datetime string to ISO-8601 with timezone offset.
    E.g. '2026-10-03 14:30' -> '2026-10-03T14:30:00+05:30'
    Preserves existing timezone offsets if present.
    """
    if not dt_str:
        return ""
    try:
        # Check if already has timezone offset (+/- HH:MM)
        if re.search(r'[+-]\d{2}:\d{2}$', dt_str) or dt_str.endswith("Z"):
            if dt_str.endswith("Z"):
                dt_str = dt_str[:-1] + "+00:00"
            return dt_str.replace(" ", "T")
        
        # Parse common formats
        dt = None
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]:
            try:
                dt = datetime.strptime(dt_str[:19], fmt)
                break
            except ValueError:
                continue
                
        if dt:
            offset_timedelta = timedelta(hours=tz_offset_hours)
            tz = timezone(offset_timedelta)
            dt = dt.replace(tzinfo=tz)
            return dt.isoformat()
    except Exception:
        pass
    return dt_str # return original if parsing fails

def parse_iso_datetime(iso_str: str) -> datetime:
    """
    Parses an ISO-8601 string (with or without timezone) into an aware datetime object.
    Falls back to local time if no timezone is provided.
    """
    if not iso_str:
        return datetime.now()
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            # Assume local if timezone naive
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except ValueError:
        return datetime.now()

