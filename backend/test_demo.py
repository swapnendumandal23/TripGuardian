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

    print("=== TEST C ===")
    
    # Baga Beach
    trip_store.update_demo_location(chat_id, 15.5523, 73.7517, is_demo=True)
    print("\n--- 1. Demo Location: Baga Beach ---")
    res1 = execute_discovery(chat_id, "Find seafood nearby", "seafood", None, None, "Goa", trip)
    print(res1)
    
    # Panjim
    trip_store.update_demo_location(chat_id, 15.4909, 73.8278, is_demo=True)
    print("\n--- 2. Demo Location: Panjim ---")
    res2 = execute_discovery(chat_id, "Find seafood nearby", "seafood", None, None, "Goa", trip)
    print(res2)

    print("\n=== TEST DEMO LANDING ===")
    # Goa Airport
    trip_store.update_demo_location(chat_id, 15.3803, 73.8350, is_demo=True)
    print("\n--- Demo Location: Goa Airport ---")
    res3 = execute_discovery(chat_id, "I just landed early. What can I do nearby?", "attraction", None, None, "Goa", trip)
    print(res3)

if __name__ == "__main__":
    asyncio.run(run_test())
