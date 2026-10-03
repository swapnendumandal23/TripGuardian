import asyncio
import sys
sys.path.append('.')

from trip_store import trip_store
from bot import execute_discovery

async def run_test():
    chat_id = 99999999
    trip_store.clear_trip(chat_id)
    trip_store.save_trip(chat_id, "Goa", "Test itinerary")
    trip = trip_store.get_trip(chat_id)

    print("--- 1. Sharing Location A (15.5494, 73.7535) ---")
    trip_store.update_location(chat_id, 15.5494, 73.7535)
    
    print("--- 2. Asking 'Find seafood nearby' ---")
    res1 = execute_discovery(chat_id, "Find seafood nearby", "seafood", None, None, "Goa", trip)
    print(res1)
    
    print("\n--- 3. Sharing Location B (15.2993, 74.1240) ---")
    trip_store.update_location(chat_id, 15.2993, 74.1240)
    
    print("--- 4. Asking 'Find seafood nearby' ---")
    res2 = execute_discovery(chat_id, "Find seafood nearby", "seafood", None, None, "Goa", trip)
    print(res2)

    if res1 == res2:
        print("\nTEST C FAILED: The results are identical!")
    else:
        print("\nTEST C PASSED: The results changed based on location!")

if __name__ == "__main__":
    asyncio.run(run_test())
