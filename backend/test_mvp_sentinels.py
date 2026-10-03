import os
import sys
import time

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, ".env"))

print("=" * 70)
print("TESTING MVP AUTONOMOUS SENTINEL NOTIFICATIONS")
print("=" * 70)

from trip_store import trip_store
from scheduler import proactive_engine

test_chat = 99887766
trip_store.save_trip(
    chat_id=test_chat,
    destination="Goa",
    itinerary="Day 1: Anjuna Beach, Sunset Cruise",
    hotel_name="Taj Holiday Village Resort & Spa",
    hotel_checkin_time="14:00",
    flight_arrival_time="13:00"
)

# 1. Early Landing + Hotel Check-in Buffer Sentinel
print("\n[SENTINEL 1] 1-Hour Early Landing + Nearby Luggage-Friendly Cafes:")
msg1 = proactive_engine.check_landing_buffer_alerts(force_chat_id=test_chat, simulate=True)
print("  Generated Notification:")
print("  " + "\n  ".join(msg1.split("\n")[:7]))
assert "Touchdown Confirmed" in msg1
assert "check-in is in *1 hour*" in msg1
print("  >>> SENTINEL 1 PASSED: Early landing buffer cafe alert operational.")

# 2. Pre-emptive Incoming Rain Radar (35 mins out)
print("\n[SENTINEL 2] Incoming Rain Radar Pre-emptive Alert:")
msg2 = proactive_engine.check_incoming_rain_alerts(force_chat_id=test_chat, simulate=True)
print("  Generated Notification:")
print("  " + "\n  ".join(msg2.split("\n")[:7]))
assert "Incoming Rain Advisory" in msg2
assert "35" in msg2
print("  >>> SENTINEL 2 PASSED: Pre-emptive rain radar alert operational.")

# 3. Live Route Traffic & Congestion Sentinel (OSRM)
print("\n[SENTINEL 3] Live Route Traffic & Congestion Advisory (OSRM):")
msg3 = proactive_engine.check_traffic_alerts(force_chat_id=test_chat, simulate=True)
print("  Generated Notification:")
print("  " + "\n  ".join(msg3.split("\n")[:7]))
assert "Live Traffic Advisory" in msg3
assert "Delay" in msg3
print("  >>> SENTINEL 3 PASSED: Traffic congestion alert operational.")

print("\n" + "=" * 70)
print("ALL 3 MVP AUTONOMOUS SENTINEL TESTS PASSED! ✅")
print("=" * 70)
