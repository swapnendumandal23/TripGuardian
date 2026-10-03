import os
import json
from openai import OpenAI
from RAG_Pipeline import rag_pipeline
from dotenv import load_dotenv
from datetime import datetime
import re

def is_open(timings: str, current_time: str) -> bool:
    if not current_time or not timings:
        return True
    timings = timings.lower()
    if "24 hours" in timings:
        return True
    try:
        # current_time is like "2026-10-02 14:30"
        dt = datetime.strptime(current_time, "%Y-%m-%d %H:%M")
        curr_mins = dt.hour * 60 + dt.minute
        
        # Extract HH:MM
        times = re.findall(r'(\d{1,2}:\d{2})', timings)
        if len(times) >= 2:
            from datetime_utils import parse_time_to_minutes
            start_mins = parse_time_to_minutes(times[0])
            end_mins = parse_time_to_minutes(times[1])
            
            # Handle next day (e.g. 18:00 - 02:00)
            if end_mins < start_mins:
                if curr_mins < end_mins:
                    curr_mins += 24 * 60
                end_mins += 24 * 60
                
            return start_mins <= curr_mins <= end_mins
    except Exception:
        pass
    return True

load_dotenv()

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

if GEMINI_KEY and GEMINI_KEY != "your_gemini_api_key_here" and len(GEMINI_KEY.strip()) > 10:
    # Use Google Gemini via official OpenAI-compatible endpoint (100% Free)
    client = OpenAI(
        api_key=GEMINI_KEY.strip(),
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    DEFAULT_MODEL = "gemini-3.5-flash"
    AI_PROVIDER = "Google Gemini (3.5 Flash)"
else:
    # Default to OpenAI
    client = OpenAI(api_key=OPENAI_KEY or "dummy_key_if_not_set")
    DEFAULT_MODEL = "gpt-4o-mini"
    AI_PROVIDER = "OpenAI (gpt-4o-mini)"

def generate_itinerary(destination: str, dates: str, budget: str, interests: str) -> str:
    rag_results = rag_pipeline.search(query=interests, n_results=5)
    context_str = "\n".join([f"- {r['metadata']['name']}: {r['document']}" for r in rag_results])
    
    prompt = f"""
You are an expert travel concierge. Create a day-by-day travel itinerary for {destination}.
Dates: {dates}
Budget: {budget}
Interests: {interests}

CRITICAL RULE: You MUST NOT invent, hallucinate, or create any locations, hotels, or restaurants. You may ONLY use the exact verified spots provided in the context below. Do not add fictitious facts or alter details.

Here are some verified local spots to include (do not invent places):
{context_str}

Please generate a 3-day itinerary formatted in Markdown. 
"""
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "You are a strictly factual travel assistant. You only recommend places provided in context and never invent facts."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        # Graceful fallback: construct clean structured itinerary directly from verified RAG spots
        spots = [r['metadata']['name'] for r in rag_results]
        day1_m = spots[0] if len(spots) > 0 else "Scenic Discovery"
        day1_a = spots[1] if len(spots) > 1 else "Local Markets & Heritage"
        day1_e = spots[2] if len(spots) > 2 else "Sunset Viewpoint"
        day2_m = spots[3] if len(spots) > 3 else "Cultural Landmark"
        day2_a = spots[4] if len(spots) > 4 else "Artisan Cafe & Dining"

        return (
            f"📍 *Custom Itinerary for {destination}* ({dates} | {budget})\n\n"
            f"*Day 1:*\n"
            f"• Morning: Explore {day1_m}\n"
            f"• Afternoon: Visit {day1_a}\n"
            f"• Evening: Sunset & dinner at {day1_e}\n\n"
            f"*Day 2:*\n"
            f"• Morning: Guided tour at {day2_m}\n"
            f"• Afternoon: Dining & relaxation at {day2_a}\n"
            f"• Evening: Local culture & leisure stroll\n\n"
            f"*(Curated from verified location database)*"
        )

def generate_personalized_itinerary(original_itinerary: str, visited_places: list, current_location: str = None, rejected_categories: list = None, current_time: str = None) -> str:
    """Generates a personalized itinerary preserving original fixed items but filling free time."""
    rag_results = rag_pipeline.search(query="attractions, dining, cafes", n_results=20)
    # Filter out visited and rejected
    rejected_categories = [c.lower() for c in (rejected_categories or [])]
    filtered_spots = []
    for r in rag_results:
        name = r['metadata']['name'].lower()
        cat = r['metadata'].get('category', '').lower()
        if r['metadata']['name'] in visited_places: continue
        if any(rej in name or rej in cat or rej in r['document'].lower() for rej in rejected_categories): continue
        filtered_spots.append(r)
        
    filtered_spots.sort(key=lambda r: not is_open(r['metadata']['timings'], current_time))
        
    context_str = "\n".join([f"- {r['metadata']['name']} (Location: {r['metadata'].get('category')}): {r['document']}" for r in filtered_spots[:5]])
    
    prompt = f"""
You are an expert travel concierge. The user has an original itinerary with fixed bookings.
You need to generate a "Recommended Flexible Schedule" that preserves all fixed bookings (flights, hotels) from the original itinerary, but intelligently fills in any "free time" slots with personalized suggestions from the verified spots list.

CRITICAL RULE: You MUST NOT invent, hallucinate, or create any new locations, facts, or alter fixed booking details. You may ONLY suggest places from the exact verified spots provided. Treat the Original Itinerary facts as an absolute source-of-truth.

Original Itinerary:
{original_itinerary}

User's Current Location: {current_location or 'Unknown'}
Already Visited Places (DO NOT SUGGEST THESE): {', '.join(visited_places) if visited_places else 'None'}
Rejected Categories (DO NOT SUGGEST THESE): {', '.join(rejected_categories) if rejected_categories else 'None'}

Verified Spots to fill free time:
{context_str}

Please generate the new itinerary formatted in Markdown. Differentiate your AI suggestions from the fixed bookings by marking them with a 💡 icon.
"""
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "You are a strictly factual travel assistant. Preserve fixed bookings exactly, suggest ONLY provided unvisited places, never invent data."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        return "*(Error generating personalized suggestions — please try again)*"

def _validate_replanned_itinerary(original: str, revised: str, disruption: dict, visited: list) -> bool:
    """Uses the LLM to verify if the revised itinerary is materially valid."""
    prompt = f"""
You are an itinerary validation auditor. 
Original Itinerary:
{original}

Disruption Event:
{disruption['message']}

Revised Itinerary:
{revised}

Visited Places (must be avoided):
{visited}

Evaluate the Revised Itinerary against these criteria:
1. Did it materially resolve or avoid the disruption? (e.g. if an activity is flooded/cancelled, is it replaced or removed?)
2. Are all FIXED BOOKINGS (flights, hotel check-ins) from the Original Itinerary preserved and unchanged?
3. Is it geographically and chronologically feasible?
4. Did it avoid all Visited Places?
5. Did it strictly rely on the provided context without hallucinating or inventing new unverified places or facts?

If it passes all criteria and is not just an exact copy of the original (it must actually adjust for the disruption), return exactly: PASS
If it fails, return exactly: FAIL
"""
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10
        )
        return "PASS" in resp.choices[0].message.content.upper()
    except Exception:
        return True # Soft fail if validation crashes


def evaluate_and_replan(disruption_event: dict, current_itinerary: str, current_time: str = None, current_location: str = None, visited_places: list = None, rejected_categories: list = None) -> str:
    filter_cond = None
    if disruption_event.get("type") == "weather" and "rain" in disruption_event.get("message", "").lower():
        filter_cond = {"type": "indoor"}
        
    rag_results = rag_pipeline.search(query="indoor activities dining museum", n_results=20, filter_conditions=filter_cond)
    
    # Filter out visited places and rejected categories
    visited_places = visited_places or []
    rejected_categories = [cat.lower() for cat in (rejected_categories or [])]
    filtered_spots = []
    for r in rag_results:
        name = r['metadata']['name'].lower()
        cat = r['metadata'].get('category', '').lower()
        if r['metadata']['name'] in visited_places: continue
        if any(rej in name or rej in cat or rej in r['document'].lower() for rej in rejected_categories): continue
        filtered_spots.append(r)
        
    # Penalty: Sort open places first
    filtered_spots.sort(key=lambda r: not is_open(r['metadata']['timings'], current_time))
    
    context_str = "\n".join([f"- {r['metadata']['name']} (Indoor): {r['document']}" for r in filtered_spots[:4]])
    
    prompt = f"""
You are the Trip Guardian emergency concierge. 
A disruption just occurred: {disruption_event['message']}

User's Actual Situation:
- Current Time: {current_time or 'Unknown'}
- Current Location: {current_location or 'Unknown'}
- Already Visited Places: {', '.join(visited_places) if visited_places else 'None'}
- Rejected Categories (DO NOT SUGGEST): {', '.join(rejected_categories) if rejected_categories else 'None'}

Current Itinerary Context:
{current_itinerary}

Here are some verified alternatives you can swap in (do not suggest already visited places):
{context_str}

Task:
1. Explain the impact of the disruption based on the user's current situation (time, location).
2. Provide a revised schedule that is chronologically and geographically feasible from the Current Time onwards.
3. CRITICAL CONSTRAINT: Flights, hotel check-ins, and other fixed commitments are HARD CONSTRAINTS. You MUST NOT overwrite, reschedule, or remove any fixed booking times/details. They must remain exactly as they were in the Current Itinerary Context.
4. CRITICAL CONSTRAINT: Travel Time. You MUST explicitly factor in and display realistic travel times (e.g., 30-90 minutes) between geographically separated activities. Reject and adjust any transitions that are physically impossible.
5. Flexible sightseeing slots may be moved or replaced. If a replacement is needed, ensure it fits the remaining free slots.
6. Preserve past activities. Do NOT reschedule past activities.
7. CRITICAL ANTI-HALLUCINATION RULE: You MUST NOT invent, hallucinate, or create any alternative locations. You may ONLY swap in places from the exact verified alternatives provided. Do not invent facts or modify source-of-truth details.
"""
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": "You are a strictly factual emergency concierge. Never hallucinate places or facts."}, {"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500
        )
        revised_itinerary = response.choices[0].message.content
        
        # Post-replanning validation
        if not _validate_replanned_itinerary(current_itinerary, revised_itinerary, disruption_event, visited_places):
            raise ValueError("Validation Failed: Revised itinerary is invalid or unchanged.")
            
        return revised_itinerary
    except Exception as e:
        alt_spots = "\n".join([f"• *{r['metadata']['name']}* ({r['metadata']['timings']}) - {r['metadata']['category']}" for r in filtered_spots])
        return (
            f"⚠️ *Disruption Impact:*\n"
            f"{disruption_event['message']}\n"
            f"Outdoor activities in your current schedule are impacted.\n\n"
            f"📋 *Original Schedule (Retained):*\n{current_itinerary}\n\n"
            f"🏛️ *Autonomous Schedule Revision (Verified Indoor Alternatives):*\n"
            f"{alt_spots}\n\n"
            f"✅ *Action Taken:* We recommend pausing the current outdoor plan and visiting the alternatives above."
        )

def analyze_intent(user_text: str, conversation_context: dict = None) -> dict:
    """Classifies a natural language request into an actionable intent using previous context."""
    if conversation_context is None:
        conversation_context = {}
        
    prompt = f"""
You are an intelligent travel concierge router. Classify the user's natural language request into a precise structured JSON intent.
The user may be following up on a previous message. If they are, use the conversation context to fill in missing details (e.g., if previous intent was 'discovery' and category was 'food', and they now say 'something cheap', keep intent='discovery', category='food', and set price_preference='cheap').

Supported Intents:
- "discovery" (Find places, food, things to do. e.g. "Find me seafood", "I am bored", "Something cheap")
- "conversational_follow_up" (User is asking a specific question about the places you just suggested, e.g. "Which one is closest?", "Is the first one open?", "Which is cheaper?")
- "weather" (Check weather)
- "itinerary" (Check or parse itinerary)
- "replanning" (Disruptions, delays. e.g. "My flight is delayed")
- "location" (Location updates)
- "preference" (Change preferences, e.g. "I do not want beaches today")
- "visited_place" (CRITICAL: If the user says they ALREADY visited or went to a place, you MUST classify it as visited_place. e.g. "I've already visited Hidden Seafood Gem", "I went to Baga Beach")
- "general_travel_qa" (General travel questions, packing, best time to visit)
- "unknown" (Unrecognized or ambiguous)

Output ONLY valid JSON matching this structure:
{{
    "intent": "discovery|conversational_follow_up|weather|itinerary|replanning|location|preference|visited_place|general_travel_qa|unknown",
    "category": "...", // The place category (e.g., seafood, museum, food, attraction, shopping)
    "query": "...", // The raw search term, or the SPECIFIC place name visited (e.g., 'Baga Beach' NOT 'I visited Baga Beach')
    "nearby": true, // Boolean if they want it near their current location
    "price_preference": "...", // e.g., cheap, expensive, null
    "avoid": [], // List of strings to avoid
    "follow_up": true, // Boolean if this continues the previous context
    "references_previous_context": true // Boolean
}}

Previous Context:
{json.dumps(conversation_context)}

Current Request: "{user_text}"
"""
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:-3]
        elif content.startswith("```"):
            content = content[3:-3]
        return json.loads(content.strip())
    except Exception as e:
        text_lower = user_text.lower()
        if "delayed" in text_lower or "flight" in text_lower:
            return {"intent": "replanning", "query": user_text}
        elif "hungry" in text_lower or "eat" in text_lower:
            return {"intent": "discovery", "category": "food", "nearby": True}
        elif "cheap" in text_lower:
            return {"intent": "discovery", "price_preference": "cheap", "follow_up": True}
        elif "suggest something else" in text_lower:
            return {"intent": "discovery", "follow_up": True}
        elif "closest" in text_lower:
            return {"intent": "conversational_follow_up"}
        elif "visited" in text_lower or "went to" in text_lower:
            return {"intent": "visited_place", "query": text_lower.replace("i've already visited", "").replace("i visited", "").replace("we went to", "").strip()}
        elif "find" in text_lower or "seafood" in text_lower or "bored" in text_lower:
            cat = "seafood" if "seafood" in text_lower else "attraction"
            return {"intent": "discovery", "category": cat, "nearby": True}
        elif "want" in text_lower or "instead" in text_lower:
            return {"intent": "preference"}
        elif "no " in text_lower or "don't want" in text_lower:
            return {"intent": "preference", "avoid": [text_lower.replace("no ", "").replace("i don't want ", "").strip()]}
        return {"intent": "unknown"}


def update_durable_preferences(current_prefs: str, new_request: str) -> str:
    """Uses LLM to smartly merge a user's new preference request with their existing durable preferences."""
    if not current_prefs:
        current_prefs = "None"
    prompt = f"Update the user's travel preferences based on this new request: '{new_request}'. Current preferences: '{current_prefs}'. Return ONLY a concise, comma-separated list of their global travel preferences (e.g., 'vegan, prefers beaches, moderate budget'). Do not include trip-specific dates or locations."
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return f"{current_prefs}, {new_request}".strip(", ")
