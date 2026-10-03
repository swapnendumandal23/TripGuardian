import requests
from typing import Dict, Any, Optional

# Supported destination coordinates (latitude, longitude)
DESTINATION_COORDINATES = {
    "goa": {"lat": 15.4909, "lon": 73.8278, "name": "Goa"},
    "north goa": {"lat": 15.5527, "lon": 73.7517, "name": "North Goa"},
    "south goa": {"lat": 15.2832, "lon": 73.9680, "name": "South Goa"},
    "jaipur": {"lat": 26.9124, "lon": 75.7873, "name": "Jaipur"},
    "mumbai": {"lat": 19.0760, "lon": 72.8777, "name": "Mumbai"},
    "kochi": {"lat": 9.9312, "lon": 76.2673, "name": "Kochi, Kerala"},
    "kerala": {"lat": 9.9312, "lon": 76.2673, "name": "Kerala"},
}

WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail"
}

# ==============================================================================
# WEATHER THRESHOLDS CONFIGURATION
# ==============================================================================
# Explicitly separates informational advisory notifications from disruptive replanning:
# - ADVISORY: Notifies the traveler (e.g. umbrella reminder, light drizzle), preserving existing activities.
# - REPLANNING: Triggers autonomous itinerary replanning (indoor alternatives) for severe/hazardous conditions.
WEATHER_THRESHOLDS = {
    # Precipitation in mm/hr
    "ADVISORY_PRECIPITATION_MM": 1.5,       # >= 1.5 mm: advisory notification sent
    "REPLANNING_PRECIPITATION_MM": 7.5,     # >= 7.5 mm: heavy rain triggers automatic replanning
    
    # Wind speed in km/h
    "ADVISORY_WIND_SPEED_KMH": 35.0,        # >= 35 km/h: high wind advisory
    "REPLANNING_WIND_SPEED_KMH": 55.0,      # >= 55 km/h: severe gales trigger automatic replanning
    
    # WMO Weather Codes for Advisory (Notification only, NO itinerary changes)
    # 51: Light drizzle, 53: Moderate drizzle, 55: Dense drizzle,
    # 61: Slight rain, 63: Moderate rain, 80: Slight rain showers, 81: Moderate rain showers
    "ADVISORY_WEATHER_CODES": {51, 53, 55, 61, 63, 80, 81},
    
    # WMO Weather Codes for Severe (Triggers automatic schedule replanning)
    # 65: Heavy rain, 82: Violent rain showers,
    # 95: Thunderstorm, 96: Thunderstorm w/ slight hail, 99: Thunderstorm w/ heavy hail
    "REPLANNING_WEATHER_CODES": {65, 82, 95, 96, 99}
}

SEVERITY_WEATHER_CODES = WEATHER_THRESHOLDS["REPLANNING_WEATHER_CODES"]

def classify_weather_severity(weather_code: int = 0, precipitation: float = 0.0, wind_speed: float = 0.0) -> str:
    """
    Classifies weather into one of three distinct tiers:
    - 'clear': Insignificant weather change; no notification, no replanning.
    - 'advisory': Light/moderate rain or wind; sends notification only, preserves activities.
    - 'severe': Heavy rain, storm, or dangerous winds; sends alert and triggers automatic replanning.
    """
    try:
        w_code = int(weather_code)
        precip = float(precipitation)
        wind = float(wind_speed)
    except (ValueError, TypeError):
        w_code, precip, wind = 0, 0.0, 0.0

    # 1. Severe Disruptive Weather (triggers autonomous schedule replanning)
    if (w_code in WEATHER_THRESHOLDS["REPLANNING_WEATHER_CODES"] or 
        precip >= WEATHER_THRESHOLDS["REPLANNING_PRECIPITATION_MM"] or 
        wind >= WEATHER_THRESHOLDS["REPLANNING_WIND_SPEED_KMH"]):
        return "severe"

    # 2. Informational Advisory Weather (notification only, NO activity alterations)
    if (w_code in WEATHER_THRESHOLDS["ADVISORY_WEATHER_CODES"] or 
        precip >= WEATHER_THRESHOLDS["ADVISORY_PRECIPITATION_MM"] or 
        wind >= WEATHER_THRESHOLDS["ADVISORY_WIND_SPEED_KMH"]):
        return "advisory"

    # 3. Insignificant / Clear Weather
    return "clear"

def resolve_coordinates(destination: str) -> Dict[str, Any]:
    dest_key = destination.strip().lower()
    for key, data in DESTINATION_COORDINATES.items():
        if key in dest_key or dest_key in key:
            return data
    # Fallback default is Goa
    return DESTINATION_COORDINATES["goa"]

def _validate_coords(lat: Any, lon: Any):
    if lat is None or lon is None:
        return None, None
    try:
        import math
        f_lat = float(lat)
        f_lon = float(lon)
        if math.isnan(f_lat) or math.isnan(f_lon) or math.isinf(f_lat) or math.isinf(f_lon):
            return None, None
        if not (-90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0):
            return None, None
        return f_lat, f_lon
    except (ValueError, TypeError):
        return None, None

# ==============================================================================
# IN-MEMORY TTL CACHE & EXTERNAL CALL TRACKING
# ==============================================================================
import time

WEATHER_CACHE_TTL = 120.0  # 2 minutes default TTL
_weather_cache: Dict[str, Dict[str, Any]] = {}
_radar_cache: Dict[str, Dict[str, Any]] = {}
_weather_call_stats: Dict[str, int] = {"external_calls": 0, "cache_hits": 0}

def get_weather_cache_stats() -> Dict[str, int]:
    return dict(_weather_call_stats)

def clear_weather_cache():
    global _weather_cache, _radar_cache, _weather_call_stats
    _weather_cache.clear()
    _radar_cache.clear()
    _weather_call_stats = {"external_calls": 0, "cache_hits": 0}

def _make_cache_key(coord: Dict[str, Any]) -> str:
    lat = coord.get("lat")
    lon = coord.get("lon")
    if lat is not None and lon is not None:
        return f"{coord.get('name', '').lower()}_{round(float(lat), 3)}_{round(float(lon), 3)}"
    return coord.get("name", "").lower()

def fetch_live_weather(destination: str = "Goa", lat: float = None, lon: float = None) -> Dict[str, Any]:
    """
    Fetches real-time weather from Open-Meteo (100% free, no API key needed).
    Returns formatted weather data and disruption detection indicators.
    Includes debouncing / TTL caching (120s) to prevent duplicate external requests for unchanged inputs.
    """
    v_lat, v_lon = _validate_coords(lat, lon)
    if v_lat is None or v_lon is None:
        coord = resolve_coordinates(destination)
    else:
        coord = {"name": destination, "lat": v_lat, "lon": v_lon}

    cache_key = _make_cache_key(coord)
    now = time.time()
    if cache_key in _weather_cache:
        entry = _weather_cache[cache_key]
        if now - entry["timestamp"] < WEATHER_CACHE_TTL:
            _weather_call_stats["cache_hits"] += 1
            cached_data = dict(entry["data"])
            cached_data["cached"] = True
            return cached_data

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": coord["lat"],
        "longitude": coord["lon"],
        "current": "temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,wind_speed_10m"
    }

    try:
        _weather_call_stats["external_calls"] += 1
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})

        weather_code = current.get("weather_code", 0)
        precipitation = current.get("precipitation", 0.0)
        temp_c = current.get("temperature_2m", 28.0)
        wind_speed = current.get("wind_speed_10m", 0.0)
        condition_desc = WMO_WEATHER_CODES.get(weather_code, "Unknown")

        # Classify weather into clear / advisory / severe
        tier = classify_weather_severity(weather_code, precipitation, wind_speed)
        is_advisory = (tier in ["advisory", "severe"])
        requires_replanning = (tier == "severe")

        disruption_event = None
        if is_advisory:
            disruption_event = {
                "id": f"weather_{tier}_{coord['name'].lower()}_{weather_code}",
                "name": f"{'Severe Weather' if requires_replanning else 'Weather Advisory'} in {coord['name']}",
                "type": "weather",
                "severity": "high" if requires_replanning else "low",
                "requires_replanning": requires_replanning,
                "tier": tier,
                "message": f"{condition_desc} with {precipitation}mm precipitation and {wind_speed}km/h winds in {coord['name']}.",
                "weather_data": {
                    "temperature": temp_c,
                    "condition": condition_desc,
                    "precipitation": precipitation,
                    "wind_speed": wind_speed
                }
            }

        result = {
            "status": "success",
            "destination": coord["name"],
            "temperature_c": temp_c,
            "condition": condition_desc,
            "precipitation_mm": precipitation,
            "wind_speed_kmh": wind_speed,
            "weather_tier": tier,
            "is_advisory": is_advisory,
            "is_disrupted": requires_replanning,
            "requires_replanning": requires_replanning,
            "disruption_event": disruption_event,
            "cached": False
        }
        _weather_cache[cache_key] = {"data": result, "timestamp": now}
        return result

    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "destination": coord["name"],
            "error": f"Weather API error: {e}",
            "weather_tier": "clear",
            "is_advisory": False,
            "is_disrupted": False,
            "requires_replanning": False,
            "disruption_event": None,
            "cached": False
        }
    except Exception as e:
        return {
            "status": "error",
            "destination": coord["name"],
            "error": str(e),
            "weather_tier": "clear",
            "is_advisory": False,
            "is_disrupted": False,
            "requires_replanning": False,
            "disruption_event": None,
            "cached": False
        }

def check_incoming_rain_forecast(destination: str = "Goa", simulate: bool = False, lat: float = None, lon: float = None) -> Dict[str, Any]:
    """
    Checks Open-Meteo's hourly forecast for incoming rain in the next 1-2 hours.
    Allows preemptive warnings before the rain actually begins!
    Includes debouncing / TTL caching (120s) for live requests.
    """
    v_lat, v_lon = _validate_coords(lat, lon)
    if v_lat is None or v_lon is None:
        coord = resolve_coordinates(destination)
    else:
        coord = {"name": destination, "lat": v_lat, "lon": v_lon}
    if simulate:
        return {
            "incoming_rain": True,
            "minutes_away": 35,
            "precipitation_mm": 6.8,
            "probability_pct": 88,
            "condition": "Severe Thunderstorm & Torrential Downpour",
            "message": f"Precipitation radar indicates heavy rain starting in ~35 minutes across {coord['name']} (88% probability, 6.8mm/hr). Outdoor beach plans at risk!"
        }

    cache_key = _make_cache_key(coord)
    now = time.time()
    if cache_key in _radar_cache:
        entry = _radar_cache[cache_key]
        if now - entry["timestamp"] < WEATHER_CACHE_TTL:
            _weather_call_stats["cache_hits"] += 1
            cached_data = dict(entry["data"])
            cached_data["cached"] = True
            return cached_data

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": coord["lat"],
        "longitude": coord["lon"],
        "hourly": "precipitation_probability,precipitation,weather_code",
        "forecast_hours": 3
    }
    try:
        _weather_call_stats["external_calls"] += 1
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})
        precips = hourly.get("precipitation", [0.0])
        probs = hourly.get("precipitation_probability", [0])

        # If next 2 hours has rain > 1.0mm or prob > 50%
        next_precip = max(precips[:2]) if precips else 0.0
        next_prob = max(probs[:2]) if probs else 0

        incoming_rain = (next_precip >= 1.0) or (next_prob >= 55)
        result = {
            "incoming_rain": incoming_rain,
            "minutes_away": 45 if incoming_rain else None,
            "precipitation_mm": next_precip,
            "probability_pct": next_prob,
            "condition": "Rain showers" if incoming_rain else "Dry / Clear",
            "message": f"Rain expected in ~45 mins in {coord['name']} ({next_precip}mm, {next_prob}% probability)." if incoming_rain else "No imminent rain detected.",
            "cached": False
        }
        _radar_cache[cache_key] = {"data": result, "timestamp": now}
        return result
    except requests.exceptions.RequestException as e:
        return {
            "incoming_rain": False,
            "minutes_away": None,
            "precipitation_mm": 0.0,
            "probability_pct": 0,
            "condition": "Unknown",
            "message": f"Weather API error checking radar: {e}",
            "cached": False
        }
    except Exception as e:
        return {
            "incoming_rain": False,
            "minutes_away": None,
            "precipitation_mm": 0.0,
            "probability_pct": 0,
            "condition": "Unknown",
            "message": f"Could not check radar: {e}",
            "cached": False
        }

def create_simulated_weather_disruption(destination: str = "Goa") -> Dict[str, Any]:
    """Helper for testing & on-demand demo disruptions even on sunny days."""
    coord = resolve_coordinates(destination)
    return {
        "id": f"simulated_monsoon_{coord['name'].lower()}",
        "name": f"Torrential Rainstorm in {coord['name']}",
        "type": "weather",
        "severity": "high",
        "tier": "severe",
        "requires_replanning": True,
        "message": f"Heavy rainstorm (12.5mm/h) and high winds expected in {coord['name']} within 30 minutes.",
        "weather_data": {
            "temperature": 25.0,
            "condition": "Heavy rain showers",
            "precipitation": 12.5,
            "wind_speed": 42.0
        }
    }
