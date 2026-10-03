import sys
import os
import time

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure backend directory is on sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

print("=" * 70)
print("1. TESTING OPEN-METEO LIVE WEATHER SERVICE")
print("=" * 70)

from weather_service import fetch_live_weather, create_simulated_weather_disruption

try:
    print("\n[Weather] Fetching live Open-Meteo weather for 'Goa'...")
    goa_weather = fetch_live_weather("Goa")
    print(f"  Status: {goa_weather.get('status')}")
    print(f"  Destination: {goa_weather.get('destination')}")
    print(f"  Condition: {goa_weather.get('condition')}")
    print(f"  Temperature: {goa_weather.get('temperature_c')}°C")
    print(f"  Precipitation: {goa_weather.get('precipitation_mm')} mm")
    print(f"  Wind Speed: {goa_weather.get('wind_speed_kmh')} km/h")
    print(f"  Is Disrupted: {goa_weather.get('is_disrupted')}")
    assert goa_weather.get("status") == "success"

    print("\n[Weather] Fetching live Open-Meteo weather for 'Jaipur'...")
    jaipur_weather = fetch_live_weather("Jaipur")
    print(f"  Condition: {jaipur_weather.get('condition')}, Temp: {jaipur_weather.get('temperature_c')}°C")
    assert jaipur_weather.get("status") == "success"

    sim_disruption = create_simulated_weather_disruption("Goa")
    print(f"\n[Weather] Simulated Disruption Generator: {sim_disruption['name']} ({sim_disruption['message']})")
    assert sim_disruption["type"] == "weather"
    print("\n>>> Open-Meteo Weather Service: PASSED!")
except Exception as e:
    print(f"\n>>> Weather Service FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("2. TESTING TRIP STORE & DEDUPLICATION")
print("=" * 70)

from trip_store import trip_store

try:
    test_chat_id = 987654321
    trip_store.save_trip(test_chat_id, "Jaipur", "Day 1: Hawa Mahal, City Palace, Chokhi Dhani.")
    trip = trip_store.get_trip(test_chat_id)
    assert trip is not None
    assert trip["destination"] == "Jaipur"
    print(f"  Saved trip destination: {trip['destination']}")

    # Deduplication test
    assert trip_store.should_send_alert(test_chat_id, "storm_1") is True
    trip_store.mark_alert_sent(test_chat_id, "storm_1")
    assert trip_store.should_send_alert(test_chat_id, "storm_1") is False
    assert trip_store.should_send_alert(test_chat_id, "storm_2") is True
    print("  Alert deduplication logic verified: PASSED")

    # Update itinerary test
    trip_store.update_itinerary(test_chat_id, "Day 1: Albert Hall Museum (Indoor)")
    updated = trip_store.get_trip(test_chat_id)
    assert "Albert Hall" in updated["itinerary"]
    print("  Itinerary update in place verified: PASSED")
    print("\n>>> Trip Store: PASSED!")
except Exception as e:
    print(f"\n>>> Trip Store FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("3. TESTING APSCHEDULER PROACTIVE ENGINE")
print("=" * 70)

from scheduler import proactive_engine

try:
    print("\n[Scheduler] Starting APScheduler...")
    proactive_engine.start(weather_poll_minutes=5)
    status = proactive_engine.get_status()
    print(f"  Scheduler Active: {status['is_running']}")
    print(f"  Jobs count: {status['jobs_count']}")
    for j in status['jobs']:
        print(f"    - Job ID: {j['id']}, Trigger: {j['trigger']}")
    assert status['is_running'] is True

    print("\n[Scheduler] Testing manual disruption trigger (scenario: rain_approaching)...")
    res = proactive_engine.trigger_manual_disruption(test_chat_id, "rain_approaching")
    print(f"  Trigger Status: {res.get('status')}")
    print(f"  Event: {res.get('event')}")
    print(f"  Revised Plan snippet: {res.get('revised_plan')[:100]}...")
    assert res.get("status") == "success"

    print("\n[Scheduler] Running polling pass check_weather_disruptions()...")
    proactive_engine.check_weather_disruptions()
    print("  Polling pass executed successfully.")

    proactive_engine.stop()
    print("  Scheduler stopped successfully.")
    print("\n>>> Proactive Engine: PASSED!")
except Exception as e:
    print(f"\n>>> Proactive Engine FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("4. TESTING TELEGRAM BOT INITIALIZATION & HANDLERS")
print("=" * 70)

from bot import (
    create_bot_application,
    start_command,
    help_command,
    check_weather_command,
    simulate_command
)

try:
    print("  Checking bot command handlers structure...")
    assert callable(start_command)
    assert callable(help_command)
    assert callable(check_weather_command)
    assert callable(simulate_command)
    print("  Bot handlers loaded: start, help, weather, simulate, plan, chat.")
    print("\n>>> Telegram Bot Handlers: PASSED!")
except Exception as e:
    print(f"\n>>> Telegram Bot FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("5. TESTING FASTAPI BACKEND WITH INTEGRATED ENGINE")
print("=" * 70)

from fastapi.testclient import TestClient
from main import app

try:
    with TestClient(app) as client:
        # 1. Health
        h = client.get("/api/health")
        print(f"  [GET /api/health]: {h.status_code} -> {h.json()}")
        assert h.status_code == 200

        # 2. Weather
        w = client.get("/api/weather/Goa")
        print(f"  [GET /api/weather/Goa]: {w.status_code} -> {w.json().get('condition')}, {w.json().get('temperature_c')}°C")
        assert w.status_code == 200

        # 3. Plan Trip
        p = client.post("/api/plan", json={
            "chat_id": 55555,
            "destination": "Goa",
            "dates": "3 days",
            "budget": "Moderate",
            "interests": "Beaches, seafood"
        })
        print(f"  [POST /api/plan]: {p.status_code} -> Trip created for {p.json().get('destination')}")
        assert p.status_code == 200

        # 4. Get Trip
        t = client.get("/api/trip/55555")
        print(f"  [GET /api/trip/55555]: {t.status_code} -> Retrieved saved destination: {t.json().get('destination')}")
        assert t.status_code == 200

        # 5. Trigger Disruption Alert
        a = client.post("/api/trigger-alert", json={"scenario": "rain_approaching", "chat_id": 55555})
        print(f"  [POST /api/trigger-alert]: {a.status_code} -> Event: {a.json().get('event')}")
        assert a.status_code == 200

        # 6. Force Poll
        poll = client.post("/api/poll-now")
        print(f"  [POST /api/poll-now]: {poll.status_code} -> {poll.json().get('message')}")
        assert poll.status_code == 200

        # 7. Check Jobs
        j = client.get("/api/jobs")
        print(f"  [GET /api/jobs]: {j.status_code} -> Active jobs count: {j.json().get('jobs_count')}")
        assert j.status_code == 200

    print("\n>>> FastAPI Endpoints: PASSED!")
except Exception as e:
    print(f"\n>>> FastAPI Backend FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("ALL PROACTIVE ENGINE & TELEGRAM INTEGRATION TESTS COMPLETE")
print("=" * 70)
