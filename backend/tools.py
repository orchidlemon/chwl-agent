"""Tool layer: async HTTP calls to Mock API (http://127.0.0.1:8000).

The Mock API is the single source of truth for all POI/queue/weather data.
The Simulator Agent writes to Mock API; the User Agent reads from Mock API.
"""
import logging
import os

import httpx

MOCK_API = os.getenv("MOCK_API_URL", "http://127.0.0.1:8000")
logger = logging.getLogger(__name__)


# ── Low-level helpers ────────────────────────────────────────────────

async def _get(path: str, params: dict = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
            r = await client.get(f"{MOCK_API}{path}", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning(f"Mock API GET {path} failed: {e}")
        return {}


async def _post(path: str, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
            r = await client.post(f"{MOCK_API}{path}", json=body)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning(f"Mock API POST {path} failed: {e}")
        return {"error": str(e)}


# ── User Agent tools (read) ──────────────────────────────────────────

async def get_activities(scenario: str, radius_km: float = 10.0,
                         categories: list = None) -> list:
    """Search activities from Mock API by scenario + radius."""
    params = {"scenario": scenario, "radius_km": str(radius_km)}
    if categories:
        params["categories"] = ",".join(categories)
    data = await _get("/api/activities/search", params)
    return data.get("items", [])


async def get_restaurants(scenario: str, preferences: list = None,
                          radius_km: float = 10.0) -> list:
    """Search restaurants from Mock API, optionally filtered by food preferences."""
    params = {"scenario": scenario, "radius_km": str(radius_km)}
    if preferences:
        params["preferences"] = ",".join(preferences)
    data = await _get("/api/restaurants/search", params)
    return data.get("items", [])


async def get_user_location() -> dict:
    """Fetch mock user current location from Mock API."""
    return await _get("/api/location/current")


async def get_weather() -> dict:
    """Get current weather from Mock API."""
    return await _get("/api/weather/current")


async def get_queue_status(poi_id: str) -> dict:
    """Get real-time queue status for a POI from Mock API."""
    return await _get("/api/queue/status", {"poi_id": poi_id})


async def get_booking_status(poi_id: str) -> dict:
    """Get booking availability for a POI from Mock API."""
    return await _get("/api/booking/status", {"poi_id": poi_id})


async def get_alternatives(scenario: str, reason: str,
                           affected_node_id: str = None) -> dict:
    """Get replacement POI candidates from Mock API."""
    params = {"scenario": scenario, "reason": reason}
    if affected_node_id:
        params["affected_node_id"] = affected_node_id
    return await _get("/api/alternatives/search", params)


async def get_route(from_id: str, to_id: str, mode: str = "taxi") -> dict:
    """Estimate route between two POIs."""
    return await _get("/api/route/estimate",
                      {"from": from_id, "to": to_id, "mode": mode})


async def poll_events() -> list:
    """Poll pending events from Mock API."""
    data = await _get("/api/events/poll")
    return data.get("events", [])


# ── Simulator Agent tools (write) ───────────────────────────────────

async def dispatch_taxi() -> dict:
    """Request a taxi from Mock API — returns plate, driver, ETA from simulation pool."""
    return await _post("/api/taxi/dispatch", {})


async def apply_llm_event(event: dict) -> dict:
    """
    Simulator Agent: push an LLM-generated environment event to Mock API.
    This modifies the shared queue/weather/booking state that the User Agent reads.
    """
    return await _post("/api/sandbox/apply-llm-event", event)


async def trigger_preset_event(event_type: str, target_poi_id: str = None) -> dict:
    """Simulator Agent: trigger a named preset event (queue_spike, etc.)."""
    return await _post("/api/sandbox/trigger-event", {
        "event_type": event_type,
        "target_poi_id": target_poi_id,
    })
