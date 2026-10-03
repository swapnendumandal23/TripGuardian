import os
import sqlite3
import time
import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trips.db")

def validate_coordinates(lat: Any, lon: Any) -> Tuple[Optional[float], Optional[float]]:
    if lat is None or lon is None:
        return None, None
    try:
        f_lat = float(lat)
        f_lon = float(lon)
        if math.isnan(f_lat) or math.isnan(f_lon) or math.isinf(f_lat) or math.isinf(f_lon):
            return None, None
        if not (-90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0):
            return None, None
        return f_lat, f_lon
    except (ValueError, TypeError):
        return None, None

class TripStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with self._get_connection() as conn:
            # Legacy table (do not drop, just in case)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trips (
                    chat_id INTEGER PRIMARY KEY,
                    destination TEXT NOT NULL,
                    itinerary TEXT NOT NULL,
                    flight_number TEXT,
                    hotel_name TEXT DEFAULT 'Taj Holiday Village Resort & Spa',
                    hotel_checkin_time TEXT DEFAULT '14:00',
                    flight_arrival_time TEXT DEFAULT '13:00',
                    landing_alert_sent INTEGER DEFAULT 0,
                    rain_alert_sent INTEGER DEFAULT 0,
                    traffic_alert_sent INTEGER DEFAULT 0,
                    last_alert_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            
            # New table for multiple trips per user
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trips_multi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'upcoming',
                    destination TEXT NOT NULL,
                    itinerary TEXT NOT NULL,
                    flight_number TEXT,
                    hotel_name TEXT DEFAULT 'Taj Holiday Village Resort & Spa',
                    hotel_checkin_time TEXT DEFAULT '14:00',
                    flight_arrival_time TEXT DEFAULT '13:00',
                    landing_alert_sent INTEGER DEFAULT 0,
                    rain_alert_sent INTEGER DEFAULT 0,
                    traffic_alert_sent INTEGER DEFAULT 0,
                    last_alert_id TEXT,
                    trip_start_date TEXT,
                    trip_end_date TEXT,
                    structured_itinerary TEXT,
                    transport_details TEXT,
                    current_lat REAL,
                    current_lon REAL,
                    location_updated_at REAL,
                    original_itinerary TEXT,
                    recommended_itinerary TEXT,
                    visited_places TEXT DEFAULT '[]',
                    rejected_categories TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            # Table for durable user preferences
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    chat_id INTEGER PRIMARY KEY,
                    preferences TEXT DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)

            # Non-destructive migrations for legacy and trips_multi table
            for table_name in ["trips", "trips_multi"]:
                for col, defn in [
                    ("hotel_name", "TEXT DEFAULT 'Taj Holiday Village Resort & Spa'"),
                    ("hotel_checkin_time", "TEXT DEFAULT '14:00'"),
                    ("flight_arrival_time", "TEXT DEFAULT '13:00'"),
                    ("landing_alert_sent", "INTEGER DEFAULT 0"),
                    ("rain_alert_sent", "INTEGER DEFAULT 0"),
                    ("traffic_alert_sent", "INTEGER DEFAULT 0"),
                    ("trip_start_date", "TEXT"),
                    ("trip_end_date", "TEXT"),
                    ("structured_itinerary", "TEXT"),
                    ("transport_details", "TEXT"),
                    ("current_lat", "REAL"),
                    ("current_lon", "REAL"),
                    ("location_updated_at", "REAL"),
                    ("original_itinerary", "TEXT"),
                    ("recommended_itinerary", "TEXT"),
                    ("visited_places", "TEXT DEFAULT '[]'"),
                    ("rejected_categories", "TEXT DEFAULT '[]'"),
                    ("is_demo_location", "INTEGER DEFAULT 0"),
                    ("demo_lat", "REAL"),
                    ("demo_lon", "REAL"),
                ]:
                    try:
                        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {defn}")
                    except Exception:
                        pass
            
            # Migrate data from trips to trips_multi if trips_multi is empty
            cur = conn.execute("SELECT COUNT(*) FROM trips_multi")
            if cur.fetchone()[0] == 0:
                conn.execute("""
                    INSERT INTO trips_multi (
                        chat_id, destination, itinerary, flight_number, hotel_name,
                        hotel_checkin_time, flight_arrival_time, landing_alert_sent,
                        rain_alert_sent, traffic_alert_sent, last_alert_id,
                        trip_start_date, trip_end_date, structured_itinerary, transport_details,
                        current_lat, current_lon, original_itinerary, recommended_itinerary,
                        visited_places, rejected_categories, created_at, updated_at
                    )
                    SELECT 
                        chat_id, destination, itinerary, flight_number, hotel_name,
                        hotel_checkin_time, flight_arrival_time, landing_alert_sent,
                        rain_alert_sent, traffic_alert_sent, last_alert_id,
                        trip_start_date, trip_end_date, structured_itinerary, transport_details,
                        current_lat, current_lon, original_itinerary, recommended_itinerary,
                        visited_places, rejected_categories, created_at, updated_at
                    FROM trips
                """)

            conn.commit()

        # Pre-seed mock trip if database is empty
        if not self.get_trip(123456789):
            self.save_trip(
                chat_id=123456789,
                destination="Goa",
                itinerary=(
                    "Day 1:\n"
                    "• Morning: Anjuna Beach walk & flea market\n"
                    "• Afternoon: Mandovi River Sunset Cruise\n"
                    "• Evening: Curlies Beach Shack dinner"
                ),
                flight_number="6E-501",
                hotel_name="Taj Holiday Village Resort & Spa",
                hotel_checkin_time="14:00",
                flight_arrival_time="13:00"
            )

    def save_trip(
        self,
        chat_id: int,
        destination: str,
        itinerary: str,
        flight_number: Optional[str] = "6E-501",
        hotel_name: str = "Taj Holiday Village Resort & Spa",
        hotel_checkin_time: str = "14:00",
        flight_arrival_time: str = "13:00"
    ):
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO trips_multi (
                    chat_id, destination, itinerary, flight_number, hotel_name,
                    hotel_checkin_time, flight_arrival_time, landing_alert_sent,
                    rain_alert_sent, traffic_alert_sent, last_alert_id, created_at, updated_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, ?, 'active')
            """, (chat_id, destination, itinerary, flight_number, hotel_name, hotel_checkin_time, flight_arrival_time, now, now))
            conn.commit()

    def refresh_trip_statuses(self):
        """Automatically updates trip status to active or completed based on dates and times."""
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        today = now_dt.strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            # Mark trips as completed if end date passed
            conn.execute("""
                UPDATE trips_multi SET status = 'completed'
                WHERE trip_end_date IS NOT NULL AND trip_end_date != '' 
                AND (
                    (length(trip_end_date) <= 10 AND trip_end_date < ?) OR
                    (length(trip_end_date) > 10 AND trip_end_date < ?)
                )
                AND status != 'completed'
            """, (today, now_str))
            
            # Mark upcoming trips as active if start date is reached or start date is NULL/empty
            conn.execute("""
                UPDATE trips_multi SET status = 'active'
                WHERE (trip_start_date IS NULL OR trip_start_date = '' OR 
                      (length(trip_start_date) <= 10 AND trip_start_date <= ?) OR
                      (length(trip_start_date) > 10 AND trip_start_date <= ?)
                )
                AND (trip_end_date IS NULL OR trip_end_date = '' OR 
                    (length(trip_end_date) <= 10 AND trip_end_date >= ?) OR
                    (length(trip_end_date) > 10 AND trip_end_date >= ?)
                )
                AND status = 'upcoming'
            """, (today, now_str, today, now_str))
            conn.commit()

    def get_trip(self, chat_id: int, trip_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        self.refresh_trip_statuses()
        with self._get_connection() as conn:
            if trip_id:
                cur = conn.execute("SELECT * FROM trips_multi WHERE chat_id = ? AND id = ?", (chat_id, trip_id))
            else:
                cur = conn.execute("""
                    SELECT * FROM trips_multi 
                    WHERE chat_id = ? 
                    ORDER BY 
                        CASE status 
                            WHEN 'active' THEN 1 
                            WHEN 'upcoming' THEN 2 
                            ELSE 3 
                        END ASC,
                        trip_start_date ASC,
                        updated_at DESC
                    LIMIT 1
                """, (chat_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
            return None

    def get_or_create_default_trip(self, chat_id: int, destination: str = "Goa") -> Dict[str, Any]:
        trip = self.get_trip(chat_id)
        if not trip:
            self.save_trip(
                chat_id=chat_id,
                destination=destination,
                itinerary=(
                    "Day 1: 14:00 Hotel Check-in at Taj Holiday Village, 16:30 Calangute Beach walk, 18:30 Sunset Mandovi River Cruise.\n"
                    "Day 2: 10:00 Old Goa Churches, 13:00 Spice Plantation Tour, 19:00 Curlies Shack dinner."
                ),
                flight_number="6E-501",
                hotel_name="Taj Holiday Village Resort & Spa",
                hotel_checkin_time="14:00",
                flight_arrival_time="13:00"
            )
            trip = self.get_trip(chat_id)
        return trip


    def update_itinerary(self, chat_id: int, new_itinerary: str):
        trip = self.get_trip(chat_id)
        if not trip: return
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trips_multi SET itinerary = ?, updated_at = ? WHERE id = ?
            """, (new_itinerary, now, trip['id']))
            conn.commit()

    def update_location(self, chat_id: int, lat: Any, lon: Any):
        trip = self.get_trip(chat_id)
        if not trip: return
        now = time.time()
        v_lat, v_lon = validate_coordinates(lat, lon)
        with self._get_connection() as conn:
            if v_lat is not None and v_lon is not None:
                conn.execute("""
                    UPDATE trips_multi SET current_lat = ?, current_lon = ?, location_updated_at = ?, updated_at = ? WHERE id = ?
                """, (v_lat, v_lon, now, now, trip['id']))
            else:
                conn.execute("""
                    UPDATE trips_multi SET current_lat = NULL, current_lon = NULL, location_updated_at = NULL, updated_at = ? WHERE id = ?
                """, (now, trip['id']))
            conn.commit()

    def get_valid_location(self, chat_id: int, max_age_hours: float = 24.0) -> Tuple[Optional[float], Optional[float]]:
        loc = self.get_current_location(chat_id, max_age_hours)
        if loc:
            return loc["latitude"], loc["longitude"]
        return None, None

    def _get_real_location(self, chat_id: int, max_age_hours: float = 24.0) -> Tuple[Optional[float], Optional[float]]:
        trip = self.get_trip(chat_id)
        if not trip:
            return None, None
        lat = trip.get("current_lat")
        lon = trip.get("current_lon")
        loc_updated = trip.get("location_updated_at") or trip.get("updated_at")
        
        v_lat, v_lon = validate_coordinates(lat, lon)
        if v_lat is None or v_lon is None:
            return None, None
            
        if loc_updated is not None:
            try:
                age_seconds = time.time() - float(loc_updated)
                if age_seconds > max_age_hours * 3600:
                    return None, None
            except (ValueError, TypeError):
                pass
                
        return v_lat, v_lon

    def update_demo_location(self, chat_id: int, lat: Any, lon: Any, is_demo: bool = True):
        trip = self.get_trip(chat_id)
        if not trip: return
        now = time.time()
        v_lat, v_lon = validate_coordinates(lat, lon) if is_demo else (None, None)
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trips_multi SET is_demo_location = ?, demo_lat = ?, demo_lon = ?, updated_at = ? WHERE id = ?
            """, (1 if is_demo else 0, v_lat, v_lon, now, trip['id']))
            conn.commit()

    def get_current_location(self, chat_id: int, max_age_hours: float = 24.0) -> Optional[Dict[str, Any]]:
        """
        Unified Location Service.
        Returns the current location either from DEMO or REAL mode.
        """
        trip = self.get_trip(chat_id)
        if not trip:
            return None
            
        is_demo = bool(trip.get("is_demo_location"))
        if is_demo:
            d_lat = trip.get("demo_lat")
            d_lon = trip.get("demo_lon")
            v_lat, v_lon = validate_coordinates(d_lat, d_lon)
            if v_lat is not None and v_lon is not None:
                return {
                    "latitude": v_lat,
                    "longitude": v_lon,
                    "source": "demo",
                    "updated_at": trip.get("updated_at")
                }
                
        # Fallback to real location if demo is disabled or invalid
        r_lat, r_lon = self._get_real_location(chat_id, max_age_hours)
        if r_lat is not None and r_lon is not None:
            return {
                "latitude": r_lat,
                "longitude": r_lon,
                "source": "real",
                "updated_at": trip.get("location_updated_at")
            }
            
        return None

    def update_flight(self, chat_id: int, flight_number: str, arrival_time: str = "13:00"):
        trip = self.get_trip(chat_id)
        if not trip: return
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trips_multi SET flight_number = ?, flight_arrival_time = ?, updated_at = ? WHERE id = ?
            """, (flight_number, arrival_time, now, trip['id']))
            conn.commit()

    def update_hotel(self, chat_id: int, hotel_name: str, checkin_time: str = "14:00"):
        trip = self.get_trip(chat_id)
        if not trip: return
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trips_multi SET hotel_name = ?, hotel_checkin_time = ?, updated_at = ? WHERE id = ?
            """, (hotel_name, checkin_time, now, trip['id']))
            conn.commit()

    def set_alert_flag(self, chat_id: int, flag_name: str, value: int = 1):
        trip = self.get_trip(chat_id)
        if not trip: return
        if flag_name in ["landing_alert_sent", "rain_alert_sent", "traffic_alert_sent"]:
            with self._get_connection() as conn:
                conn.execute(f"UPDATE trips_multi SET {flag_name} = ?, updated_at = ? WHERE id = ?", (value, time.time(), trip['id']))
                conn.commit()

    def reset_alert_flags(self, chat_id: int):
        trip = self.get_trip(chat_id)
        if not trip: return
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trips_multi SET landing_alert_sent = 0, rain_alert_sent = 0, traffic_alert_sent = 0, last_alert_id = NULL WHERE id = ?
            """, (trip['id'],))
            conn.commit()

    def get_all_active_trips(self) -> List[Dict[str, Any]]:
        self.refresh_trip_statuses()
        with self._get_connection() as conn:
            # For the scheduler, we want to fetch the 'current' highest priority trip for every user
            # But ONLY if it is truly active (status='active')
            cur = conn.execute("""
                SELECT * FROM (
                    SELECT *,
                    ROW_NUMBER() OVER(
                        PARTITION BY chat_id 
                        ORDER BY 
                            CASE status 
                                WHEN 'active' THEN 1 
                                WHEN 'upcoming' THEN 2 
                                ELSE 3 
                            END ASC,
                            trip_start_date ASC,
                            updated_at DESC
                    ) as rn
                    FROM trips_multi
                ) WHERE rn = 1 AND status = 'active'
            """)
            return [dict(row) for row in cur.fetchall()]

    def should_send_alert(self, chat_id: int, alert_id: str) -> bool:
        trip = self.get_trip(chat_id)
        if not trip:
            return False
        last_alerts = trip.get("last_alert_id") or ""
        return alert_id not in last_alerts.split(",")

    def mark_alert_sent(self, chat_id: int, alert_id: str):
        now = time.time()
        trip = self.get_trip(chat_id)
        if not trip:
            return
        last_alerts = trip.get("last_alert_id") or ""
        alerts_list = last_alerts.split(",") if last_alerts else []
        if alert_id not in alerts_list:
            alerts_list.append(alert_id)
            if len(alerts_list) > 15:
                alerts_list.pop(0)
            new_alerts = ",".join(alerts_list)
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE trips_multi SET last_alert_id = ?, updated_at = ? WHERE id = ?
                """, (new_alerts, now, trip['id']))
                conn.commit()

    def clear_trip(self, chat_id: int):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM trips_multi WHERE chat_id = ?", (chat_id,))
            conn.commit()

    def save_trip_v2(
        self,
        chat_id: int,
        destination: str,
        itinerary: str,
        flight_number: Optional[str] = None,
        hotel_name: str = "Hotel",
        hotel_checkin_time: str = "14:00",
        flight_arrival_time: str = "13:00",
        trip_start_date: Optional[str] = None,
        trip_end_date: Optional[str] = None,
        structured_itinerary: Optional[str] = None,
        transport_details: Optional[str] = None,
        original_itinerary: Optional[str] = None,
        recommended_itinerary: Optional[str] = None,
        status: str = "upcoming"
    ):
        """Enhanced save that inserts a new trip entry."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO trips_multi (
                    chat_id, destination, itinerary, flight_number, hotel_name,
                    hotel_checkin_time, flight_arrival_time, landing_alert_sent,
                    rain_alert_sent, traffic_alert_sent, last_alert_id,
                    trip_start_date, trip_end_date, structured_itinerary, transport_details,
                    original_itinerary, recommended_itinerary,
                    created_at, updated_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chat_id, destination, itinerary, flight_number, hotel_name,
                  hotel_checkin_time, flight_arrival_time,
                  trip_start_date, trip_end_date, structured_itinerary, transport_details,
                  original_itinerary or itinerary, recommended_itinerary,
                  now, now, status))
            conn.commit()

    def mark_place_visited(self, chat_id: int, place_name: str):
        trip = self.get_trip(chat_id)
        if not trip: return
        import json
        visited = json.loads(trip.get("visited_places") or "[]")
        if place_name not in visited:
            visited.append(place_name)
            with self._get_connection() as conn:
                conn.execute("UPDATE trips_multi SET visited_places = ?, updated_at = ? WHERE id = ?", 
                             (json.dumps(visited), time.time(), trip['id']))
                conn.commit()
                
    def get_visited_places(self, chat_id: int) -> list:
        trip = self.get_trip(chat_id)
        if not trip: return []
        import json
        return json.loads(trip.get("visited_places") or "[]")
        
    def add_rejected_category(self, chat_id: int, category: str):
        trip = self.get_trip(chat_id)
        if not trip: return
        import json
        rejected = json.loads(trip.get("rejected_categories") or "[]")
        cat = category.lower().strip()
        if cat not in rejected:
            rejected.append(cat)
            with self._get_connection() as conn:
                conn.execute("UPDATE trips_multi SET rejected_categories = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(rejected), time.time(), trip['id']))
                conn.commit()

    def get_rejected_categories(self, chat_id: int) -> list:
        trip = self.get_trip(chat_id)
        if not trip: return []
        import json
        return json.loads(trip.get("rejected_categories") or "[]")
        
    def update_recommended_itinerary(self, chat_id: int, recommended_itinerary: str):
        trip = self.get_trip(chat_id)
        if not trip: return
        with self._get_connection() as conn:
            conn.execute("UPDATE trips_multi SET recommended_itinerary = ?, updated_at = ? WHERE id = ?",
                         (recommended_itinerary, time.time(), trip['id']))
            conn.commit()

    def get_structured_itinerary(self, chat_id: int) -> Optional[dict]:
        """Returns parsed structured itinerary JSON for a trip."""
        import json
        trip = self.get_trip(chat_id)
        if trip and trip.get("structured_itinerary"):
            try:
                return json.loads(trip["structured_itinerary"])
            except Exception:
                return None
        return None

    def get_user_preferences(self, chat_id: int) -> str:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT preferences FROM user_profiles WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()
            return row["preferences"] if row and row["preferences"] else ""

    def update_user_preferences(self, chat_id: int, preferences: str):
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO user_profiles (chat_id, preferences, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET preferences = ?, updated_at = ?
            """, (chat_id, preferences, now, preferences, now))
            conn.commit()

trip_store = TripStore()
