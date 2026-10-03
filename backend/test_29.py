import sys
import os

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from datetime_utils import parse_time_to_minutes, normalize_datetime_string, parse_iso_datetime

# 1. Test 12-hour / 24-hour time formats
assert parse_time_to_minutes("14:30") == 14 * 60 + 30, "Failed to parse 24-hour time"
assert parse_time_to_minutes("02:30 PM") == 14 * 60 + 30, "Failed to parse 12-hour PM time"
assert parse_time_to_minutes("02:30 AM") == 2 * 60 + 30, "Failed to parse 12-hour AM time"
assert parse_time_to_minutes("2 PM") == 14 * 60, "Failed to parse short PM time"
assert parse_time_to_minutes("12:00 AM") == 0, "Failed to parse midnight (12:00 AM)"
assert parse_time_to_minutes("12:00 PM") == 12 * 60, "Failed to parse noon (12:00 PM)"
assert parse_time_to_minutes("14:30:45") == 14 * 60 + 30, "Failed to parse time with seconds"
assert parse_time_to_minutes("invalid time") == 0, "Failed to gracefully handle invalid time"

# 2. Test ISO string normalization & timezone handling
dt1 = normalize_datetime_string("2026-10-03 14:30:00", tz_offset_hours=5.5)
assert dt1 == "2026-10-03T14:30:00+05:30", f"Failed to normalize and apply +05:30 offset, got {dt1}"

dt2 = normalize_datetime_string("2026-10-03T14:30:00+02:00", tz_offset_hours=5.5)
assert dt2 == "2026-10-03T14:30:00+02:00", f"Failed to preserve existing offset, got {dt2}"

dt3 = normalize_datetime_string("2026-10-03T14:30:00Z")
assert dt3 == "2026-10-03T14:30:00+00:00", "Failed to normalize Z timezone to +00:00"

# 3. Test ISO parsing back to datetime
p_dt1 = parse_iso_datetime("2026-10-03T14:30:00+05:30")
assert p_dt1.hour == 14, "Failed to parse ISO datetime hour"
assert p_dt1.tzinfo is not None, "Parsed ISO datetime is missing tzinfo"

print("TEST 29 PASSED: Robust time parsing, ISO normalization, and timezone resolution work deterministically.")
