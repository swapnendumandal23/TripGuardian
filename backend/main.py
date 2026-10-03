from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import time

from RAG_Pipeline import rag_pipeline
from agent_interface import generate_itinerary, evaluate_and_replan, client, DEFAULT_MODEL
from telegram_client import send_telegram_message
from trip_store import trip_store
from weather_service import fetch_live_weather
from scheduler import proactive_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start APScheduler proactive poller
    proactive_engine.start(weather_poll_minutes=5)
    yield
    # Shutdown: Cleanly shut down scheduler
    proactive_engine.stop()

app = FastAPI(title="Trip Guardian API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AlertRequest(BaseModel):
    scenario: str = "rain_approaching"
    chat_id: int = 123456789

class ChatRequest(BaseModel):
    query: str

class PlanRequest(BaseModel):
    chat_id: int = 123456789
    destination: str = "Goa"
    dates: str = "3 days"
    budget: str = "Moderate"
    interests: str = "Beaches, heritage, seafood"

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Trip Guardian API running"}

@app.get("/api/health")
def health():
    return {"status": "ok", "scheduler_active": proactive_engine.is_running}

@app.get("/api/weather/{destination}")
def get_destination_weather(destination: str):
    """Inspects real-time Open-Meteo weather for any city."""
    return fetch_live_weather(destination)

@app.post("/api/plan")
def plan_trip(req: PlanRequest):
    """Generates an itinerary and saves it into the active trip store."""
    itinerary = generate_itinerary(
        destination=req.destination,
        dates=req.dates,
        budget=req.budget,
        interests=req.interests
    )
    trip_store.save_trip(req.chat_id, req.destination, itinerary)
    return {
        "status": "success",
        "chat_id": req.chat_id,
        "destination": req.destination,
        "itinerary": itinerary
    }

@app.get("/api/trip/{chat_id}")
def get_user_trip(chat_id: int):
    trip = trip_store.get_trip(chat_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip

@app.post("/api/trigger-alert")
def trigger_alert(req: AlertRequest, background_tasks: BackgroundTasks):
    """Triggers autonomous replanning & Telegram alert dispatch."""
    # Ensure a trip exists for this user in trip_store
    if not trip_store.get_trip(req.chat_id):
        trip_store.save_trip(
            req.chat_id,
            "Goa",
            "Day 1: Morning beach walk at Anjuna. Afternoon Mandovi River Sunset Cruise. Evening Curlies."
        )

    result = proactive_engine.trigger_manual_disruption(req.chat_id, req.scenario)
    return result

@app.post("/api/poll-now")
def poll_now():
    """Forces the proactive engine to check live Open-Meteo weather for all active trips."""
    proactive_engine.check_weather_disruptions()
    return {"status": "success", "message": "Proactive check completed across all active destinations."}

@app.post("/api/chat")
def chat(req: ChatRequest):
    start = time.time()
    results = rag_pipeline.search(req.query, n_results=3)
    
    context_str = "\n".join([r['document'] for r in results])
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "Recommend places based on the context provided. Be extremely concise."},
                {"role": "user", "content": f"Context:\n{context_str}\n\nQuery: {req.query}"}
            ]
        )
        llm_resp = resp.choices[0].message.content
    except Exception as e:
        llm_resp = f"LLM note (Check key): {str(e)}"
        
    latency = int((time.time() - start) * 1000)
    
    return {
        "query": req.query,
        "matched_locations": [
            {
                "id": r["metadata"]["id"],
                "name": r["metadata"]["name"],
                "category": r["metadata"]["category"],
                "type": r["metadata"]["type"],
                "timings": r["metadata"]["timings"],
                "score": 1.0 - (r["distance"] or 0),
                "description": r["document"]
            } for r in results
        ],
        "llm_response": llm_resp,
        "latency_ms": latency
    }

@app.get("/api/jobs")
def get_jobs():
    """Returns actual active APScheduler jobs."""
    return proactive_engine.get_status()
