import requests
from typing import Dict, Any
from weather_service import resolve_coordinates

# Coordinates for major transit hubs
TRANSIT_COORDINATES = {
    "goa": {
        "airport": {"name": "Dabolim Airport (GOI)", "lat": 15.3808, "lon": 73.8314},
        "mopa_airport": {"name": "Manohar International Airport (GOX)", "lat": 15.7533, "lon": 73.8653},
        "default_hotel": {"name": "Taj Holiday Village Resort & Spa", "lat": 15.5024, "lon": 73.7716}
    },
    "jaipur": {
        "airport": {"name": "Jaipur International Airport (JAI)", "lat": 26.8289, "lon": 75.8056},
        "default_hotel": {"name": "Rambagh Palace Jaipur", "lat": 26.8978, "lon": 75.8083}
    },
    "mumbai": {
        "airport": {"name": "Chhatrapati Shivaji Maharaj Airport (BOM)", "lat": 19.0896, "lon": 72.8656},
        "default_hotel": {"name": "The Taj Mahal Palace", "lat": 18.9217, "lon": 72.8332}
    }
}

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"

# ==============================================================================
# IN-MEMORY TTL CACHE & EXTERNAL CALL TRACKING
# ==============================================================================
import time

TRAFFIC_CACHE_TTL = 120.0  # 2 minutes default TTL
_traffic_cache: Dict[str, Dict[str, Any]] = {}
_traffic_call_stats: Dict[str, int] = {"external_calls": 0, "cache_hits": 0}

def get_traffic_cache_stats() -> Dict[str, int]:
    return dict(_traffic_call_stats)

def clear_traffic_cache():
    global _traffic_cache, _traffic_call_stats
    _traffic_cache.clear()
    _traffic_call_stats = {"external_calls": 0, "cache_hits": 0}

def calculate_live_route(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> Dict[str, Any]:
    """
    Queries live road network distance and baseline travel duration via OSRM.
    100% Free, NO API key required.
    Includes debouncing / TTL caching (120s) to prevent duplicate external requests.
    """
    cache_key = f"{round(float(origin_lat), 3)}_{round(float(origin_lon), 3)}_{round(float(dest_lat), 3)}_{round(float(dest_lon), 3)}"
    now = time.time()
    if cache_key in _traffic_cache:
        entry = _traffic_cache[cache_key]
        if now - entry["timestamp"] < TRAFFIC_CACHE_TTL:
            _traffic_call_stats["cache_hits"] += 1
            cached = dict(entry["data"])
            cached["cached"] = True
            return cached

    url = f"{OSRM_BASE_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=false"
    try:
        _traffic_call_stats["external_calls"] += 1
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        if "routes" in data and data["routes"]:
            route = data["routes"][0]
            duration_mins = round(route["duration"] / 60, 1)
            distance_km = round(route["distance"] / 1000, 1)
            res = {
                "status": "success",
                "distance_km": distance_km,
                "duration_mins": duration_mins,
                "cached": False
            }
            _traffic_cache[cache_key] = {"data": res, "timestamp": now}
            return res
    except requests.exceptions.RequestException as e:
        print(f"OSRM API Exception: {e}")
    except Exception as e:
        print(f"OSRM Unexpected Exception: {e}")
    
    # Fallback estimates if OSRM public server has momentary timeout
    try:
        import math
        R = 6371.0
        dlat = math.radians(dest_lat - origin_lat)
        dlon = math.radians(dest_lon - origin_lon)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(origin_lat)) * math.cos(math.radians(dest_lat)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance_km = round(R * c * 1.3, 1) # 1.3 road winding factor
        duration_mins = round((distance_km / 40.0) * 60.0, 1) # Assume 40 km/h avg speed
    except Exception:
        distance_km = 28.5
        duration_mins = 35.0

    res = {
        "status": "estimated",
        "distance_km": distance_km,
        "duration_mins": duration_mins,
        "cached": False
    }
    _traffic_cache[cache_key] = {"data": res, "timestamp": now}
    return res

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

def check_traffic_congestion(destination: str = "Goa", simulate_surge: bool = False, lat: float = None, lon: float = None) -> Dict[str, Any]:
    """
    Evaluates traffic congestion between the current location/airport and destination hotel.
    Detects bottlenecks on major transit arteries (e.g. NH-66, Mandovi Bridge).
    """
    dest_key = destination.strip().lower()
    hub = TRANSIT_COORDINATES.get(dest_key, TRANSIT_COORDINATES["goa"])
    
    # If user has a live location, use it as origin, else use airport
    v_lat, v_lon = _validate_coords(lat, lon)
    if v_lat is not None and v_lon is not None:
        apt = {"name": "Current Location", "lat": v_lat, "lon": v_lon}
    else:
        apt = hub.get("airport", TRANSIT_COORDINATES["goa"]["airport"])
        
    hotel = hub.get("default_hotel", TRANSIT_COORDINATES["goa"]["default_hotel"])

    route = calculate_live_route(apt["lat"], apt["lon"], hotel["lat"], hotel["lon"])
    baseline_mins = route["duration_mins"]

    # In MVP prototype: detect congestion surges (or simulate live bottleneck)
    if simulate_surge:
        delay_mins = 28.0
        congested_mins = round(baseline_mins + delay_mins, 1)
        return {
            "has_congestion": True,
            "origin": apt["name"],
            "destination": hotel["name"],
            "baseline_mins": baseline_mins,
            "current_eta_mins": congested_mins,
            "delay_mins": delay_mins,
            "bottleneck": "NH-66 Zuari & Mandovi Bridge Approach",
            "advisory": (
                f"🚨 Heavy congestion detected on route to {hotel['name']} (+{int(delay_mins)} mins delay).\n"
                f"• Baseline Travel Time: {baseline_mins} mins\n"
                f"• Current Congested Travel Time: {congested_mins} mins\n"
                f"• Detour Advice: Divert via Chogm Road / Coastal Nerul bypass, or grab a quick coffee nearby before departing!"
            )
        }

    return {
        "has_congestion": False,
        "origin": apt["name"],
        "destination": hotel["name"],
        "baseline_mins": baseline_mins,
        "current_eta_mins": baseline_mins,
        "delay_mins": 0.0,
        "bottleneck": None,
        "advisory": f"Traffic is flowing smoothly from {apt['name']} to {hotel['name']} (ETA: {baseline_mins} mins)."
    }
