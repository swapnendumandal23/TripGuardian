import sys
import os
import time

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, ".env"))

import requests
from unittest.mock import patch
from weather_service import fetch_live_weather, check_incoming_rain_forecast, clear_weather_cache
from traffic_service import calculate_live_route, clear_traffic_cache
from places_service import discover_live_places, clear_places_cache

print("\n" + "=" * 70)
print("[TEST 28] BUG-29 External API Outage / Graceful Degradation")
print("=" * 70)

def _mock_requests_get_fail(*args, **kwargs):
    raise requests.exceptions.ConnectionError("Mocked Connection Error")
def _mock_requests_post_fail(*args, **kwargs):
    raise requests.exceptions.Timeout("Mocked Timeout Error")

try:
    # 1. Test Weather Service Fallback
    clear_weather_cache()
    with patch("requests.get", side_effect=_mock_requests_get_fail):
        w_fallback = fetch_live_weather("Goa", lat=15.49, lon=73.82)
        radar_fallback = check_incoming_rain_forecast("Goa", lat=15.49, lon=73.82)

    assert w_fallback["status"] == "error", "Weather should return error status on failure"
    assert w_fallback["weather_tier"] == "clear", "Weather fallback tier must be clear to avoid panic replanning"
    assert not radar_fallback["incoming_rain"], "Radar fallback must default to no rain"

    # 2. Test Traffic Service Fallback
    clear_traffic_cache()
    with patch("requests.get", side_effect=_mock_requests_get_fail):
        t_fallback = calculate_live_route(15.38, 73.83, 15.50, 73.77)

    assert t_fallback["status"] == "estimated", "Traffic should return estimated status on failure"
    assert t_fallback["distance_km"] > 0, "Traffic fallback must calculate haversine distance"
    assert t_fallback["duration_mins"] > 0, "Traffic fallback must calculate estimated duration"

    # 3. Test Places Service Fallback
    clear_places_cache()
    with patch("requests.post", side_effect=_mock_requests_post_fail):
        p_fallback = discover_live_places("Goa", category="cafe", radius_meters=5000, lat=15.49, lon=73.82)

    assert len(p_fallback) == 2, "Places should return 2 generic fallback items on failure"
    assert p_fallback[0]["distance_km"] == 1.5, "Places fallback must include default distances"

    print("  >>> TEST 28 PASSED: System gracefully degrades and returns useful internal fallbacks when providers are down.")

except Exception as e:
    print(f"Test Failed: {e}")
    sys.exit(1)
