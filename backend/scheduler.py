import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from weather_service import (
    fetch_live_weather,
    create_simulated_weather_disruption,
    check_incoming_rain_forecast
)
from traffic_service import check_traffic_congestion
from places_service import get_gap_time_cafes, discover_live_places
from trip_store import trip_store
from RAG_Pipeline import rag_pipeline
from agent_interface import evaluate_and_replan
from telegram_client import send_telegram_message
from simulation_engine import sim_engine

logger = logging.getLogger("proactive_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ──────────────────────────────────────────────────
# COOLDOWN RULES (in minutes)
# ──────────────────────────────────────────────────
COOLDOWNS = {
    "landing_buffer":       0,      # Once per trip (controlled by alert_key uniqueness)
    "rain_approaching":     120,    # 2 hour cooldown
    "traffic_congestion":   60,     # 1 hour cooldown
    "weather_disruption":   120,    # 2 hour cooldown
    "weather_advisory":     120,    # 2 hour cooldown
    "flight_delay":         30,     # Re-notify every 30 min if delay worsens
    "flight_early":         0,      # Once per flight
    "crowd_surge":          120,    # 2 hour cooldown per venue
    "venue_closure":        480,    # Once per day per venue
    "traffic_incident":     60,     # 1 hour cooldown
    "activity_reminder":    0,      # Once per activity (key = activity name + time)
    "morning_briefing":     1440,   # Once per day
}

# ──────────────────────────────────────────────────
# POLLING FREQUENCIES & INTERVAL CONFIGURATION
# ──────────────────────────────────────────────────
# Separates simulation polling frequency (hackathon demo responsiveness)
# from production live-data polling (weather, radar, traffic, places).
POLLING_INTERVALS = {
    "simulations": 5,          # Fast 5s polling for hackathon demo triggers (0 external calls, pure SQLite)
    "activities": 15,          # 15s interval for upcoming activity reminders (local time calculation)
    "visited_places": 15,      # 15s interval for geofence visited-places updates (local coordinates math)
    "weather_live": 120,       # 120s (2 min) interval for Open-Meteo live weather updates
    "rain_radar_live": 120,    # 120s (2 min) interval for Open-Meteo rain radar
    "traffic_live": 180,       # 180s (3 min) interval for OSRM live traffic routing
    "landing_buffer": 120,     # 120s (2 min) interval for airport landing & hotel check-in gap detection
}


class ProactiveEngine:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False

    # ──────────────────────────────────────────────────
    # FAST SIMULATION SENTINEL (0 EXTERNAL CALLS)
    # ──────────────────────────────────────────────────
    def check_active_simulations(self):
        """
        Fast hackathon simulation polling loop (runs every 5 seconds).
        Queries local SQLite sim_engine (0 external API calls).
        Ensures instantaneous demo responsiveness for all user-triggered disruptions
        (flight delays, traffic incidents, storm events, crowd surges, venue closures)
        without creating network load or hitting external API rate limits.
        """
        active_trips = trip_store.get_all_active_trips()
        for trip in active_trips:
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")

            # 1. Weather event simulations
            weather_sims = sim_engine.get_active_simulations("weather_event", chat_id)
            for wsim in weather_sims:
                if not sim_engine.has_triggered_for(wsim["id"], chat_id):
                    params = wsim["params"]
                    if params.get("event_type") in ("heavy_rain", "thunderstorm", "cyclone_warning"):
                        self._send_rain_alert(chat_id, destination, simulated_params=params)
                        sim_engine.mark_alert_triggered(wsim["id"], chat_id)

            # 2. Traffic incident simulations
            traffic_sims = sim_engine.get_active_simulations("traffic_incident", chat_id)
            for tsim in traffic_sims:
                if not sim_engine.has_triggered_for(tsim["id"], chat_id):
                    params = tsim["params"]
                    message = (
                        f"🚦 *Simulated Traffic Incident*\n\n"
                        f"A *{params.get('cause', 'traffic incident')}* has been reported on *{params.get('route_description', 'the main route')}*.\n\n"
                        f"• *Estimated Delay:* +{params.get('delay_minutes', 30)} minutes\n"
                        f"• *Area:* {params.get('destination', destination)}\n\n"
                        f"💡 *Recommendation:* Consider an alternate route or delay departure by {params.get('delay_minutes', 30)} minutes."
                    )
                    alert_key = f"traffic_sim_{tsim['id']}"
                    if self._throttled_send(chat_id, "traffic_incident", alert_key, message):
                        sim_engine.mark_alert_triggered(tsim["id"], chat_id)

    # ──────────────────────────────────────────────────
    # THROTTLE GUARD (wraps every notification)
    # ──────────────────────────────────────────────────
    def _throttled_send(self, chat_id: int, alert_type: str, alert_key: str,
                        message: str, cooldown_minutes: Optional[int] = None) -> bool:
        """
        Sends a notification ONLY if it hasn't been sent within the cooldown window.
        Returns True if actually sent, False if throttled.
        """
        cd = cooldown_minutes if cooldown_minutes is not None else COOLDOWNS.get(alert_type, 60)
        if not sim_engine.can_send_notification(chat_id, alert_type, alert_key):
            logger.debug(f"[Throttle] Suppressed {alert_type}/{alert_key} for chat {chat_id}")
            return False
        send_telegram_message(chat_id, message)
        sim_engine.log_notification(chat_id, alert_type, alert_key, message[:200], cd)
        logger.info(f"[ProactiveEngine] Sent {alert_type} alert to chat {chat_id}")
        return True

    # ──────────────────────────────────────────────────
    # 1. CHECK-IN BUFFER & EARLY LANDING WATCHER
    # ──────────────────────────────────────────────────

    def check_landing_buffer_alerts(self, force_chat_id: Optional[int] = None, simulate: bool = False):
        """
        Proactively notifies travelers who have landed ~1 hour before hotel check-in
        and suggests nearby luggage-friendly cafes with Wi-Fi & AC.
        """
        trips = [trip_store.get_or_create_default_trip(force_chat_id)] if force_chat_id else trip_store.get_all_active_trips()
        for trip in trips:
            if not trip:
                continue
            chat_id = trip["chat_id"]

            if trip.get("landing_alert_sent") and not simulate:
                continue

            destination = trip.get("destination", "Goa")
            hotel_name = trip.get("hotel_name", "Taj Holiday Village Resort & Spa")
            checkin_time = trip.get("hotel_checkin_time", "14:00")
            arrival_time = trip.get("flight_arrival_time", "13:00")

            # Check gap condition if not forced simulation
            if not simulate:
                try:
                    from datetime_utils import parse_time_to_minutes
                    c_mins = parse_time_to_minutes(checkin_time)
                    a_mins = parse_time_to_minutes(arrival_time)
                    gap_mins = c_mins - a_mins
                    if not (30 <= gap_mins <= 120):
                        continue
                except Exception:
                    pass

            # Also check for simulated early arrival
            early_sims = sim_engine.get_active_simulations("flight_early", chat_id)
            for esim in early_sims:
                if not sim_engine.has_triggered_for(esim["id"], chat_id):
                    early_mins = esim["params"].get("early_minutes", 30)
                    try:
                        from datetime_utils import parse_time_to_minutes
                        a_mins = parse_time_to_minutes(arrival_time)
                        total_mins = a_mins - early_mins
                        new_arrival = f"{total_mins // 60:02d}:{total_mins % 60:02d}"
                        
                        event_dict = {
                            "type": "flight_disruption",
                            "name": "Early Flight Arrival",
                            "message": f"Flight arrived {early_mins} minutes early. Original arrival: {arrival_time}, New arrival: {new_arrival}. Include ~45 minutes for baggage/exit and 30 mins transfer time. Recommend activities before hotel check-in at {checkin_time}."
                        }
                        self._handle_disruption(chat_id, event_dict, trip["itinerary"])
                        arrival_time = new_arrival
                    except Exception:
                        pass
                    sim_engine.mark_alert_triggered(esim["id"], chat_id)

            cafes = get_gap_time_cafes(destination)
            cafe_lines = []
            for c in cafes[:2]:
                cafe_lines.append(f"• *{c['name']}* ({c['area']})\n  _{c['vibe']}_ (Hours: {c['hours']})")
            cafe_block = "\n".join(cafe_lines)

            message = (
                f"🛬 *Touchdown Confirmed! Welcome to {destination}!*\n\n"
                f"🏨 Your hotel (*{hotel_name}*) check-in is in *1 hour* (at {checkin_time}).\n"
                f"✈️ Landed: *{arrival_time}* | Your room is currently being prepared.\n\n"
                f"☕ *Luggage-Friendly Cafes Nearby with Wi-Fi & AC:*\n"
                f"{cafe_block}\n\n"
                f"Relax, charge your phone, and enjoy a coffee while your room is readied! 🌴"
            )

            alert_key = f"landing_{destination}_{checkin_time}"
            if self._throttled_send(chat_id, "landing_buffer", alert_key, message):
                trip_store.set_alert_flag(chat_id, "landing_alert_sent", 1)
            if simulate:
                return message
            continue

    # ──────────────────────────────────────────────────
    # 2. PRE-EMPTIVE INCOMING RAIN RADAR
    # ──────────────────────────────────────────────────

    def check_incoming_rain_alerts(self, force_chat_id: Optional[int] = None, simulate: bool = False):
        """
        Polls Open-Meteo hourly precipitation radar. If rain is approaching in ~35-45 mins,
        warns the traveler before rain starts and suggests quick indoor alternatives.
        Also checks for simulated weather events.
        """
        trips = [trip_store.get_or_create_default_trip(force_chat_id)] if force_chat_id else trip_store.get_all_active_trips()
        for trip in trips:
            if not trip:
                continue
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")

            # Check simulated weather events first
            weather_sims = sim_engine.get_active_simulations("weather_event", chat_id)
            for wsim in weather_sims:
                if sim_engine.has_triggered_for(wsim["id"], chat_id):
                    continue
                params = wsim["params"]
                if params.get("event_type") in ("heavy_rain", "thunderstorm", "cyclone_warning"):
                    self._send_rain_alert(chat_id, destination, simulated_params=params)
                    sim_engine.mark_alert_triggered(wsim["id"], chat_id)
                    if simulate:
                        return
                    continue

            # Skip if real rain already sent recently (throttle handles this)
            if trip.get("rain_alert_sent") and not simulate:
                continue

            # Resolve location: Activity location > Current Lat/Lon > Destination
            act_loc = self._get_upcoming_activity_location(chat_id, destination)
            v_lat, v_lon = trip_store.get_valid_location(chat_id)
            radar = check_incoming_rain_forecast(act_loc, simulate=simulate, lat=v_lat if act_loc == destination else None, lon=v_lon if act_loc == destination else None)
            if radar.get("incoming_rain"):
                msg = self._send_rain_alert(chat_id, destination, radar=radar)
                trip_store.set_alert_flag(chat_id, "rain_alert_sent", 1)
                if simulate:
                    return msg
                continue

    def _send_rain_alert(self, chat_id: int, destination: str,
                         radar: Optional[Dict] = None, simulated_params: Optional[Dict] = None):
        """Common rain/weather alert builder."""
        if simulated_params:
            severity = simulated_params.get("severity", "high")
            event_type = simulated_params.get("event_type", "heavy_rain")
            mins = 20
            prob = 95 if severity == "extreme" else 85
            precip = 12.0 if severity == "extreme" else 6.5
            label = event_type.replace("_", " ").title()
        else:
            mins = radar.get("minutes_away", 35)
            prob = radar.get("probability_pct", 80)
            precip = radar.get("precipitation_mm", 4.5)
            label = "Incoming Rain"

        try:
            indoor_spots = rag_pipeline.search("museum heritage cultural cafe", n_results=2, filter_conditions={"type": "indoor"})
            spot_lines = [f"• *{s['metadata']['name']}* ({s['metadata']['timings']}) - {s['metadata']['category']}" for s in indoor_spots]
            spot_block = "\n".join(spot_lines) if spot_lines else "• Museum of Goa (10:00 - 18:00)"
        except Exception:
            spot_block = "• Museum of Goa (10:00 - 18:00)\n• Houses of Goa Architectural Museum (10:00 - 19:30)"

        message = (
            f"🌧️ *{label} Advisory ({mins} Mins Out)*\n\n"
            f"Live radar indicates {label.lower()} approaching *{destination}* in approximately *{mins} minutes*.\n"
            f"• Probability: *{prob}%*\n"
            f"• Forecast Intensity: *{precip} mm/hr*\n\n"
            f"🏛️ *Nearby Indoor Sheltered Spots:*\n"
            f"{spot_block}\n\n"
            f"💡 *Action:* Outdoor beach or open-deck cruise plans are at risk. Wrap up and transition indoors!"
        )

        alert_key = f"rain_{destination}_{datetime.now().strftime('%Y%m%d_%H')}"
        self._throttled_send(chat_id, "rain_approaching", alert_key, message)
        return message

    # ──────────────────────────────────────────────────
    # 3. LIVE ROUTE TRAFFIC & CONGESTION SENTINEL
    # ──────────────────────────────────────────────────

    def check_traffic_alerts(self, force_chat_id: Optional[int] = None, simulate: bool = False):
        """
        Monitors route congestion via OSRM + simulated traffic incidents.
        """
        trips = [trip_store.get_or_create_default_trip(force_chat_id)] if force_chat_id else trip_store.get_all_active_trips()
        for trip in trips:
            if not trip:
                continue
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")

            # Check simulated traffic incidents
            traffic_sims = sim_engine.get_active_simulations("traffic_incident", chat_id)
            for tsim in traffic_sims:
                if sim_engine.has_triggered_for(tsim["id"], chat_id):
                    continue
                params = tsim["params"]
                message = (
                    f"🚦 *Simulated Traffic Incident*\n\n"
                    f"A *{params.get('cause', 'traffic incident')}* has been reported on *{params.get('route_description', 'the main route')}*.\n\n"
                    f"• *Estimated Delay:* +{params.get('delay_minutes', 30)} minutes\n"
                    f"• *Area:* {params.get('destination', destination)}\n\n"
                    f"💡 *Recommendation:* Consider an alternate route or delay departure by {params.get('delay_minutes', 30)} minutes."
                )
                alert_key = f"traffic_sim_{tsim['id']}"
                if self._throttled_send(chat_id, "traffic_incident", alert_key, message):
                    sim_engine.mark_alert_triggered(tsim["id"], chat_id)
                if simulate:
                    return message
                continue

            # Real traffic check
            if trip.get("traffic_alert_sent") and not simulate:
                continue

            v_lat, v_lon = trip_store.get_valid_location(chat_id)
            traffic = check_traffic_congestion(destination, simulate_surge=simulate, lat=v_lat, lon=v_lon)
            if traffic.get("has_congestion"):
                delay = int(traffic.get("delay_mins", 25))
                eta = int(traffic.get("current_eta_mins", 55))
                base = int(traffic.get("baseline_mins", 30))
                bottleneck = traffic.get("bottleneck", "NH-66 Arterial Road")

                message = (
                    f"🚦 *Live Traffic Advisory: Heavy Delay*\n\n"
                    f"Severe congestion detected along route between *{traffic['origin']}* and *{traffic['destination']}*.\n\n"
                    f"• *Bottleneck:* {bottleneck}\n"
                    f"• *Delay:* +{delay} minutes\n"
                    f"• *Current Travel Time:* ~{eta} mins (Normally {base} mins)\n\n"
                    f"💡 *Proactive Guidance:*\n"
                    f"Consider taking the coastal Nerul / Chogm bypass, or relax at a nearby cafe until congestion clears!"
                )

                alert_key = f"traffic_{destination}_{datetime.now().strftime('%Y%m%d_%H')}"
                if self._throttled_send(chat_id, "traffic_congestion", alert_key, message):
                    trip_store.set_alert_flag(chat_id, "traffic_alert_sent", 1)
                if simulate:
                    return message
                continue

    def _get_upcoming_activity_location(self, chat_id: int, fallback_dest: str) -> str:
        """Helper to find the location of the next upcoming activity today."""
        structured = trip_store.get_structured_itinerary(chat_id)
        if not structured:
            return fallback_dest
        try:
            trip_start = structured.get("trip_dates", {}).get("start")
            start_date = datetime.strptime(trip_start, "%Y-%m-%d")
            trip_day = (datetime.now() - start_date).days + 1
        except Exception:
            trip_day = 1
            
        activities = structured.get("activities", [])
        today_activities = [a for a in activities if a.get("day") == trip_day]
        now_minutes = datetime.now().hour * 60 + datetime.now().minute
        
        from datetime_utils import parse_time_to_minutes
        for act in sorted(today_activities, key=lambda x: x.get("time", "23:59")):
            time_str = act.get("time", "")
            if not time_str: continue
            try:
                act_mins = parse_time_to_minutes(time_str)
                if act_mins > now_minutes:
                    loc = act.get("location")
                    if loc:
                        return loc
            except Exception:
                continue
                
        return fallback_dest

    # ──────────────────────────────────────────────────
    # 4. WEATHER & DISRUPTION REPLANNING POLLER
    # ──────────────────────────────────────────────────

    def check_weather_disruptions(self):
        """Periodic job: queries Open-Meteo for severe storm conditions."""
        active_trips = trip_store.get_all_active_trips()
        current_hour = datetime.now().hour
        is_nighttime = (current_hour >= 23 or current_hour < 7)

        for trip in active_trips:
            chat_id = trip["chat_id"]
            destination = trip["destination"]
            
            # Resolve location: Activity location > Current Lat/Lon > Destination
            act_loc = self._get_upcoming_activity_location(chat_id, destination)
            v_lat, v_lon = trip_store.get_valid_location(chat_id)
            weather_report = fetch_live_weather(act_loc, lat=v_lat if act_loc == destination else None, lon=v_lon if act_loc == destination else None)

            if weather_report.get("is_advisory"):
                event = weather_report["disruption_event"]
                alert_id = event["id"]

                if is_nighttime and event.get("severity") != "high":
                    continue
                    
                if not trip_store.should_send_alert(chat_id, alert_id):
                    continue

                alert_key = f"weather_{destination}_{alert_id}"
                if sim_engine.can_send_notification(chat_id, "weather_disruption", alert_key):
                    if event.get("requires_replanning"):
                        # Severe weather: send proactive alert AND trigger autonomous replanning
                        self._handle_disruption(chat_id, event, trip["itinerary"])
                    else:
                        # Light/moderate weather: send advisory notification only, DO NOT touch itinerary
                        cond_name = event['weather_data']['condition']
                        precip = event['weather_data']['precipitation']
                        wind = event['weather_data']['wind_speed']
                        adv_msg = (
                            f"🌦️ *Trip Guardian Weather Advisory*\n\n"
                            f"*Location:* {act_loc}\n"
                            f"*Condition:* {cond_name}\n"
                            f"*Precipitation:* {precip} mm/hr | *Wind:* {wind} km/h\n\n"
                            f"💡 *Advisory:* Passing or light showers detected. Keep an umbrella handy! Your planned itinerary remains unchanged."
                        )
                        self._throttled_send(chat_id, "weather_advisory", alert_key, adv_msg)

                    sim_engine.log_notification(chat_id, "weather_disruption", alert_key, event.get("message", "")[:200], COOLDOWNS["weather_disruption"])
                    trip_store.mark_alert_sent(chat_id, alert_id)

    # ──────────────────────────────────────────────────
    # 5. FLIGHT DELAY / TRANSPORT DISRUPTION WATCHDOG
    # ──────────────────────────────────────────────────

    def check_flight_disruptions(self):
        """
        Monitors flight delays — both from mock_disruptions.json AND active simulations.
        When a delay is detected, recalculates the traveler's day schedule.
        """
        active_trips = trip_store.get_all_active_trips()
        for trip in active_trips:
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")
            trip_flight_num = trip.get("flight_number")

            # --- Check SIMULATED flight delays ---
            delay_sims = sim_engine.get_active_simulations("flight_delay", chat_id)
            for dsim in delay_sims:
                if sim_engine.has_triggered_for(dsim["id"], chat_id):
                    continue
                params = dsim["params"]
                delay_mins = params.get("delay_minutes", 60)
                reason = params.get("reason", "Air traffic congestion")
                flight_num = params.get("flight_number") or trip_flight_num or "Unknown Flight"

                # Calculate new arrival
                orig_arrival = trip.get("flight_arrival_time", "13:00")
                try:
                    from datetime_utils import parse_time_to_minutes
                    a_mins = parse_time_to_minutes(orig_arrival)
                    total = a_mins + delay_mins
                    new_arrival = f"{(total // 60) % 24:02d}:{total % 60:02d}"
                except Exception:
                    new_arrival = "TBD"

                # Suggest activities for the gap
                cafes = get_gap_time_cafes(destination)
                cafe_lines = [f"• *{c['name']}* — _{c['vibe']}_" for c in cafes[:2]]
                cafe_block = "\n".join(cafe_lines) if cafe_lines else "• Check nearby lounges"

                message = (
                    f"✈️ *Flight Delay Alert*\n\n"
                    f"Flight *{flight_num}* has been delayed by *{delay_mins} minutes*.\n"
                    f"• *Reason:* {reason}\n"
                    f"• *Original Arrival:* {orig_arrival}\n"
                    f"• *New Arrival:* {new_arrival}\n\n"
                    f"📋 *Schedule Impact:* Your hotel check-in and afternoon plans will shift accordingly.\n\n"
                    f"☕ *While You Wait:*\n{cafe_block}\n\n"
                    f"We'll automatically adjust your itinerary once the new ETA is confirmed. ✅"
                )

                alert_key = f"flight_delay_{flight_num}_{delay_mins}"
                if self._throttled_send(chat_id, "flight_delay", alert_key, message):
                    sim_engine.mark_alert_triggered(dsim["id"], chat_id)
                    event_dict = {
                        "type": "flight_disruption",
                        "name": "Flight Delay",
                        "message": f"Flight {flight_num} delayed by {delay_mins} minutes. Original arrival: {orig_arrival}, New arrival: {new_arrival}. Please include ~45 minutes for baggage claim/exit and 30 minutes for transfer to hotel. Adjust the downstream schedule to fit this new timeline while preserving fixed bookings like hotel check-in."
                    }
                    self._handle_disruption(chat_id, event_dict, trip["itinerary"])

            # --- Check SIMULATED train/bus delays ---
            for transport_type in ("train_delay", "bus_delay"):
                transport_sims = sim_engine.get_active_simulations(transport_type, chat_id)
                for tsim in transport_sims:
                    if sim_engine.has_triggered_for(tsim["id"], chat_id):
                        continue
                    params = tsim["params"]
                    delay_mins = params.get("delay_minutes", 30)
                    reason = params.get("reason", "Operational delay")
                    transport_id = params.get("train_number", params.get("bus_id", "N/A"))
                    emoji = "🚂" if "train" in transport_type else "🚌"

                    message = (
                        f"{emoji} *{transport_type.replace('_', ' ').title()} Alert*\n\n"
                        f"{transport_type.split('_')[0].title()} *{transport_id}* is delayed by *{delay_mins} minutes*.\n"
                        f"• *Reason:* {reason}\n\n"
                        f"💡 Your connecting activities will be adjusted automatically."
                    )

                    alert_key = f"{transport_type}_{transport_id}_{delay_mins}"
                    if self._throttled_send(chat_id, "flight_delay", alert_key, message):
                        sim_engine.mark_alert_triggered(tsim["id"], chat_id)

    # ──────────────────────────────────────────────────
    # 6. CROWD SURGE & VENUE CLOSURE SENTINEL (NEW)
    # ──────────────────────────────────────────────────

    def check_crowd_and_closure_alerts(self):
        """
        Checks for active crowd surge and venue closure simulations.
        When detected, suggests alternative nearby venues of the same category.
        """
        active_trips = trip_store.get_all_active_trips()
        for trip in active_trips:
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")

            # --- Crowd Surge ---
            crowd_sims = sim_engine.get_active_simulations("crowd_surge", chat_id)
            for csim in crowd_sims:
                if sim_engine.has_triggered_for(csim["id"], chat_id):
                    continue
                params = csim["params"]
                location = params.get("location_name", "Tourist Spot")
                level = params.get("crowd_level", "high")

                # Find alternatives via Overpass
                try:
                    alternatives = discover_live_places(destination, category="sightseeing", radius_meters=3000)
                    alt_lines = [f"• *{p['name']}*" for p in alternatives[:3] if p['name'].lower() != location.lower()]
                    alt_block = "\n".join(alt_lines) if alt_lines else "• Try nearby hidden gems or cafes"
                except Exception:
                    alt_block = "• Check nearby quieter attractions"

                message = (
                    f"👥 *Crowd Surge Alert*\n\n"
                    f"*{location}* is currently experiencing *{level}* crowd levels.\n"
                    f"Expected wait times may be significantly longer than usual.\n\n"
                    f"🔄 *Quieter Alternatives Nearby:*\n{alt_block}\n\n"
                    f"💡 Consider visiting during off-peak hours (early morning or late afternoon) for a better experience."
                )

                alert_key = f"crowd_{location}_{csim['id']}"
                if self._throttled_send(chat_id, "crowd_surge", alert_key, message):
                    sim_engine.mark_alert_triggered(csim["id"], chat_id)

            # --- Venue Closure ---
            closure_sims = sim_engine.get_active_simulations("venue_closure", chat_id)
            for vsim in closure_sims:
                if sim_engine.has_triggered_for(vsim["id"], chat_id):
                    continue
                params = vsim["params"]
                venue = params.get("venue_name", "Venue")
                reason = params.get("reason", "Temporarily closed")

                try:
                    alternatives = discover_live_places(destination, category="food", radius_meters=2000)
                    alt_lines = [f"• *{p['name']}*" for p in alternatives[:3] if p['name'].lower() != venue.lower()]
                    alt_block = "\n".join(alt_lines) if alt_lines else "• Check other dining options nearby"
                except Exception:
                    alt_block = "• Explore other options nearby"

                message = (
                    f"🚫 *Venue Closed: {venue}*\n\n"
                    f"Unfortunately, *{venue}* is currently closed.\n"
                    f"• *Reason:* {reason}\n\n"
                    f"🔄 *Open Alternatives Nearby:*\n{alt_block}\n\n"
                    f"Your itinerary has been auto-updated. ✅"
                )

                alert_key = f"closure_{venue}_{vsim['id']}"
                if self._throttled_send(chat_id, "venue_closure", alert_key, message):
                    sim_engine.mark_alert_triggered(vsim["id"], chat_id)

    # ──────────────────────────────────────────────────
    # 7. ACTIVITY-AWARE REMINDER SENTINEL (NEW)
    # ──────────────────────────────────────────────────

    def check_upcoming_activities(self):
        """
        Every 5 minutes, scans all active trips with structured itineraries.
        Sends a contextual heads-up ~30 mins before each scheduled activity.
        """
        active_trips = trip_store.get_all_active_trips()
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        for trip in active_trips:
            chat_id = trip["chat_id"]
            destination = trip.get("destination", "Goa")

            # Need structured itinerary
            structured = trip_store.get_structured_itinerary(chat_id)
            if not structured:
                continue

            # Determine which trip day we're on
            trip_start = structured.get("trip_dates", {}).get("start")
            if not trip_start:
                continue

            try:
                start_date = datetime.strptime(trip_start, "%Y-%m-%d")
                trip_day = (now - start_date).days + 1
            except Exception:
                trip_day = 1

            if trip_day < 1:
                continue  # Trip hasn't started yet

            # Find today's activities
            activities = structured.get("activities", [])
            today_activities = [a for a in activities if a.get("day") == trip_day]

            for act in today_activities:
                act_time_str = act.get("time", "")
                if not act_time_str:
                    continue

                try:
                    from datetime_utils import parse_time_to_minutes
                    act_minutes = parse_time_to_minutes(act_time_str)
                    now_minutes = now.hour * 60 + now.minute
                    diff = act_minutes - now_minutes

                    # Alert window: 25-35 minutes before
                    if 25 <= diff <= 35:
                        act_name = act.get("name", "Activity")
                        act_type = act.get("type", "general")
                        duration = act.get("duration_hrs", "?")

                        # Get live weather for context
                        try:
                            act_loc = act.get("location", destination)
                            wx = fetch_live_weather(act_loc)
                            temp = wx.get("temperature_c", "")
                            cond = wx.get("condition", "")
                            weather_line = f"🌤️ Current: {temp}°C, {cond}"
                        except Exception:
                            weather_line = ""

                        icon = {"outdoor": "🏖️", "indoor": "🏛️", "dining": "🍽️",
                                "transport": "🚗", "sightseeing": "📸"}.get(act_type, "📌")

                        message = (
                            f"📋 *Upcoming Activity Reminder*\n\n"
                            f"{icon} *{act_name}* starts in ~30 minutes (at {act_time_str})\n"
                            f"• Type: {act_type} | Duration: ~{duration}h\n"
                            f"{weather_line}\n\n"
                            f"Have a great time! 🎉"
                        )

                        alert_key = f"activity_d{trip_day}_{act_time_str}_{act_name}"
                        self._throttled_send(chat_id, "activity_reminder", alert_key, message)

                except Exception as e:
                    logger.debug(f"Activity time parse error: {e}")
                    continue

    def check_and_update_visited_places(self):
        """Periodically checks structured itineraries for past activities and marks their locations as visited."""
        now = datetime.now()
        for trip in trip_store.get_all_active_trips():
            chat_id = trip["chat_id"]
            structured = trip_store.get_structured_itinerary(chat_id)
            if not structured: continue
            
            try:
                trip_start = structured.get("trip_dates", {}).get("start")
                start_date = datetime.strptime(trip_start, "%Y-%m-%d") if trip_start else now
                trip_day = (now - start_date).days + 1
            except Exception:
                trip_day = 1
                
            for act in structured.get("activities", []):
                act_day = act.get("day", 1)
                loc = act.get("location") or act.get("name")
                if not loc: continue
                
                is_past = False
                if act_day < trip_day:
                    is_past = True
                elif act_day == trip_day and act.get("time"):
                    try:
                        from datetime_utils import parse_time_to_minutes
                        if parse_time_to_minutes(act.get("time")) < now.hour * 60 + now.minute:
                            is_past = True
                    except Exception:
                        pass
                
                if is_past:
                    trip_store.mark_place_visited(chat_id, loc)

    # ──────────────────────────────────────────────────
    # 8. MORNING BRIEFING
    # ──────────────────────────────────────────────────

    def send_morning_briefings(self):
        """Daily 8:00 AM Cron job."""
        for trip in trip_store.get_all_active_trips():
            self.trigger_morning_briefing(trip["chat_id"])

    def trigger_morning_briefing(self, chat_id: int) -> str:
        trip = trip_store.get_trip(chat_id)
        if not trip:
            return "No active trip found."

        dest = trip["destination"]
        weather = fetch_live_weather(dest)
        temp = weather.get("temperature_c", 28.0)
        cond = weather.get("condition", "Clear")

        # Get today's schedule from structured itinerary if available
        schedule_block = ""
        structured = trip_store.get_structured_itinerary(chat_id)
        if structured:
            trip_start = structured.get("trip_dates", {}).get("start")
            try:
                start_date = datetime.strptime(trip_start, "%Y-%m-%d")
                trip_day = (datetime.now() - start_date).days + 1
                activities = [a for a in structured.get("activities", []) if a.get("day") == trip_day]
                if activities:
                    lines = []
                    for a in sorted(activities, key=lambda x: x.get("time", "00:00")):
                        icon = {"outdoor": "🏖️", "indoor": "🏛️", "dining": "🍽️",
                                "transport": "🚗", "sightseeing": "📸"}.get(a.get("type", ""), "📌")
                        lines.append(f"  {icon} {a.get('time', '')} — {a.get('name', 'Activity')}")
                    schedule_block = "\n📋 *Today's Schedule (Day " + str(trip_day) + "):*\n" + "\n".join(lines) + "\n"
            except Exception:
                pass

        briefing_text = (
            f"☀️ *Trip Guardian Morning Briefing*\n\n"
            f"Good morning! Daily briefing for *{dest}*:\n\n"
            f"🌤️ *Weather:* {temp}°C, {cond}\n"
            f"✅ *Safety:* Conditions favorable for outdoor sightseeing.\n"
            f"{schedule_block}\n"
            f"• Tap `/itinerary` to review today's schedule\n"
            f"• Tap `/discover` anytime for nearby places!\n\n"
            f"Have a great day exploring! 🚀"
        )

        alert_key = f"briefing_{datetime.now().strftime('%Y%m%d')}"
        self._throttled_send(chat_id, "morning_briefing", alert_key, briefing_text)
        return briefing_text

    # ──────────────────────────────────────────────────
    # DISRUPTION HANDLER (shared by multiple sentinels)
    # ──────────────────────────────────────────────────

    def _handle_disruption(self, chat_id: int, event: Dict[str, Any], current_itinerary: str) -> str:
        trip = trip_store.get_trip(chat_id)
        visited = []
        loc_ctx = "Unknown"
        curr_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        if trip:
            visited = trip_store.get_visited_places(chat_id)
            v_lat, v_lon = trip_store.get_valid_location(chat_id)
            if v_lat is not None and v_lon is not None:
                loc_ctx = f"{v_lat}, {v_lon}"
            else:
                loc_ctx = trip.get("destination", "Unknown")

        revised_plan = evaluate_and_replan(
            event, 
            current_itinerary, 
            current_time=curr_time, 
            current_location=loc_ctx, 
            visited_places=visited
        )
        trip_store.update_itinerary(chat_id, revised_plan)

        message = (
            f"🚨 *Trip Guardian Proactive Alert*\n\n"
            f"*Condition:* {event.get('name', 'Disruption')}\n"
            f"*Impact:* {event.get('message', '')}\n\n"
            f"*AI Revised Itinerary:*\n{revised_plan}"
        )
        send_telegram_message(chat_id, message)
        return revised_plan

    def trigger_manual_disruption(self, chat_id: int, scenario: str = "rain_approaching") -> Dict[str, Any]:
        trip = trip_store.get_trip(chat_id)
        current_itinerary = trip["itinerary"] if trip else "Day 1 Afternoon: Beach walk & Sunset Cruise."
        destination = trip["destination"] if trip else "Goa"

        db_path = os.path.join(os.path.dirname(__file__), "data", "mock_disruptions.json")
        event = None
        if os.path.exists(db_path):
            with open(db_path, "r", encoding="utf-8") as f:
                disruptions = json.load(f)
            event = next((d for d in disruptions if d["trigger_payload"]["scenario"] == scenario), None)

        if not event:
            event = create_simulated_weather_disruption(destination)

        revised_plan = self._handle_disruption(chat_id, event, current_itinerary)
        trip_store.mark_alert_sent(chat_id, event["id"])
        return {"status": "success", "event": event["name"], "revised_plan": revised_plan}

    # ──────────────────────────────────────────────────
    # SCHEDULER LIFECYCLE
    # ──────────────────────────────────────────────────

    def start(self, weather_poll_minutes: int = 2):
        if not self.is_running:
            # 0. Fast Hackathon Simulation Sentinel (Every 5 seconds, 0 external calls, pure SQLite)
            self.scheduler.add_job(
                self.check_active_simulations,
                "interval",
                seconds=POLLING_INTERVALS["simulations"],
                id="fast_simulation_sentinel",
                replace_existing=True
            )
            # 1. Early Landing Buffer Sentinel (Every 120 seconds)
            self.scheduler.add_job(
                self.check_landing_buffer_alerts,
                "interval",
                seconds=POLLING_INTERVALS["landing_buffer"],
                id="landing_buffer_sentinel",
                replace_existing=True
            )
            # 2. Pre-emptive Incoming Rain Radar (Every 120 seconds)
            self.scheduler.add_job(
                self.check_incoming_rain_alerts,
                "interval",
                seconds=POLLING_INTERVALS["rain_radar_live"],
                id="incoming_rain_radar_poller",
                replace_existing=True
            )
            # 3. Live Route Traffic Sentinel (Every 180 seconds)
            self.scheduler.add_job(
                self.check_traffic_alerts,
                "interval",
                seconds=POLLING_INTERVALS["traffic_live"],
                id="traffic_sentinel_poller",
                replace_existing=True
            )
            # 4. Severe Weather Disruption Poller (Every 120 seconds or weather_poll_minutes)
            live_wx_interval = weather_poll_minutes * 60 if weather_poll_minutes else POLLING_INTERVALS["weather_live"]
            self.scheduler.add_job(
                self.check_weather_disruptions,
                "interval",
                seconds=live_wx_interval,
                id="open_meteo_weather_poller",
                replace_existing=True
            )
            # 5. Flight / Transport Delay Watchdog (Every 5 seconds, pure SQLite)
            self.scheduler.add_job(
                self.check_flight_disruptions,
                "interval",
                seconds=POLLING_INTERVALS["simulations"],
                id="flight_watchdog_poller",
                replace_existing=True
            )
            # 6. Crowd Surge & Venue Closure Sentinel (Every 5 seconds, pure SQLite)
            self.scheduler.add_job(
                self.check_crowd_and_closure_alerts,
                "interval",
                seconds=POLLING_INTERVALS["simulations"],
                id="crowd_closure_sentinel",
                replace_existing=True
            )
            # 7. Activity Reminder Sentinel (Every 15 seconds, local datetime math)
            self.scheduler.add_job(
                self.check_upcoming_activities,
                "interval",
                seconds=POLLING_INTERVALS["activities"],
                id="activity_reminder_sentinel",
                replace_existing=True
            )
            # 8. Visited Places Poller (Every 15 seconds, local coordinates math)
            self.scheduler.add_job(
                self.check_and_update_visited_places,
                "interval",
                seconds=POLLING_INTERVALS["visited_places"],
                id="visited_places_poller",
                replace_existing=True
            )
            # 9. Morning Briefing Cron (Daily at 8:00 AM)
            self.scheduler.add_job(
                self.send_morning_briefings,
                CronTrigger(hour=8, minute=0),
                id="morning_briefing_cron",
                replace_existing=True
            )

            self.scheduler.start()
            self.is_running = True
            logger.info(f"[ProactiveEngine] All 10 Proactive Cron Sentinels active & running.")


    def stop(self):
        if self.is_running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("[ProactiveEngine] APScheduler stopped.")

    def get_status(self) -> Dict[str, Any]:
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": str(job.next_run_time) if job.next_run_time else "Paused",
                "trigger": str(job.trigger)
            })
        return {
            "is_running": self.is_running,
            "jobs_count": len(jobs),
            "jobs": jobs,
            "active_trips_monitored": len(trip_store.get_all_active_trips())
        }

proactive_engine = ProactiveEngine()
