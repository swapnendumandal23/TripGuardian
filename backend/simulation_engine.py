"""
simulation_engine.py — Disruption Simulation Controller

Manages all simulated disruptions for Trip Guardian:
- Flight delays, early arrivals, cancellations
- Train/bus delays
- Weather events (rain, storm, heatwave)
- Tourist crowd surges at specific locations
- Venue closures (restaurants, shops, attractions)
- Traffic incidents on specific routes

Each simulation is persisted in SQLite with:
- Auto-expiry timestamps (simulations expire after their duration)
- Active/expired status tracking
- Association with specific chat_ids or global scope
"""

import json
import logging
import sqlite3
import time
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger("simulation_engine")

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trips.db")


class SimulationEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS simulations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sim_type TEXT NOT NULL,
                    target_chat_id INTEGER,
                    params TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    triggered_alerts TEXT DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    alert_key TEXT NOT NULL,
                    message_preview TEXT,
                    sent_at REAL NOT NULL,
                    cooldown_until REAL NOT NULL
                )
            """)
            conn.commit()

    # ──────────────────────────────────────────────────
    # SIMULATION CRUD
    # ──────────────────────────────────────────────────

    def create_simulation(
        self,
        sim_type: str,
        params: Dict[str, Any],
        target_chat_id: int,
        duration_minutes: int = 60
    ) -> int:
        """
        Creates a new active simulation.
        
        sim_type: flight_delay | flight_early | train_delay | bus_delay |
                  weather_event | crowd_surge | venue_closure | traffic_incident
        params: type-specific parameters dict
        duration_minutes: how long this simulation stays active
        target_chat_id: specific user to affect (None = all active trips)
        """
        now = time.time()
        expires = now + (duration_minutes * 60)

        with self._get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO simulations (sim_type, target_chat_id, params, status, created_at, expires_at)
                VALUES (?, ?, ?, 'active', ?, ?)
            """, (sim_type, target_chat_id, json.dumps(params, ensure_ascii=False), now, expires))
            conn.commit()
            sim_id = cur.lastrowid

        logger.info(f"[SimEngine] Created simulation #{sim_id}: {sim_type} (expires in {duration_minutes}m)")
        return sim_id

    def get_active_simulations(self, sim_type: Optional[str] = None, chat_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns all currently active (non-expired) simulations, optionally filtered."""
        now = time.time()
        with self._get_connection() as conn:
            # First, expire old simulations
            conn.execute("UPDATE simulations SET status = 'expired' WHERE expires_at < ? AND status = 'active'", (now,))
            conn.commit()

            query = "SELECT * FROM simulations WHERE status = 'active'"
            params_list = []
            if sim_type:
                query += " AND sim_type = ?"
                params_list.append(sim_type)
            if chat_id:
                query += " AND target_chat_id = ?"
                params_list.append(chat_id)

            cur = conn.execute(query, params_list)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["params"] = json.loads(d["params"])
                d["triggered_alerts"] = json.loads(d.get("triggered_alerts", "[]"))
                results.append(d)
            return results

    def get_all_simulations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns all simulations (active + expired), newest first."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM simulations ORDER BY created_at DESC LIMIT ?", (limit,))
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["params"] = json.loads(d["params"])
                d["triggered_alerts"] = json.loads(d.get("triggered_alerts", "[]"))
                results.append(d)
            return results

    def deactivate_simulation(self, sim_id: int):
        with self._get_connection() as conn:
            conn.execute("UPDATE simulations SET status = 'expired' WHERE id = ?", (sim_id,))
            conn.commit()

    def mark_alert_triggered(self, sim_id: int, chat_id: int):
        """Marks that a simulation has already triggered an alert for a specific chat_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT triggered_alerts FROM simulations WHERE id = ?", (sim_id,)).fetchone()
            if row:
                triggered = json.loads(row["triggered_alerts"])
                if chat_id not in triggered:
                    triggered.append(chat_id)
                    conn.execute("UPDATE simulations SET triggered_alerts = ? WHERE id = ?",
                                 (json.dumps(triggered), sim_id))
                    conn.commit()

    def has_triggered_for(self, sim_id: int, chat_id: int) -> bool:
        """Checks if this simulation has already alerted this chat_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT triggered_alerts FROM simulations WHERE id = ?", (sim_id,)).fetchone()
            if row:
                triggered = json.loads(row["triggered_alerts"])
                return chat_id in triggered
        return False

    # ──────────────────────────────────────────────────
    # NOTIFICATION THROTTLE / COOLDOWN
    # ──────────────────────────────────────────────────

    def can_send_notification(self, chat_id: int, alert_type: str, alert_key: str) -> bool:
        """
        Checks if we're allowed to send this notification type given cooldown rules.
        Returns True if OK to send, False if still in cooldown.
        """
        now = time.time()
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT cooldown_until FROM notification_log
                WHERE chat_id = ? AND alert_type = ? AND alert_key = ?
                ORDER BY sent_at DESC LIMIT 1
            """, (chat_id, alert_type, alert_key)).fetchone()

            if row and row["cooldown_until"] > now:
                return False
            return True

    def log_notification(self, chat_id: int, alert_type: str, alert_key: str,
                         message_preview: str = "", cooldown_minutes: int = 60):
        """Records that a notification was sent, with a cooldown period."""
        now = time.time()
        cooldown_until = now + (cooldown_minutes * 60)
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO notification_log (chat_id, alert_type, alert_key, message_preview, sent_at, cooldown_until)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (chat_id, alert_type, alert_key, message_preview[:200], now, cooldown_until))
            conn.commit()

    def get_notification_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent notification log entries for the dashboard."""
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT * FROM notification_log ORDER BY sent_at DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cur.fetchall()]

    def clear_cooldowns(self, chat_id: Optional[int] = None):
        """Clears all cooldowns (useful for testing)."""
        with self._get_connection() as conn:
            if chat_id:
                conn.execute("DELETE FROM notification_log WHERE chat_id = ?", (chat_id,))
            else:
                conn.execute("DELETE FROM notification_log")
            conn.commit()

    # ──────────────────────────────────────────────────
    # CONVENIENCE SIMULATION CREATORS
    # ──────────────────────────────────────────────────

    def simulate_flight_delay(self, flight_number: str, delay_minutes: int,
                               target_chat_id: int,
                               reason: str = "Air traffic congestion",
                               duration_minutes: int = 120) -> int:
        return self.create_simulation("flight_delay", {
            "flight_number": flight_number,
            "delay_minutes": delay_minutes,
            "reason": reason,
            "original_arrival": None,
            "new_arrival": None,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_flight_early(self, flight_number: str, early_minutes: int,
                               target_chat_id: int,
                               duration_minutes: int = 120) -> int:
        return self.create_simulation("flight_early", {
            "flight_number": flight_number,
            "early_minutes": early_minutes,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_train_delay(self, train_number: str, delay_minutes: int,
                              target_chat_id: int,
                              reason: str = "Signal failure",
                              duration_minutes: int = 120) -> int:
        return self.create_simulation("train_delay", {
            "train_number": train_number,
            "delay_minutes": delay_minutes,
            "reason": reason,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_bus_delay(self, bus_id: str, delay_minutes: int,
                            target_chat_id: int,
                            reason: str = "Road maintenance",
                            duration_minutes: int = 60) -> int:
        return self.create_simulation("bus_delay", {
            "bus_id": bus_id,
            "delay_minutes": delay_minutes,
            "reason": reason,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_weather_event(self, destination: str, target_chat_id: int,
                                event_type: str = "heavy_rain",
                                severity: str = "high",
                                duration_minutes: int = 180) -> int:
        return self.create_simulation("weather_event", {
            "destination": destination,
            "event_type": event_type,  # heavy_rain, thunderstorm, heatwave, cyclone_warning
            "severity": severity,      # low, medium, high, extreme
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_crowd_surge(self, location_name: str, target_chat_id: int,
                              crowd_level: str = "high",
                              destination: str = "Goa",
                              duration_minutes: int = 120) -> int:
        return self.create_simulation("crowd_surge", {
            "location_name": location_name,
            "crowd_level": crowd_level,  # low, moderate, high, extreme
            "destination": destination,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_venue_closure(self, venue_name: str, target_chat_id: int,
                                reason: str = "Holiday closure",
                                destination: str = "Goa",
                                duration_minutes: int = 480) -> int:
        return self.create_simulation("venue_closure", {
            "venue_name": venue_name,
            "reason": reason,  # holiday, renovation, emergency, staff_shortage
            "destination": destination,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)

    def simulate_traffic_incident(self, route_description: str, target_chat_id: int,
                                   delay_minutes: int = 45,
                                   cause: str = "Multi-vehicle accident",
                                   destination: str = "Goa",
                                   duration_minutes: int = 90) -> int:
        return self.create_simulation("traffic_incident", {
            "route_description": route_description,
            "delay_minutes": delay_minutes,
            "cause": cause,
            "destination": destination,
        }, duration_minutes=duration_minutes, target_chat_id=target_chat_id)


# Global singleton
sim_engine = SimulationEngine()
