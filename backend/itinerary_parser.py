"""
itinerary_parser.py — PDF & Free-Text Itinerary Extraction Engine

Extracts structured trip data from:
1. PDF uploads (MakeMyTrip, Yatra, Goibibo, Cleartrip confirmations)
2. Free-form text messages typed by the user

Uses Gemini 3.8 Flash via the OpenAI-compatible endpoint for structured JSON extraction.
"""

import json
import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime

from pypdf import PdfReader
from agent_interface import client, DEFAULT_MODEL

logger = logging.getLogger("itinerary_parser")

# --- JSON Schema that the LLM must output ---
EXTRACTION_SCHEMA = """{
  "destination": "string — primary city/region (e.g. 'Goa', 'Jaipur')",
  "trip_dates": {
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD",
    "timezone": "string — local timezone name or offset (e.g., 'Asia/Kolkata', '+05:30')"
  },
  "flights": [
    {
      "number": "airline code + number (e.g. '6E-501')",
      "from": "departure city or airport code",
      "to": "arrival city or airport code",
      "departure": "YYYY-MM-DDTHH:MM:SS±HH:MM (ISO 8601 with timezone)",
      "departure_local": "original local time string (e.g., '10:30 AM')",
      "arrival": "YYYY-MM-DDTHH:MM:SS±HH:MM (ISO 8601 with timezone)",
      "arrival_local": "original local time string",
      "type": "departure | return"
    }
  ],
  "trains": [
    {
      "number": "train number",
      "name": "train name if available",
      "from": "boarding station",
      "to": "destination station",
      "departure": "YYYY-MM-DDTHH:MM:SS±HH:MM",
      "arrival": "YYYY-MM-DDTHH:MM:SS±HH:MM"
    }
  ],
  "hotels": [
    {
      "name": "hotel name",
      "address": "full address if available",
      "checkin": "YYYY-MM-DDTHH:MM:SS±HH:MM",
      "checkout": "YYYY-MM-DDTHH:MM:SS±HH:MM",
      "room_type": "room type if mentioned"
    }
  ],
  "activities": [
    {
      "day": 1,
      "time": "HH:MM (strictly 24-hour format)",
      "end_time": "HH:MM (strictly 24-hour format, estimated)",
      "name": "activity or place name",
      "type": "outdoor | indoor | dining | transport | sightseeing",
      "duration_hrs": 2,
      "location": "specific area/address if available"
    }
  ],
  "transport": [
    {
      "type": "bus | cab | auto | ferry",
      "from": "pickup point",
      "to": "drop point",
      "time": "YYYY-MM-DDTHH:MM:SS±HH:MM"
    }
  ],
  "budget": "budget range if mentioned (e.g. 'INR 25000', 'moderate')",
  "travelers": 1,
  "notes": "any special requirements, preferences, dietary notes"
}"""


EXTRACTION_PROMPT = """You are a travel itinerary extraction specialist. Your job is to read raw text from a travel booking confirmation (PDF, email, or typed by user) and extract ALL structured trip information into a precise JSON format.

RULES:
1. Extract EVERY piece of information available. Do NOT skip flights, hotels, or activities.
2. CRITICAL ANTI-HALLUCINATION RULE: Do NOT hallucinate, guess, or invent any flights, hotels, names, dates, or booking facts. Only extract exactly what is explicitly stated in the RAW INPUT TEXT. If information is missing, use null.
3. Dates must be in ISO 8601 format with timezone offsets where appropriate (YYYY-MM-DDTHH:MM:SS±HH:MM). Retain original ambiguous times in the '_local' fields.
4. For activities, use strictly 24-hour HH:MM format for the 'time' and 'end_time' fields.
5. For activities, infer the type (outdoor/indoor/dining/sightseeing) from context.
6. If only a rough itinerary is described (e.g., "Day 1: Visit Amber Fort"), still extract it into the activities array with estimated times.
7. Return ONLY valid JSON. No markdown, no explanations, no ```json blocks. Just the raw JSON object.

OUTPUT SCHEMA:
{schema}

RAW INPUT TEXT:
{raw_text}
"""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extracts all text from a PDF file using pypdf."""
    try:
        reader = PdfReader(pdf_path)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())
        full_text = "\n\n".join(pages_text)
        logger.info(f"Extracted {len(full_text)} chars from PDF ({len(reader.pages)} pages)")
        return full_text
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """Extracts text from PDF bytes (for Telegram file downloads)."""
    import io
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())
        full_text = "\n\n".join(pages_text)
        logger.info(f"Extracted {len(full_text)} chars from PDF bytes ({len(reader.pages)} pages)")
        return full_text
    except Exception as e:
        logger.error(f"PDF bytes extraction failed: {e}")
        return ""


def parse_itinerary_with_llm(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Sends raw text (from PDF or user message) to the LLM for structured extraction.
    Returns parsed JSON dict or None on failure.
    """
    if not raw_text or len(raw_text.strip()) < 20:
        logger.warning("Input text too short for meaningful extraction")
        return None

    prompt = EXTRACTION_PROMPT.format(schema=EXTRACTION_SCHEMA, raw_text=raw_text[:6000])

    try:
        try:
            response = client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": "You are a JSON extraction engine. Return ONLY a valid JSON object matching the requested schema. No markdown formatting, no comments."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4096
            )
        except Exception as primary_err:
            logger.warning(f"Primary model {DEFAULT_MODEL} failed ({primary_err}), trying fallback gemini-3.5-flash-lite...")
            response = client.chat.completions.create(
                model="gemini-3.5-flash-lite",
                messages=[
                    {"role": "system", "content": "You are a JSON extraction engine. Return ONLY a valid JSON object matching the requested schema. No markdown formatting, no comments."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4096
            )
        content = response.choices[0].message.content.strip()

        import re
        content = content.strip()
        
        # Robustly extract the outermost JSON object
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1 and end > start:
            content_clean = content[start:end+1]
        else:
            content_clean = content

        # Strip JavaScript/C-style comments if any
        content_clean = re.sub(r'//.*', '', content_clean)
        # Strip trailing commas
        content_clean = re.sub(r',\s*([\]}])', r'\1', content_clean)

        parsed = json.loads(content_clean)
        logger.info(f"LLM extraction successful: destination={parsed.get('destination')}, "
                     f"flights={len(parsed.get('flights', []))}, "
                     f"hotels={len(parsed.get('hotels', []))}, "
                     f"activities={len(parsed.get('activities', []))}")
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON: {e}")
        # Attempt partial recovery
        try:
            import re
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1 and end > start:
                raw_sub = content[start:end+1]
                clean_sub = re.sub(r'//.*', '', raw_sub)
                clean_sub = re.sub(r',\s*([\]}])', r'\1', clean_sub)
                parsed = json.loads(clean_sub)
                logger.info("Recovered JSON from partial response")
                return parsed
        except Exception:
            pass
        return None
    except Exception as e:
        logger.error(f"LLM extraction failed: {e}")
        return None


def structured_to_readable_itinerary(parsed: Dict[str, Any]) -> str:
    """Converts the structured JSON back into a readable markdown itinerary for Telegram display."""
    dest = parsed.get("destination", "Unknown")
    dates = parsed.get("trip_dates", {})
    start_date = dates.get("start", "")
    end_date = dates.get("end", "")
    
    lines = [f"📍 *Trip to {dest}*"]
    if start_date and end_date:
        lines.append(f"📅 {start_date} → {end_date}")
    lines.append("")

    # Flights
    flights = parsed.get("flights", [])
    if flights:
        lines.append("✈️ *Flights:*")
        for f in flights:
            dep = f.get("departure_local", f.get("departure", ""))
            arr = f.get("arrival_local", f.get("arrival", ""))
            lines.append(f"  • `{f.get('number', 'N/A')}` {f.get('from', '')} → {f.get('to', '')}  Dep: {dep}  Arr: {arr}")
        lines.append("")

    # Trains
    trains = parsed.get("trains", [])
    if trains:
        lines.append("🚂 *Trains:*")
        for t in trains:
            lines.append(f"  • `{t.get('number', '')}` {t.get('name', '')} {t.get('from', '')} → {t.get('to', '')}  Dep: {t.get('departure', '')}")
        lines.append("")

    # Hotels
    hotels = parsed.get("hotels", [])
    if hotels:
        lines.append("🏨 *Hotels:*")
        for h in hotels:
            lines.append(f"  • *{h.get('name', 'N/A')}*")
            lines.append(f"    Check-in: {h.get('checkin', 'N/A')} | Check-out: {h.get('checkout', 'N/A')}")
        lines.append("")

    # Activities
    activities = parsed.get("activities", [])
    if activities:
        # Group by day
        days = {}
        for a in activities:
            day = a.get("day", 1)
            if day not in days:
                days[day] = []
            days[day].append(a)

        for day_num in sorted(days.keys()):
            lines.append(f"📋 *Day {day_num}:*")
            for a in sorted(days[day_num], key=lambda x: x.get("time", "00:00")):
                icon = {"outdoor": "🏖️", "indoor": "🏛️", "dining": "🍽️", "transport": "🚗", "sightseeing": "📸"}.get(a.get("type", ""), "📌")
                time_str = a.get("time", "")
                lines.append(f"  {icon} {time_str} — *{a.get('name', 'Activity')}* ({a.get('type', 'general')}, ~{a.get('duration_hrs', '?')}h)")
            lines.append("")

    return "\n".join(lines)


def extract_trip_fields(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts the key fields needed by trip_store.save_trip_v2() from the parsed JSON.
    """
    dest = parsed.get("destination", "Unknown")
    dates = parsed.get("trip_dates", {})

    # First flight
    flights = parsed.get("flights", [])
    flight_number = flights[0].get("number") if flights else None
    flight_arrival = None
    if flights:
        arr = flights[0].get("arrival", "")
        if "T" in arr:
            flight_arrival = arr.split("T")[1][:5]  # "13:00"

    # First hotel
    hotels = parsed.get("hotels", [])
    hotel_name = hotels[0].get("name") if hotels else None
    hotel_checkin = None
    if hotels:
        ci = hotels[0].get("checkin", "")
        if "T" in ci:
            hotel_checkin = ci.split("T")[1][:5]

    readable = structured_to_readable_itinerary(parsed)

    return {
        "destination": dest,
        "itinerary": readable,
        "flight_number": flight_number,
        "flight_arrival_time": flight_arrival or "13:00",
        "hotel_name": hotel_name or "Hotel",
        "hotel_checkin_time": hotel_checkin or "14:00",
        "trip_start_date": dates.get("start"),
        "trip_end_date": dates.get("end"),
        "structured_itinerary": json.dumps(parsed, ensure_ascii=False),
    }
