import os
import sys
from datetime import datetime

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from trip_store import TripStore

def run_test_pref():
    print("\n" + "=" * 70)
    print("[TEST 31] BUG-32 Preference Persistence After Restart")
    print("=" * 70)

    db_path = "test_pref.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    chat_id = 12345

    # 1. Start application and set preference
    store1 = TripStore(db_path)
    store1.update_user_preferences(chat_id, "vegan, loves museums, moderate budget")
    
    # Simulate restart by destroying instance
    del store1

    # 2. Restart application and verify preference is retained
    store2 = TripStore(db_path)
    prefs = store2.get_user_preferences(chat_id)
    
    assert prefs == "vegan, loves museums, moderate budget", f"Expected preferences not found! Got: {prefs}"

    # 3. Simulate another preference change
    store2.update_user_preferences(chat_id, "vegan, loves museums, high budget, hates outdoors")
    
    # 4. Verify again
    del store2
    store3 = TripStore(db_path)
    prefs_updated = store3.get_user_preferences(chat_id)
    assert prefs_updated == "vegan, loves museums, high budget, hates outdoors", "Updated preferences not retained!"

    if os.path.exists(db_path):
        os.remove(db_path)
        
    print("  >>> TEST 31 PASSED: User preferences are durable and persist across application restarts.")

if __name__ == "__main__":
    run_test_pref()
