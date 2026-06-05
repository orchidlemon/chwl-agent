"""Tool layer: reliable async calls to Mock API.

Public function names and return shapes stay compatible with the current backend.
Internally calls go through ToolRegistry for retry, timeout, cache and circuit
breaker behavior.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

import httpx

from .tool_registry import ToolDefinition, ToolRegistry

MOCK_API = os.getenv("MOCK_API_URL", "http://127.0.0.1:8000")
logger = logging.getLogger(__name__)

_registry = ToolRegistry()
_registered = False


async def _http_get(path: str, params: dict | None = None, timeout_s: float = 6.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
        response = await client.get(f"{MOCK_API}{path}", params=params)
        response.raise_for_status()
        return response.json()


async def _http_post(path: str, body: dict | None = None, timeout_s: float = 6.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
        response = await client.post(f"{MOCK_API}{path}", json=body or {})
        response.raise_for_status()
        return response.json()


def _register_tools() -> None:
    global _registered
    if _registered:
        return

    def get_handler(path: str) -> Callable[[dict[str, Any]], Awaitable[dict]]:
        async def _handler(payload: dict[str, Any]) -> dict:
            return await _http_get(path, payload)
        return _handler

    def post_handler(path: str) -> Callable[[dict[str, Any]], Awaitable[dict]]:
        async def _handler(payload: dict[str, Any]) -> dict:
            return await _http_post(path, payload)
        return _handler

    configs = [
        ("activities_search", "/api/activities/search", "GET", 6500, 2, None),
        ("restaurants_search", "/api/restaurants/search", "GET", 6500, 2, None),
        ("location_current", "/api/location/current", "GET", 4000, 2, 30000),
        ("weather_current", "/api/weather/current", "GET", 4000, 2, 15000),
        ("queue_status", "/api/queue/status", "GET", 4000, 2, None),
        ("booking_status", "/api/booking/status", "GET", 4000, 2, None),
        ("alternatives_search", "/api/alternatives/search", "GET", 6000, 2, None),
        ("route_estimate", "/api/route/estimate", "GET", 5000, 2, None),
        ("events_poll", "/api/events/poll", "GET", 4000, 1, None),
        ("taxi_dispatch", "/api/taxi/dispatch", "POST", 6000, 1, None),
        ("apply_llm_event", "/api/sandbox/apply-llm-event", "POST", 6000, 1, None),
        ("trigger_preset_event", "/api/sandbox/trigger-event", "POST", 6000, 1, None),
    ]
    for name, path, method, timeout_ms, retries, cache_ttl in configs:
        handler = get_handler(path) if method == "GET" else post_handler(path)
        _registry.register_tool(
            ToolDefinition(
                name=name,
                timeout_ms=timeout_ms,
                max_retries=retries,
                retry_delay_ms=250,
                critical=name in {"activities_search", "restaurants_search", "weather_current"},
                cache_ttl_ms=cache_ttl,
            ),
            handler,
        )

    _registry.register_tool(
        ToolDefinition(name="booking_execute", timeout_ms=2000, max_retries=1, retry_delay_ms=100),
        _booking_execute_handler,
    )
    _registered = True


async def _request_with_registry(tool_name: str, payload: dict | None = None) -> dict:
    _register_tools()
    result = await _registry.invoke(tool_name, payload or {})
    if result.get("success"):
        return result.get("data", {}) or {}
    error = result.get("error", {})
    logger.warning("Tool %s failed: %s", tool_name, error.get("message", error))
    return {"error": error.get("message", "tool failed"), "code": error.get("code")}


async def _get(path: str, params: dict | None = None) -> dict:
    name_by_path = {
        "/api/activities/search": "activities_search",
        "/api/restaurants/search": "restaurants_search",
        "/api/location/current": "location_current",
        "/api/weather/current": "weather_current",
        "/api/queue/status": "queue_status",
        "/api/booking/status": "booking_status",
        "/api/alternatives/search": "alternatives_search",
        "/api/route/estimate": "route_estimate",
        "/api/events/poll": "events_poll",
    }
    tool_name = name_by_path.get(path)
    if not tool_name:
        try:
            return await _http_get(path, params)
        except Exception as exc:
            logger.warning("Mock API GET %s failed: %s", path, exc)
            return {}
    data = await _request_with_registry(tool_name, params or {})
    return {} if "error" in data else data


async def _post(path: str, body: dict) -> dict:
    name_by_path = {
        "/api/taxi/dispatch": "taxi_dispatch",
        "/api/sandbox/apply-llm-event": "apply_llm_event",
        "/api/sandbox/trigger-event": "trigger_preset_event",
    }
    tool_name = name_by_path.get(path)
    if not tool_name:
        try:
            return await _http_post(path, body)
        except Exception as exc:
            logger.warning("Mock API POST %s failed: %s", path, exc)
            return {"error": str(exc)}
    return await _request_with_registry(tool_name, body or {})


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
    """Get current weather from Mock API with retry/circuit-breaker protection."""
    return await _get("/api/weather/current")


async def get_queue_status(poi_id: str) -> dict:
    """Get real-time queue status for a POI."""
    return await _get("/api/queue/status", {"poi_id": poi_id})


async def get_booking_status(poi_id: str) -> dict:
    """Get booking availability for a POI."""
    return await _get("/api/booking/status", {"poi_id": poi_id})


async def get_alternatives(scenario: str, reason: str,
                           affected_node_id: str = None) -> dict:
    """Get replacement POI candidates from Mock API."""
    params = {"scenario": scenario, "reason": reason}
    if affected_node_id:
        params["affected_node_id"] = affected_node_id
    return await _get("/api/alternatives/search", params)


async def get_route(from_id: str, to_id: str, mode: str = "taxi") -> dict:
    """Estimate a single route segment between two POIs."""
    return await _get("/api/route/estimate", {"from": from_id, "to": to_id, "mode": mode})


async def get_routes(origin: str, destinations: list[str], mode: str = "taxi") -> dict:
    """Estimate multiple route segments and aggregate teammate-style output."""
    if not destinations:
        return {"total_travel_time_min": 0, "segments": []}

    _register_tools()
    segments = []
    total_time = 0
    pairs = []
    previous = origin
    for destination in destinations:
        pairs.append((previous, destination))
        previous = destination

    calls = [
        ("route_estimate", {"from": start, "to": end, "mode": mode})
        for start, end in pairs
    ]
    raw_results = await _registry.invoke_parallel(calls)

    for index, (start, destination) in enumerate(pairs):
        result = raw_results.get(f"route_estimate#{index}") or raw_results.get("route_estimate", {})
        route = result.get("data", {}) if result.get("success") else {}
        segment = {
            "from": start,
            "to": destination,
            "distance_km": route.get("distance_km", 0),
            "travel_time_min": route.get("duration_min", route.get("estimated_time_min", 0)),
            "walk_time_min": route.get("walk_distance_m", 0) // 80 if route.get("walk_distance_m") else 0,
            "transport_mode": mode,
            "raw": route,
        }
        total_time += int(segment["travel_time_min"] or 0)
        segments.append(segment)
    return {"total_travel_time_min": total_time, "segments": segments}


async def poll_events() -> list:
    """Poll pending events from Mock API."""
    data = await _get("/api/events/poll")
    return data.get("events", [])


async def dispatch_taxi() -> dict:
    """Request a taxi from Mock API."""
    return await _post("/api/taxi/dispatch", {})


async def apply_llm_event(event: dict) -> dict:
    """Push an LLM-generated environment event to Mock API."""
    return await _post("/api/sandbox/apply-llm-event", event)


async def trigger_preset_event(event_type: str, target_poi_id: str = None) -> dict:
    """Trigger a named preset event."""
    return await _post("/api/sandbox/trigger-event", {
        "event_type": event_type,
        "target_poi_id": target_poi_id,
    })


async def booking_execute(
    node: dict,
    on_status: Callable[[dict], Awaitable[None] | None] | None = None,
) -> dict:
    """Execute a frontend-compatible booking lifecycle for one itinerary node."""
    _register_tools()
    result = await _registry.invoke("booking_execute", {"node": node})
    data = result.get("data", {}) if result.get("success") else {
        "booking_ref": None,
        "status": "failed",
        "error": result.get("error", {}).get("message", "booking failed"),
    }

    stages = [
        {**data, "status": "queued", "progress": 0.1},
        {**data, "status": "processing", "progress": 0.55},
        {**data, "status": "confirmed", "progress": 1.0},
    ]
    if data.get("status") == "failed":
        stages = [{**data, "status": "failed", "progress": 1.0}]

    for index, stage in enumerate(stages):
        if on_status:
            callback_result = on_status(stage)
            if hasattr(callback_result, "__await__"):
                await callback_result
        if index < len(stages) - 1:
            await asyncio.sleep(0.25)
    return stages[-1]


async def _booking_execute_handler(payload: dict) -> dict:
    node = payload.get("node", {})
    return {
        "booking_ref": f"BK-{str(node.get('poiId') or node.get('id') or 'NODE')[-6:].upper()}",
        "node_id": node.get("id", ""),
        "poi_id": node.get("poiId", ""),
        "name": node.get("name", ""),
        "status": "queued",
        "estimated_wait_minutes": 1,
    }
