import sys
import os
import time
from datetime import datetime, timedelta
import sqlite3

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from trip_store import TripStore
from scheduler import ProactiveEngine

def run_test_30():
    print("\n" + "=" * 70)
    print("[TEST 30] BUG-31 Proactive recommendations stop after trip ends")
    print("=" * 70)

    store = TripStore("test_30.db")
    store._init_db()

    chat_id = 99930

    # 1. Create a trip that ended 2 hours ago
    now = datetime.now()
    two_hours_ago = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    start_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")

    store.save_trip_v2(
        chat_id=chat_id,
        destination="Past Trip",
        itinerary="Past activities",
        trip_start_date=start_date,
        trip_end_date=two_hours_ago,
        status="active"
    )

    # Verify the trip gets marked as completed
    store.refresh_trip_statuses()
    
    trip = store.get_trip(chat_id)
    assert trip is not None, "Trip should still exist in database"
    assert trip["status"] == "completed", f"Trip status should be 'completed', but got '{trip['status']}'"

    # Verify it doesn't appear in active trips for scheduler
    active_trips = store.get_all_active_trips()
    assert not any(t["chat_id"] == chat_id for t in active_trips), "Completed trip must not appear in active trips list for scheduler"

    # Clean up test DB
    os.remove("test_30.db")
    
    print("  >>> TEST 30 PASSED: Trips ending exactly at a specific time are correctly marked as completed and excluded from proactive loops.")

if __name__ == "__main__":
    run_test_30()
