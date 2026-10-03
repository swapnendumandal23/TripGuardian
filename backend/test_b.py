import asyncio
from bot import trip_store, proactive_engine
from simulation_engine import sim_engine

async def run_test():
    user_a = 11112222
    user_b = 33334444
    
    # 1. Clear db
    trip_store.clear_trip(user_a)
    trip_store.clear_trip(user_b)
    
    # 2. Save itineraries for both users
    trip_store.save_trip(user_a, "Goa", "Day 1: Arrive via flight AI-101. Rest.", flight_number="AI-101")
    trip_store.save_trip(user_b, "Goa", "Day 1: Arrive via flight 6E-202. Visit beach.", flight_number="6E-202")
    
    print("=== TEST B: MULTI-USER DISRUPTION ALERT ===")
    print(f"User A ({user_a}): Flight AI-101")
    print(f"User B ({user_b}): Flight 6E-202")
    
    # 3. Create simulated disruption for User A
    print("\n[Admin] Triggering 60 min delay for flight AI-101 (User A)...")
    sim_id = sim_engine.create_simulation(
        "flight_delay",
        {
            "flight_number": "AI-101",
            "delay_minutes": 60,
            "reason": "Air traffic congestion"
        },
        user_a # Target User A
    )
    
    # 4. We will patch proactive_engine._throttled_send to see who receives alerts
    alerts_sent = []
    original_send = proactive_engine._throttled_send
    
    def mock_send(chat_id, alert_type, key, message):
        alerts_sent.append((chat_id, alert_type))
        print(f"\n[ALERT SENT TO {chat_id}] {alert_type}")
        print(f"Message: {message}\n")
        # Returning True simulates successful send
        return True
        
    proactive_engine._throttled_send = mock_send
    
    # 5. Run the disruption checker
    print("\n[System] Running proactive engine check_flight_disruptions()...")
    proactive_engine.check_flight_disruptions()
    
    # 6. Verify
    print("\n--- Verification ---")
    if any(a[0] == user_a for a in alerts_sent):
        print("✅ User A received an alert.")
    else:
        print("❌ User A did NOT receive an alert.")
        
    if any(a[0] == user_b for a in alerts_sent):
        print("❌ User B mistakenly received an alert.")
    else:
        print("✅ User B did NOT receive an alert.")
        
    proactive_engine._throttled_send = original_send

if __name__ == "__main__":
    asyncio.run(run_test())
