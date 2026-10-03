"""
streamlit_dashboard.py — Trip Guardian Simulation Control Center
Provides a UI for triggering mock disruptions (delays, weather, crowd, closure).
Run with: streamlit run backend/streamlit_dashboard.py
"""

import streamlit as st
import sqlite3
import pandas as pd
import json
import time
from datetime import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trips.db")

# Add simulation engine to path so we can import it
import sys
sys.path.append(os.path.dirname(__file__))
from simulation_engine import sim_engine

st.set_page_config(page_title="Trip Guardian Simulator", page_icon="🛡️", layout="wide")

st.title("🛡️ Trip Guardian — Simulation Control Center")
st.markdown("Trigger real-time disruptions, view active trips, and monitor AI re-routing behavior.")

# --- Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_active_trips():
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query(
                "SELECT chat_id, destination, flight_number, hotel_name, trip_start_date, trip_end_date, status FROM trips_multi ORDER BY status, trip_start_date",
                conn
            )
            return df
    except Exception as e:
        st.error(f"Failed to load trips: {e}")
        return pd.DataFrame()

def load_notification_log():
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query("SELECT * FROM notification_log ORDER BY sent_at DESC LIMIT 20", conn)
            if not df.empty:
                df['sent_at'] = pd.to_datetime(df['sent_at'], unit='s')
                df['cooldown_until'] = pd.to_datetime(df['cooldown_until'], unit='s')
            return df
    except Exception:
        return pd.DataFrame()

def load_active_simulations():
    try:
        sims = sim_engine.get_active_simulations()
        if sims:
            df = pd.DataFrame(sims)
            df['created_at'] = pd.to_datetime(df['created_at'], unit='s')
            df['expires_at'] = pd.to_datetime(df['expires_at'], unit='s')
            return df
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# --- Layout ---
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📊 Active Trips Monitor")
    trips_df = load_active_trips()
    if not trips_df.empty:
        st.dataframe(trips_df, use_container_width=True, hide_index=True)
        active_chat_ids = trips_df["chat_id"].tolist()
    else:
        st.info("No active trips found in database.")
        active_chat_ids = []

    st.subheader("⏱️ Active Simulations")
    sims_df = load_active_simulations()
    if not sims_df.empty:
        st.dataframe(sims_df[["id", "sim_type", "status", "expires_at"]], use_container_width=True, hide_index=True)
        
        # Deactivation tool
        if st.button("Deactivate All Simulations"):
            for sim_id in sims_df["id"]:
                sim_engine.deactivate_simulation(sim_id)
            st.success("All simulations deactivated.")
            st.rerun()
    else:
        st.info("No active simulations.")


with col2:
    st.subheader("🎛️ Simulation Triggers")
    
    target_options = [str(cid) for cid in active_chat_ids]
    target_selection = st.selectbox("Target User (Required)", target_options) if target_options else None
    target_id = int(target_selection) if target_selection else None
    
    if target_id:
        st.subheader("📍 Location Simulation")
        from trip_store import trip_store
        
        trip_data = trip_store.get_trip(target_id)
        current_loc = trip_store.get_current_location(target_id)
        
        st.markdown(f"**Current:** {current_loc['source'].upper() if current_loc else 'UNKNOWN'} — " + (f"{current_loc['latitude']:.4f}, {current_loc['longitude']:.4f}" if current_loc else "No location"))
        
        loc_mode = st.radio("Mode:", ["Real Location", "Demo Location"], index=1 if current_loc and current_loc['source'] == 'demo' else 0)
        
        if loc_mode == "Demo Location":
            demo_dest = st.selectbox("Predefined Destination:", ["Baga Beach, Goa", "Panjim, Goa", "Candolim, Goa", "Goa Airport", "Custom Coordinates"])
            
            if demo_dest == "Custom Coordinates":
                custom_lat = st.text_input("Custom Latitude", value=str(current_loc['latitude']) if current_loc else "15.5494")
                custom_lon = st.text_input("Custom Longitude", value=str(current_loc['longitude']) if current_loc else "73.7535")
                apply_lat, apply_lon = float(custom_lat), float(custom_lon)
            else:
                presets = {
                    "Baga Beach, Goa": (15.5523, 73.7517),
                    "Panjim, Goa": (15.4909, 73.8278),
                    "Candolim, Goa": (15.5183, 73.7667),
                    "Goa Airport": (15.3803, 73.8350)
                }
                apply_lat, apply_lon = presets[demo_dest]
                st.write(f"Coordinates: {apply_lat}, {apply_lon}")
                
            if st.button("Apply Location", type="primary"):
                trip_store.update_demo_location(target_id, apply_lat, apply_lon, is_demo=True)
                st.success(f"Demo location applied: {apply_lat}, {apply_lon}")
                time.sleep(0.5)
                st.rerun()
        else:
            if current_loc and current_loc['source'] == 'demo':
                if st.button("Switch to Real Location", type="primary"):
                    trip_store.update_demo_location(target_id, None, None, is_demo=False)
                    st.success("Switched to real location mode.")
                    time.sleep(0.5)
                    st.rerun()
                    
        st.divider()
    
    tab1, tab2, tab3, tab4 = st.tabs(["✈️ Flights", "🌧️ Weather", "🚦 Traffic", "👥 Venues"])

    with tab1:
        flight_num = st.text_input("Flight Number", value="6E-501")
        delay_mins = st.slider("Delay Minutes", 15, 360, 120)
        reason = st.selectbox("Delay Reason", ["Air traffic congestion", "Technical fault", "Weather conditions at origin"])
        if st.button("🚀 Trigger Flight Delay", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_flight_delay(flight_num, delay_mins, target_id, reason)
                st.success(f"Flight delay simulation created for {flight_num}!")
                time.sleep(1)
                st.rerun()
            
        st.divider()
        early_mins = st.slider("Early Arrival Minutes", 15, 120, 30)
        if st.button("🚀 Trigger Early Landing", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_flight_early(flight_num, target_id, early_mins)
                st.success(f"Early arrival simulation created for {flight_num}!")
                time.sleep(1)
                st.rerun()

    with tab2:
        dest = st.text_input("Destination", value="Goa")
        event = st.selectbox("Event Type", ["heavy_rain", "thunderstorm", "heatwave", "cyclone_warning"])
        severity = st.select_slider("Severity", ["low", "medium", "high", "extreme"], value="high")
        if st.button("🚀 Trigger Weather Event", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_weather_event(dest, target_id, event, severity)
                st.success(f"Weather event simulation created for {dest}!")
                time.sleep(1)
                st.rerun()

    with tab3:
        route = st.text_input("Route Description", value="Airport to Hotel Transfer")
        t_delay = st.slider("Traffic Delay (mins)", 15, 120, 45)
        t_cause = st.selectbox("Cause", ["Multi-vehicle accident", "Road construction", "VVIP Movement", "Waterlogging"])
        if st.button("🚀 Trigger Traffic Incident", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_traffic_incident(route, target_id, t_delay, t_cause, dest)
                st.success("Traffic incident simulation created!")
                time.sleep(1)
                st.rerun()

    with tab4:
        st.markdown("**Crowd Surge**")
        loc_name = st.text_input("Location Name", value="Calangute Beach")
        crowd_lvl = st.select_slider("Crowd Level", ["low", "moderate", "high", "extreme"], value="extreme")
        if st.button("🚀 Trigger Crowd Surge", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_crowd_surge(loc_name, target_id, crowd_lvl, dest)
                st.success("Crowd surge simulation created!")
                time.sleep(1)
                st.rerun()
            
        st.divider()
        st.markdown("**Venue Closure**")
        venue_name = st.text_input("Venue Name", value="Curlies Beach Shack")
        v_reason = st.selectbox("Closure Reason", ["Holiday closure", "Emergency maintenance", "Private event", "Staff shortage"])
        if st.button("🚀 Trigger Venue Closure", use_container_width=True):
            if not target_id:
                st.error("Target user required!")
            else:
                sim_engine.simulate_venue_closure(venue_name, target_id, v_reason, dest)
                st.success("Venue closure simulation created!")
                time.sleep(1)
                st.rerun()

st.divider()

st.subheader("📜 Live Notification Log (Throttling Check)")
log_df = load_notification_log()
if not log_df.empty:
    st.dataframe(
        log_df[["sent_at", "chat_id", "alert_type", "alert_key", "message_preview", "cooldown_until"]],
        use_container_width=True, hide_index=True
    )
    if st.button("Reset All Cooldowns"):
        sim_engine.clear_cooldowns()
        st.success("Cooldowns reset!")
        st.rerun()
else:
    st.info("No notifications sent yet.")
