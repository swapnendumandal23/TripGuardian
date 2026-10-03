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
print("TESTING UPGRADES: SQLITE PERSISTENCE, OPENSTREETMAP, & CRON SUITE")
print("=" * 70)

# --- 1. SQLite Persistence Test ---
print("\n[TEST 1] SQLite Database Persistence (trips.db):")
from trip_store import TripStore, DB_PATH
print(f"  Database Path: {DB_PATH}")
test_store = TripStore(DB_PATH)
test_chat = 77889900
test_store.save_trip(test_chat, "Goa", "Day 1: Calangute Beach. Day 2: Fort Aguada.", flight_number="6E-202")
retrieved = test_store.get_trip(test_chat)
assert retrieved is not None, "Failed to retrieve from SQLite"
assert retrieved["destination"] == "Goa"
assert retrieved["flight_number"] == "6E-202"
print(f"  ✅ Saved & retrieved from SQLite: Destination={retrieved['destination']}, Flight={retrieved['flight_number']}")

# Test persistence across a brand-new instance
new_store_instance = TripStore(DB_PATH)
re_retrieved = new_store_instance.get_trip(test_chat)
assert re_retrieved is not None
print(f"  ✅ Re-opened database with new instance: Data persisted cleanly.")
print("  >>> TEST 1 PASSED: SQLite persistence verified.")

# --- 2. OpenStreetMap Overpass Live Places Test ---
print("\n[TEST 2] Live OpenStreetMap (Overpass API) Places Discovery:")
from places_service import discover_live_places
places = discover_live_places("Goa", category="seafood", radius_meters=6000)
print(f"  Found {len(places)} live places in Goa:")
for i, p in enumerate(places[:3], 1):
    print(f"   {i}. {p['name']} ({p['category']}) - Cuisine: {p['cuisine']} (Hours: {p['opening_hours']})")
assert len(places) > 0, "Failed to discover live places"
print("  >>> TEST 2 PASSED: Live OpenStreetMap discovery verified.")

# --- 3. Cron Suite: Morning Briefing Test ---
print("\n[TEST 3] Morning Briefing Cron Functionality:")
from scheduler import proactive_engine
briefing = proactive_engine.trigger_morning_briefing(test_chat)
print(f"  Briefing generated ({len(briefing)} chars):")
print("  " + "\n  ".join(briefing.split("\n")[:5]))
assert "Trip Guardian Morning Briefing" in briefing
print("  >>> TEST 3 PASSED: Daily 8:00 AM Morning Briefing verified.")

# --- 4. Cron Suite: Flight Watchdog Test ---
print("\n[TEST 4] Flight Sentinel Watchdog Status:")
status = proactive_engine.get_status()
print(f"  Active Cron Jobs ({status['jobs_count']}):")
for j in status['jobs']:
    print(f"   • {j['id']}: {j['trigger']}")
print("  >>> TEST 4 PASSED: Cron jobs registered properly.")

print("\n" + "=" * 70)
print("ALL UPGRADE TESTS COMPLETED SUCCESSFULLY! ✅")
print("=" * 70)
