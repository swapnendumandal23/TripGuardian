import os
import sys
import logging
import asyncio

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from weather_service import fetch_live_weather
from trip_store import trip_store
from scheduler import proactive_engine
from RAG_Pipeline import rag_pipeline
from agent_interface import generate_itinerary, client, DEFAULT_MODEL, analyze_intent, evaluate_and_replan, generate_personalized_itinerary, is_open, update_durable_preferences
from places_service import discover_live_places
from itinerary_parser import extract_text_from_bytes, parse_itinerary_with_llm, extract_trip_fields

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("trip_guardian_bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Conversation states for /plan wizard
DESTINATION, DURATION, BUDGET, INTERESTS = range(4)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📲 Test Phone Push", "⚡ MVP Demo Scenarios"],
        ["🗺️ Plan Trip", "📋 My Itinerary"],
        ["🌤️ Check Weather", "📍 Live Discovery"],
        ["🌅 Morning Briefing", "💡 Ask Concierge"],
        ["🔄 Reset Trip"]
    ],
    resize_keyboard=True
)

async def safe_reply(message, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Sends a message with markdown, falling back to plain text if formatting fails."""
    try:
        return await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"Markdown send failed ({e}), falling back to plain text.")
        try:
            return await message.reply_text(text, reply_markup=reply_markup, parse_mode=None)
        except Exception as e2:
            logger.error(f"Complete failure sending message: {e2}")
            return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and introduces the concierge capabilities."""
    user_name = update.effective_user.first_name or "Traveler"
    chat_id = update.effective_chat.id

    welcome_text = (
        f"👋 Welcome to *Trip Guardian*, {user_name}!\n\n"
        "I am your autonomous, real-time AI Travel Concierge & Disruption Guardian.\n\n"
        "🛡️ *Autonomous MVP Features:*\n"
        "• *Check-in Gap Concierge*: Land early before hotel check-in? I'll automatically find you nearby luggage-friendly cafes!\n"
        "• *Pre-emptive Weather Radar*: Rain approaching in 30 mins? I'll warn you and reroute you indoors before it starts.\n"
        "• *Live Route Traffic Sentinel*: Severe delays on route? I calculate detours and recommend buffer times.\n"
        "• *Morning Briefings*: Daily 8 AM wake-up schedule & weather overview.\n\n"
        "Tap **⚡ MVP Demo Scenarios** below to test these autonomous alerts instantly!"
    )
    await safe_reply(update.message, welcome_text, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Trip Guardian Commands:*\n\n"
        "• `/mvp` - Interactive test lab for all autonomous MVP scenarios\n"
        "• `/plan` - Start interactive trip planner\n"
        "• `/itinerary` - View active trip schedule\n"
        "• `/weather` - View live weather & outdoor safety\n"
        "• `/discover [cuisine/spot]` - Live OpenStreetMap place discovery\n"
        "• `/briefing` - Today's morning concierge briefing\n"
        "• `/flight <num>` - Track your flight (e.g. `/flight 6E-501`)\n"
        "• `/reset` - Clear current trip\n\n"
        "💬 You can also just type any question (e.g. _'Where can I catch the best sunset in Goa?'_) to search verified local spots!"
    )
    await safe_reply(update.message, help_text)

# --- Trip Planning Wizard (ConversationHandler) ---

async def plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update.message,
        "🗺️ *Step 1/4: Destination*\n\nWhere would you like to travel? (e.g., Goa, Jaipur, Mumbai, Kerala)",
        reply_markup=ReplyKeyboardRemove()
    )
    return DESTINATION

async def plan_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["plan_destination"] = update.message.text.strip()
    await safe_reply(
        update.message,
        f"📅 *Step 2/4: Duration & Dates*\n\nHow many days or what dates are you planning for {context.user_data['plan_destination']}? (e.g., '3 days', 'Oct 10 - Oct 13')"
    )
    return DURATION

async def plan_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["plan_duration"] = update.message.text.strip()
    await safe_reply(
        update.message,
        "💰 *Step 3/4: Budget*\n\nWhat is your travel budget? (e.g., 'Budget-friendly', 'Moderate', 'Luxury')"
    )
    return BUDGET

async def plan_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["plan_budget"] = update.message.text.strip()
    await safe_reply(
        update.message,
        "🎯 *Step 4/4: Interests*\n\nWhat are your top interests? (e.g., 'Beaches, seafood, watersports, vibrant nightlife')"
    )
    return INTERESTS

async def plan_interests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    interests = update.message.text.strip()
    destination = context.user_data.get("plan_destination", "Goa")
    duration = context.user_data.get("plan_duration", "3 days")
    budget = context.user_data.get("plan_budget", "Moderate")

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    status_msg = await safe_reply(update.message, "🤖 Searching verified spots & crafting your customized itinerary...")

    # Update durable preferences
    existing_prefs = trip_store.get_user_preferences(chat_id)
    new_prefs = update_durable_preferences(existing_prefs, interests)
    trip_store.update_user_preferences(chat_id, new_prefs)

    combined_interests = f"{interests}. Global preferences: {new_prefs}" if new_prefs else interests

    # Run blocking AI generation in a threadpool
    try:
        itinerary = await asyncio.to_thread(
            generate_itinerary,
            destination=destination,
            dates=duration,
            budget=budget,
            interests=combined_interests
        )
    except Exception as e:
        logger.error(f"Error in generate_itinerary: {e}")
        itinerary = f"📍 Custom Itinerary for {destination}:\nEnjoy exploring local verified spots!"

    # Save to SQLite trip store
    trip_store.save_trip(chat_id, destination, itinerary)

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    await safe_reply(
        update.message,
        f"🎉 *Your Itinerary for {destination} is Ready!*\n\n{itinerary}\n\n"
        "🛡️ *Trip Guardian Active:* Persistent SQLite monitoring enabled. Watching weather, arrival gaps, and road traffic in the background.",
        reply_markup=MAIN_KEYBOARD
    )
    return ConversationHandler.END

async def plan_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update.message, "Trip planning cancelled.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END

# --- MVP Interactive Scenarios & Utilities ---

async def test_push_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Arms a real phone push notification test:
    Gives the user 10 seconds to lock their phone screen, then dispatches real Telegram push messages.
    """
    chat_id = update.effective_chat.id
    target_msg = update.effective_message
    trip_store.get_or_create_default_trip(chat_id)
    trip_store.reset_alert_flags(chat_id)

    countdown_text = (
        "📲 *LOCK-SCREEN NOTIFICATION TEST ARMED!*\n\n"
        "👉 *Press your phone's power button to LOCK YOUR SCREEN NOW* (or switch to home screen).\n\n"
        "⏱️ In *10 seconds*, Trip Guardian's autonomous background engine will push 3 real native notification banners to your phone tray:\n\n"
        "1️⃣ 🛬 *Early Landing 1-Hr Buffer Alert* (T+10s)\n"
        "2️⃣ 🚦 *Live Route Traffic Delay Advisory* (T+15s)\n"
        "3️⃣ 🌧️ *Incoming Rain Radar Advisory* (T+20s)\n\n"
        "🔒 *Lock your phone now and watch your lock screen!* 📳"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(countdown_text, parse_mode="Markdown")
    else:
        await safe_reply(target_msg, countdown_text, reply_markup=MAIN_KEYBOARD)

    async def _delayed_pushes():
        await asyncio.sleep(10)
        # Push 1: Early Landing Alert (sends as a new Telegram push message)
        await asyncio.to_thread(proactive_engine.check_landing_buffer_alerts, force_chat_id=chat_id, simulate=True)

        await asyncio.sleep(5)
        # Push 2: Live Traffic Advisory (sends as a new Telegram push message)
        await asyncio.to_thread(proactive_engine.check_traffic_alerts, force_chat_id=chat_id, simulate=True)

        await asyncio.sleep(5)
        # Push 3: Incoming Rain Radar Advisory (sends as a new Telegram push message)
        await asyncio.to_thread(proactive_engine.check_incoming_rain_alerts, force_chat_id=chat_id, simulate=True)

    asyncio.create_task(_delayed_pushes())

async def demo_mvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive control panel to simulate all MVP autonomous proactive notifications."""
    chat_id = update.effective_chat.id
    trip_store.get_or_create_default_trip(chat_id)

    keyboard = [
        [
            InlineKeyboardButton("📲 Test Phone Lock-Screen Push (10s Countdown)", callback_data="mvp_push_test"),
        ],
        [
            InlineKeyboardButton("🛬 1-Hr Early Landing + Cafe Finder", callback_data="mvp_landing"),
        ],
        [
            InlineKeyboardButton("🌧️ Incoming Rain (35m Radar Warning)", callback_data="mvp_rain"),
        ],
        [
            InlineKeyboardButton("🚦 Live Route Traffic Surge Delay", callback_data="mvp_traffic"),
        ],
        [
            InlineKeyboardButton("🌪️ Severe Monsoon Reroute & Replan", callback_data="mvp_monsoon"),
        ],
        [
            InlineKeyboardButton("⚡ Run All 3 In Sequence (Autopilot)", callback_data="mvp_all"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_reply(
        update.message,
        "⚡ *Trip Guardian MVP Autonomous Scenarios*\n\n"
        "Tap **📲 Test Phone Lock-Screen Push** to test notifications on your locked phone, or choose any sentinel below:",
        reply_markup=reply_markup
    )

simulate_command = demo_mvp_command

async def view_itinerary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    trip = trip_store.get_trip(chat_id)
    if trip and trip.get("status") == "completed":
        trip = None  # Do not expose completed trip as active context

    if not trip:
        await safe_reply(
            update.message,
            "You don't have an active itinerary saved yet!\nUse `/plan` to create one or upload a PDF itinerary.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    dest = trip["destination"]
    orig_itin = trip.get("original_itinerary", trip["itinerary"])
    rec_itin = trip.get("recommended_itinerary")
    hotel = trip.get("hotel_name") or "N/A"
    flight = trip.get("flight_number") or "N/A"
    start_date = trip.get("trip_start_date") or ""
    end_date = trip.get("trip_end_date") or ""
    date_range = f" ({start_date} → {end_date})" if start_date else ""

    msg = f"📋 *Your Itinerary: {dest}*{date_range}\n🏨 *Hotel:* {hotel}\n✈️ *Flight:* `{flight}`\n\n{orig_itin}"
    if rec_itin and rec_itin != orig_itin:
        msg += f"\n\n💡 *AI Recommended Additions:*\n\n{rec_itin}"

    await safe_reply(
        update.message,
        msg,
        reply_markup=MAIN_KEYBOARD
    )

async def check_weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    trip = trip_store.get_trip(chat_id)
    if trip and trip.get("status") == "completed":
        trip = None
    destination = trip["destination"] if trip else "Goa"
    user_lat, user_lon = trip_store.get_valid_location(chat_id)

    report = await asyncio.to_thread(fetch_live_weather, destination, user_lat, user_lon)
    if report.get("status") == "error":
        await safe_reply(update.message, f"⚠️ Weather check error: {report.get('error')}")
        return

    temp = report.get("temperature_c")
    cond = report.get("condition")
    precip = report.get("precipitation_mm")
    wind = report.get("wind_speed_kmh")
    disrupted = report.get("is_disrupted") # Severe / replanning required
    is_advisory = report.get("is_advisory") # Light/moderate rain advisory

    if disrupted:
        safety_icon = "🚨 Severe Weather Alert — Outdoor Activities Disrupted"
    elif is_advisory:
        safety_icon = "🌦️ Weather Advisory — Light/Passing Showers, Carry Umbrella"
    else:
        safety_icon = "✅ Conditions Safe for Outdoor Activities"

    msg = (
        f"🌤️ *Live Weather for {report.get('destination')}:*\n\n"
        f"• *Condition:* {cond}\n"
        f"• *Temperature:* {temp}°C\n"
        f"• *Precipitation:* {precip} mm\n"
        f"• *Wind Speed:* {wind} km/h\n\n"
        f"*{safety_icon}*"
    )
    await safe_reply(update.message, msg, reply_markup=MAIN_KEYBOARD)

async def discover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live queries OpenStreetMap (Overpass API) for dining & attractions."""
    chat_id = update.effective_chat.id
    trip = trip_store.get_trip(chat_id)
    if trip and trip.get("status") == "completed":
        trip = None
    destination = trip["destination"] if trip else "Goa"
    user_lat, user_lon = trip_store.get_valid_location(chat_id)

    query = " ".join(context.args) if context.args else "food attraction"
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    reply_msg = await asyncio.to_thread(execute_discovery, chat_id, query, query, user_lat, user_lon, destination, trip)
    await safe_reply(update.message, reply_msg, reply_markup=MAIN_KEYBOARD)

async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await asyncio.to_thread(proactive_engine.trigger_morning_briefing, chat_id)

async def flight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await safe_reply(update.message, "Please provide your flight number!\nExample: `/flight 6E-501`")
        return

    flight_num = context.args[0].upper().strip()
    trip_store.update_flight(chat_id, flight_num)
    await safe_reply(
        update.message,
        f"✈️ *Flight {flight_num} Registered!*\n\n"
        "Flight Sentinel is monitoring this flight in the background. If delays or landing gaps occur, your schedule will be autonomously updated.",
        reply_markup=MAIN_KEYBOARD
    )

async def disruption_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data

    # Ensure user has an active trip record in SQLite
    trip_store.get_or_create_default_trip(chat_id)

    reply_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📲 Test Phone Push (Lock Screen)", callback_data="mvp_push_test"),
        ],
        [
            InlineKeyboardButton("🛬 1-Hr Early Landing", callback_data="mvp_landing"),
            InlineKeyboardButton("🌧️ Incoming Rain", callback_data="mvp_rain"),
        ],
        [
            InlineKeyboardButton("🚦 Route Traffic Delay", callback_data="mvp_traffic"),
            InlineKeyboardButton("🌪️ Monsoon Replan", callback_data="mvp_monsoon"),
        ],
        [
            InlineKeyboardButton("⚡ Run All 3 In Sequence", callback_data="mvp_all"),
        ]
    ])

    if data == "mvp_push_test":
        await test_push_command(update, context)
        return
    elif data == "mvp_landing":

        await query.edit_message_text("⚙️ Simulating touchdown 1 hr before hotel check-in... finding luggage-friendly cafes...")
        msg = await asyncio.to_thread(proactive_engine.check_landing_buffer_alerts, force_chat_id=chat_id, simulate=True)
        if msg:
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_kb)
            except Exception:
                await query.edit_message_text(msg, reply_markup=reply_kb)
    elif data == "mvp_rain":
        await query.edit_message_text("⚙️ Simulating incoming precipitation radar (35 mins away)... pulling indoor pivots...")
        msg = await asyncio.to_thread(proactive_engine.check_incoming_rain_alerts, force_chat_id=chat_id, simulate=True)
        if msg:
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_kb)
            except Exception:
                await query.edit_message_text(msg, reply_markup=reply_kb)
    elif data == "mvp_traffic":
        await query.edit_message_text("⚙️ Simulating live road congestion on route to hotel... calculating detours...")
        msg = await asyncio.to_thread(proactive_engine.check_traffic_alerts, force_chat_id=chat_id, simulate=True)
        if msg:
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_kb)
            except Exception:
                await query.edit_message_text(msg, reply_markup=reply_kb)
    elif data == "mvp_all":
        await query.edit_message_text("⚙️ *Starting Autonomous Sentinel Sequence (1/3)*:\n\n1️⃣ Simulating early touchdown buffer...")
        msg1 = await asyncio.to_thread(proactive_engine.check_landing_buffer_alerts, force_chat_id=chat_id, simulate=True)
        if msg1:
            try:
                await query.edit_message_text(msg1, parse_mode="Markdown")
            except Exception:
                await query.edit_message_text(msg1)
        await asyncio.sleep(2.0)

        # Step 2: Traffic
        msg2 = await asyncio.to_thread(proactive_engine.check_traffic_alerts, force_chat_id=chat_id, simulate=True)
        if msg2:
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg2, parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=msg2)
        await asyncio.sleep(2.0)

        # Step 3: Rain Radar
        msg3 = await asyncio.to_thread(proactive_engine.check_incoming_rain_alerts, force_chat_id=chat_id, simulate=True)
        if msg3:
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg3, parse_mode="Markdown", reply_markup=reply_kb)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=msg3, reply_markup=reply_kb)
    elif data == "mvp_monsoon":
        await query.edit_message_text("⚙️ Simulating severe monsoon downpour... replanning schedule with indoor spots...")
        res = await asyncio.to_thread(proactive_engine.trigger_manual_disruption, chat_id, "rain_approaching")
        if res and "revised_plan" in res:
            try:
                await query.edit_message_text(f"🚨 *AI Revised Itinerary:*\n\n{res['revised_plan']}", parse_mode="Markdown", reply_markup=reply_kb)
            except Exception:
                await query.edit_message_text(f"🚨 AI Revised Itinerary:\n\n{res['revised_plan']}", reply_markup=reply_kb)
    else:
        # Legacy fallback
        scenario = "rain_approaching" if "rain" in data else "flight_delay_4h"
        await query.edit_message_text(f"⚙️ Simulating *{scenario}*...")
        await asyncio.to_thread(proactive_engine.trigger_manual_disruption, chat_id, scenario)


async def poll_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update.message, "🔄 Running all proactive sentinels across active destinations now...")
    await asyncio.to_thread(proactive_engine.check_weather_disruptions)
    await asyncio.to_thread(proactive_engine.check_incoming_rain_alerts)
    await asyncio.to_thread(proactive_engine.check_traffic_alerts)
    await asyncio.to_thread(proactive_engine.check_flight_disruptions)
    await safe_reply(update.message, "✅ Multi-signal proactive poll complete.")

async def reset_trip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    trip_store.clear_trip(chat_id)
    await safe_reply(update.message, "🗑️ Active trip cleared from database. Start fresh with `/plan`!", reply_markup=MAIN_KEYBOARD)

# --- Natural Concierge Chat ---

def execute_discovery(chat_id, text, cat, user_lat, user_lon, destination, trip):
    loc_data = trip_store.get_current_location(chat_id) if trip else None
    
    if loc_data:
        v_lat, v_lon = loc_data["latitude"], loc_data["longitude"]
        src = loc_data.get("source", "real")
        logger.info(f"\n[DISCOVERY REQUEST]\nquery={cat}\nuser_id={chat_id}\ntrip_id={trip.get('id') if trip else ''}\nlatitude_used={v_lat}\nlongitude_used={v_lon}\nradius=5000\nsource={src}\n")
        places = discover_live_places(destination, cat, lat=v_lat, lon=v_lon)
        loc_context = "your simulated location" if src == "demo" else "your current location"
        is_live_prox = True
    else:
        fallback_loc = destination
        if trip:
            act_loc = proactive_engine._get_upcoming_activity_location(chat_id, destination)
            if act_loc:
                fallback_loc = act_loc
        places = discover_live_places(fallback_loc, cat)
        loc_context = f"{fallback_loc} (itinerary fallback)" if fallback_loc != destination else f"{destination} center"
        is_live_prox = False
        
    visited = trip_store.get_visited_places(chat_id) if trip else []
    rejected = trip_store.get_rejected_categories(chat_id) if trip else []
    
    # Filter visited, but allow explicitly requested ones. Also filter rejected.
    places = [p for p in places if (p['name'] not in visited or p['name'].lower() in text.lower()) and not any(r in p['name'].lower() or r in p['category'].lower() for r in rejected)]
        
    if not places:
        return f"No live spots found near {loc_context} for '{cat}'."
    place_cards = []
    for p in places:
        c_info = f" • Cuisine: {p['cuisine']}" if p.get('cuisine') else ""
        d_info = f" • Distance: {p['distance_km']:.1f}km" if is_live_prox and p.get('distance_km') is not None else ""
        card = f"📍 *{p['name']}* ({p['category']})\n  🕒 Hours: {p['opening_hours']}{c_info}{d_info}"
        place_cards.append(card)
    places_text = "\n\n".join(place_cards)
    return f"🗺️ *Discovery: {cat.title()} near {loc_context}*\n\n{places_text}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Handle quick reply buttons
    if text in ["📲 Test Phone Push", "Test Phone Push"]:
        return await test_push_command(update, context)
    elif text == "🗺️ Plan Trip":
        return await plan_start(update, context)
    elif text == "📋 My Itinerary":
        return await view_itinerary(update, context)
    elif text in ["⚡ MVP Demo Scenarios", "🚨 Test Disruption"]:
        return await demo_mvp_command(update, context)
    elif text == "🌤️ Check Weather":
        return await check_weather_command(update, context)
    elif text == "📍 Live Discovery":
        return await discover_command(update, context)
    elif text == "🌅 Morning Briefing":
        return await briefing_command(update, context)
    elif text == "💡 Ask Concierge":
        await safe_reply(update.message, "Ask me anything! e.g., 'What are the best indoor museums in Jaipur?' or 'Top sunset beach in Goa?'")
        return
    elif text == "🔄 Reset Trip":
        return await reset_trip_command(update, context)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Free-form travel query -> Intent Router
    def _route_and_handle():
        trip = trip_store.get_trip(chat_id)
        if trip and trip.get("status") == "completed":
            trip = None  # Do not use completed trips as active context
        destination = trip["destination"] if trip else "Goa"
        user_lat, user_lon = trip_store.get_valid_location(chat_id)
        
        intent_data = analyze_intent(text)
        intent = intent_data.get("intent", "general")
        args = intent_data.get("args", {})

        if intent == "discover":
            cat = args.get("category", "attraction")
            return execute_discovery(chat_id, text, cat, user_lat, user_lon, destination, trip)
            
        elif intent == "disruption":
            msg = args.get("message", text)
            if trip and trip.get("itinerary"):
                from datetime import datetime
                curr_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                loc_ctx = f"{user_lat}, {user_lon}" if user_lat is not None and user_lon is not None else trip["destination"]
                visited = trip_store.get_visited_places(chat_id)
                return evaluate_and_replan(
                    {"message": msg, "type": "user_reported"},
                    trip["itinerary"],
                    current_time=curr_time,
                    current_location=loc_ctx,
                    visited_places=visited,
                    rejected_categories=trip_store.get_rejected_categories(chat_id) if trip else []
                )
            else:
                return "I see there's a disruption, but I don't have an active itinerary for you yet. Type /plan to start one!"
                
        elif intent == "preference_change":
            changes = args.get("changes", text)
            
            # Save durable user preference globally
            existing_prefs = trip_store.get_user_preferences(chat_id)
            new_prefs = update_durable_preferences(existing_prefs, changes)
            trip_store.update_user_preferences(chat_id, new_prefs)
            
            if trip and trip.get("itinerary"):
                prompt = f"The user wants to change their itinerary: {changes}. Here is the current itinerary:\n{trip['itinerary']}\nProvide a revised itinerary."
                resp = client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7
                )
                new_itin = resp.choices[0].message.content
                # Save without overwriting chat_id etc
                updated = dict(trip)
                updated["itinerary"] = new_itin
                trip_store.save_trip_v2(chat_id, **updated)
                return f"✅ *Itinerary Updated!*\n\n{new_itin}\n\n_(Note: I've also updated your global travel preferences!)_"
            else:
                return f"Got it! I've updated your global preferences. When you're ready, type /plan to start a new trip!"

        elif intent == "visited":
            place = args.get("place", text)
            if trip:
                trip_store.mark_place_visited(chat_id, place)
                
                # Regenerate personalized itinerary excluding visited places
                orig_itin = trip.get("original_itinerary", trip.get("itinerary", ""))
                visited = trip_store.get_visited_places(chat_id)
                v_lat, v_lon = trip_store.get_valid_location(chat_id)
                loc_ctx = f"Lat {v_lat}, Lon {v_lon}" if v_lat is not None and v_lon is not None else None
                from datetime import datetime
                curr_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                rec_itin = generate_personalized_itinerary(orig_itin, visited, loc_ctx, rejected, curr_time)
                trip_store.update_recommended_itinerary(chat_id, rec_itin)
                
                return f"✅ Marked *{place}* as visited! I've updated your personalized recommendations to exclude it. Type /itinerary to view your updated plan."
            else:
                return "You don't have an active trip to mark places visited. Type /plan!"
                
        elif intent == "reject":
            category = args.get("category", text)
            if trip:
                trip_store.add_rejected_category(chat_id, category)
                
                # Regenerate personalized itinerary excluding rejected places
                orig_itin = trip.get("original_itinerary", trip.get("itinerary", ""))
                visited = trip_store.get_visited_places(chat_id)
                rejected = trip_store.get_rejected_categories(chat_id)
                v_lat, v_lon = trip_store.get_valid_location(chat_id)
                loc_ctx = f"Lat {v_lat}, Lon {v_lon}" if v_lat is not None and v_lon is not None else None
                from datetime import datetime
                curr_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                rec_itin = generate_personalized_itinerary(orig_itin, visited, loc_ctx, rejected, curr_time)
                trip_store.update_recommended_itinerary(chat_id, rec_itin)
                
                return f"✅ Got it! I will avoid recommending *{category}* from now on. I've updated your personalized recommendations."
            else:
                return "You don't have an active trip to update preferences. Type /plan!"
                
        else:
            # Fallback to general RAG question
            results = rag_pipeline.search(text, n_results=10)
            visited = trip_store.get_visited_places(chat_id) if trip else []
            rejected = trip_store.get_rejected_categories(chat_id) if trip else []
            
            filtered_results = []
            for r in results:
                name = r['metadata']['name'].lower()
                cat = r['metadata'].get('category', '').lower()
                if r['metadata']['name'] in visited and r['metadata']['name'].lower() not in text.lower(): continue
                if any(rej in name or rej in cat or rej in r['document'].lower() for rej in rejected): continue
                filtered_results.append(r)
                
            from datetime import datetime
            curr_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            filtered_results.sort(key=lambda r: not is_open(r['metadata']['timings'], curr_time))
                
            results = filtered_results[:3]
            
            if not results:
                # Fallback to Live POI Discovery
                places = discover_live_places(destination, text, lat=user_lat, lon=user_lon) if user_lat and user_lon else discover_live_places(destination, text)
                if not places:
                    return f"I couldn't find any verified spots or live locations matching '{text}'."
                
                for p in places[:3]:
                    c_info = f" • Cuisine: {p['cuisine']}" if p.get('cuisine') else ""
                    d_info = f" • Distance: {p.get('distance_km', 0):.1f}km" if p.get('distance_km') else ""
                    card = f"📍 *{p['name']}* ({p['category']})\n  🕒 Hours: {p['opening_hours']}{c_info}{d_info}"
                    place_cards.append(card)
                places_text = "\n\n".join(place_cards)
                return f"🔍 *I couldn't find any curated verified spots for '{text}', but I found these live places nearby:*\n\n{places_text}"

            context_str = "\n".join([f"- {r['metadata']['name']}: {r['document']}" for r in results])
            try:
                resp = client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a concise, knowledgeable travel concierge. Recommend spots strictly using the provided context."},
                        {"role": "user", "content": f"Context:\n{context_str}\n\nQuery: {text}"}
                    ]
                )
                return resp.choices[0].message.content
            except Exception as e:
                spot_names = ", ".join([r['metadata']['name'] for r in results])
                return f"Found verified spots: {spot_names}."

    answer = await asyncio.to_thread(_route_and_handle)
    try:
        await safe_reply(update.message, answer, parse_mode="Markdown")
    except Exception:
        await safe_reply(update.message, answer)

async def parse_text_itinerary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parses free-text itinerary provided by user (e.g. /parse [text])."""
    chat_id = update.effective_chat.id
    if not context.args:
        await safe_reply(update.message, "⚠️ Please provide your itinerary text after the command. Example: `/parse Flying to Goa on 6E-501...`")
        return

    raw_text = " ".join(context.args)
    await safe_reply(update.message, "🤖 Analyzing your itinerary text...")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    parsed = await asyncio.to_thread(parse_itinerary_with_llm, raw_text)
    if not parsed:
        await safe_reply(update.message, "❌ Sorry, I couldn't understand the itinerary details. Please try providing more structure or a PDF.")
        return

    trip_data = extract_trip_fields(parsed)
    
    # Generate personalized plan async
    await safe_reply(update.message, "✨ Optimizing your free time with AI suggestions...")
    visited = trip_store.get_visited_places(chat_id)
    loc_ctx = None
    trip_exist = trip_store.get_trip(chat_id)
    if trip_exist and trip_exist.get("status") == "completed":
        trip_exist = None
    if trip_exist and trip_exist.get("current_lat") and trip_exist.get("current_lon"):
        loc_ctx = f"Lat {trip_exist['current_lat']}, Lon {trip_exist['current_lon']}"
        
    rec_itin = await asyncio.to_thread(generate_personalized_itinerary, trip_data['itinerary'], visited, loc_ctx)
    
    trip_data["recommended_itinerary"] = rec_itin
    await asyncio.to_thread(trip_store.save_trip_v2, chat_id, **trip_data)

    msg = f"✅ *Trip Parsed Successfully!*\n\n{trip_data['itinerary']}\n\n💡 *Recommended Additions:*\n{rec_itin}\n\n🛡️ Background monitoring is now active!"
    await safe_reply(update.message, msg)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles PDF itinerary uploads."""
    chat_id = update.effective_chat.id
    doc = update.message.document

    if not doc.file_name.lower().endswith(".pdf"):
        await safe_reply(update.message, "⚠️ Please upload a PDF file containing your travel itinerary/booking.")
        return

    await safe_reply(update.message, f"📄 Received `{doc.file_name}`. Reading document...")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        file_bytes = await telegram_file.download_as_bytearray()
        
        raw_text = await asyncio.to_thread(extract_text_from_bytes, bytes(file_bytes))
        if not raw_text:
            await safe_reply(update.message, "❌ Failed to extract text from this PDF. It might be an image-based scan.")
            return

        await safe_reply(update.message, "🤖 Text extracted! Structuring your trip with AI...")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        parsed = await asyncio.to_thread(parse_itinerary_with_llm, raw_text)
        if not parsed:
            await safe_reply(update.message, "❌ Sorry, I couldn't understand the itinerary details from the document.")
            return

        trip_data = extract_trip_fields(parsed)
        
        # Generate personalized plan async
        await safe_reply(update.message, "✨ Optimizing your free time with AI suggestions...")
        visited = trip_store.get_visited_places(chat_id)
        v_lat, v_lon = trip_store.get_valid_location(chat_id)
        loc_ctx = f"Lat {v_lat}, Lon {v_lon}" if v_lat is not None and v_lon is not None else None
            
        rec_itin = await asyncio.to_thread(generate_personalized_itinerary, trip_data['itinerary'], visited, loc_ctx)
        
        trip_data["recommended_itinerary"] = rec_itin
        await asyncio.to_thread(trip_store.save_trip_v2, chat_id, **trip_data)

        msg = f"✅ *Trip Parsed Successfully!*\n\n{trip_data['itinerary']}\n\n💡 *Recommended Additions:*\n{rec_itin}\n\n🛡️ Background monitoring is now active!"
        await safe_reply(update.message, msg)

    except Exception as e:
        logger.error(f"Document handling failed: {e}", exc_info=True)
        await safe_reply(update.message, f"❌ An error occurred while processing the document. Please try again.")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stores user's current live location."""
    chat_id = update.effective_chat.id
    if not update.message or not update.message.location:
        return
        
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    
    trip = trip_store.get_trip(chat_id)
    if trip:
        await asyncio.to_thread(trip_store.update_location, chat_id, lat, lon)
        logger.info(f"\n[LOCATION RECEIVED]\nuser_id={chat_id}\nchat_id={chat_id}\ntrip_id={trip.get('id')}\nlatitude={lat}\nlongitude={lon}\ntimestamp={time.time()}\nsource=real\n")
        await safe_reply(update.message, f"📍 *Location Updated*\nI've saved your current coordinates ({lat:.4f}, {lon:.4f}). Re-optimizing your personalized itinerary for this new area...")
        
        # Regenerate personalized itinerary for the new location
        orig_itin = trip.get("original_itinerary", trip.get("itinerary", ""))
        visited = trip_store.get_visited_places(chat_id)
        loc_ctx = f"Lat {lat}, Lon {lon}"
        rec_itin = await asyncio.to_thread(generate_personalized_itinerary, orig_itin, visited, loc_ctx)
        
        await asyncio.to_thread(trip_store.update_recommended_itinerary, chat_id, rec_itin)
        await safe_reply(update.message, f"✨ *Itinerary Context Updated!*\nYour recommended schedule has been adapted for your new location. Type /itinerary to view it.")
    else:
        await safe_reply(update.message, "📍 *Location Received*\nBut you don't have an active trip yet! Type /plan to start one.")

async def demo_location_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await safe_reply(update.message, "Usage: /demo_location [Location Name or Lat,Lon]\nExample: /demo_location Baga Beach, Goa")
        return
        
    query = " ".join(context.args)
    trip = trip_store.get_trip(chat_id)
    if not trip:
        await safe_reply(update.message, "No active trip found to set demo location.")
        return
        
    # Attempt to parse as lat,lon
    import re
    coords_match = re.match(r"^\s*([+-]?\d+\.?\d*)\s*,\s*([+-]?\d+\.?\d*)\s*$", query)
    if coords_match:
        lat, lon = float(coords_match.group(1)), float(coords_match.group(2))
        name = query
    else:
        # Use places_service resolve_coordinates
        from places_service import resolve_coordinates
        try:
            coord = await asyncio.to_thread(resolve_coordinates, query)
            lat, lon = coord['lat'], coord['lon']
            name = coord['name']
        except Exception as e:
            await safe_reply(update.message, f"Could not resolve demo location: {e}")
            return
            
    await asyncio.to_thread(trip_store.update_demo_location, chat_id, lat, lon, is_demo=True)
    logger.info(f"\n[LOCATION RECEIVED]\nuser_id={chat_id}\nchat_id={chat_id}\ntrip_id={trip.get('id')}\nlatitude={lat}\nlongitude={lon}\ntimestamp={time.time()}\nsource=demo\n")
    await safe_reply(update.message, f"📍 *Demo location updated to {name}* ({lat:.4f}, {lon:.4f}).\nThis will now be used for all discovery and itinerary replanning.")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Global error handler caught: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await safe_reply(
            update.effective_message,
            "⚠️ A brief network hiccup occurred. Please try tapping your selection or sending your message again!",
            reply_markup=MAIN_KEYBOARD
        )

def create_bot_application():
    if not TOKEN or TOKEN == "your_telegram_token_here":
        logger.warning("TELEGRAM_BOT_TOKEN is not configured in .env.")
        return None

    req = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )

    app = ApplicationBuilder().token(TOKEN).request(req).build()

    plan_conv = ConversationHandler(
        entry_points=[CommandHandler("plan", plan_start)],
        states={
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_destination)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_duration)],
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_budget)],
            INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_interests)],
        },
        fallbacks=[CommandHandler("cancel", plan_cancel)]
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mvp", demo_mvp_command))
    app.add_handler(CommandHandler("simulate", demo_mvp_command))
    app.add_handler(CommandHandler("push", test_push_command))
    app.add_handler(CommandHandler("test_push", test_push_command))
    app.add_handler(CommandHandler("itinerary", view_itinerary))
    app.add_handler(CommandHandler("weather", check_weather_command))
    app.add_handler(CommandHandler("discover", discover_command))
    app.add_handler(CommandHandler("briefing", briefing_command))
    app.add_handler(CommandHandler("flight", flight_command))
    app.add_handler(CommandHandler("poll_now", poll_now_command))
    app.add_handler(CommandHandler("reset", reset_trip_command))
    app.add_handler(CommandHandler("demo_location", demo_location_command))
    app.add_handler(CallbackQueryHandler(disruption_callback))
    app.add_handler(CommandHandler("parse", parse_text_itinerary))
    app.add_handler(plan_conv)
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(global_error_handler)

    return app


if __name__ == "__main__":
    app = create_bot_application()
    if app:
        proactive_engine.start(weather_poll_minutes=5)
        print("Starting Trip Guardian Telegram Bot with Multi-Signal MVP Sentinels...")
        app.run_polling(bootstrap_retries=5, drop_pending_updates=True)
    else:
        print("Cannot run bot without TELEGRAM_BOT_TOKEN. Please set it in .env")
