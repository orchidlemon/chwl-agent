"""
Teammate Mock API 集成适配层

将队友的 HTTP Mock API 格式转换成 Orchestrator 期望的格式。

队友 API 返回原始 JSON（如 {"items": [...]}），
Orchestrator 期望的格式是 {"success": True, "data": {...}}。

用法:
    from mocks.teammate_adapter import TeammateAPIAdapter
    adapter = TeammateAPIAdapter(base_url="http://127.0.0.1:8000")

    tools.register_mock("activities_search", adapter.activities_search)
    tools.register_mock("restaurants_search", adapter.restaurants_search)
    # ... etc

    # 对于队友没有的 planner 层 API，保留使用内存 mock
"""
from __future__ import annotations

import http.client
import json
import logging
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class TeammateAPIAdapter:
    """
    队友 API 适配器。

    每个方法对应一个 mock handler，供 ToolRegistry.register_mock 注册。
    返回格式: {"success": True, "data": {...}} 或 {"success": False, "error": {...}}
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        # 解析 host 和 port
        clean = base_url.replace("http://", "").replace("https://", "")
        if ":" in clean:
            self.host, port_str = clean.split(":")
            self.port = int(port_str)
        else:
            self.host = clean
            self.port = 80

    def _http_get(self, path: str, params: dict = None) -> dict:
        """GET 请求队友 API，返回标准格式"""
        url = path
        if params:
            filtered = {k: v for k, v in params.items() if v is not None and v != ""}
            if filtered:
                url += "?" + urlencode(filtered, doseq=True)

        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
            conn.request("GET", url)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            conn.close()
            return {"success": True, "data": body}
        except Exception as e:
            logger.warning("[TeammateAPI] GET %s 失败: %s", path, e)
            return {"success": False, "error": {
                "code": "network_error", "message": str(e),
                "is_retryable": True,
            }}

    def _http_post(self, path: str, payload: dict = None) -> dict:
        """POST 请求队友 API，返回标准格式"""
        body = json.dumps(payload or {}).encode("utf-8")
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
            conn.request("POST", path, body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode("utf-8"))
            conn.close()
            return {"success": True, "data": data}
        except Exception as e:
            logger.warning("[TeammateAPI] POST %s 失败: %s", path, e)
            return {"success": False, "error": {
                "code": "network_error", "message": str(e),
                "is_retryable": True,
            }}

    # ── Handler 实现 ──

    def activities_search(self, payload: dict) -> dict:
        """GET /api/activities/search → 转为 {activities: [...]} 格式"""
        result = self._http_get("/api/activities/search", {
            "scenario": payload.get("scene", "family"),
            "radius_km": payload.get("radius_km", 8),
            "categories": payload.get("categories", ""),
        })
        if result.get("success"):
            items = result["data"].get("items", [])
            # 转换为系统期望格式
            result["data"] = {"activities": items}
        return result

    def restaurants_search(self, payload: dict) -> dict:
        """GET /api/restaurants/search → 转为 {restaurants: [...]} 格式"""
        result = self._http_get("/api/restaurants/search", {
            "scenario": payload.get("scene", "family"),
            "preferences": payload.get("preferences", ""),
            "radius_km": payload.get("radius_km", 8),
        })
        if result.get("success"):
            items = result["data"].get("items", [])
            result["data"] = {"restaurants": items}
        return result

    def weather(self, payload: dict) -> dict:
        """GET /api/weather/current"""
        return self._http_get("/api/weather/current")

    def route_check(self, payload: dict) -> dict:
        """GET /api/route/estimate?from=&to=&mode="""
        destinations = payload.get("destinations", [])
        origin = payload.get("origin", "current_location")
        mode = payload.get("transport_mode", "taxi")
        # 返回多段路线（队友 API 一次只查一段）
        segments = []
        total_time = 0
        prev = origin
        for dest in destinations:
            result = self._http_get("/api/route/estimate", {
                "from": prev, "to": dest, "mode": mode,
            })
            if result.get("success"):
                seg = result["data"]
                segments.append({
                    "from": prev,
                    "to": dest,
                    "distance_km": seg.get("distance_km", 0),
                    "travel_time_min": seg.get("duration_min", 0),
                    "walk_time_min": seg.get("walk_distance_m", 0) // 80,
                    "transport_mode": mode,
                })
                total_time += seg.get("duration_min", 0)
            prev = dest

        return {"success": True, "data": {
            "total_travel_time_min": total_time,
            "segments": segments,
        }}

    def booking_status(self, payload: dict) -> dict:
        """GET /api/booking/status?poi_id="""
        watch_check = payload.get("watch_check", False)
        if watch_check and "poi_id" in payload:
            return self._http_get("/api/booking/status", {
                "poi_id": payload["poi_id"],
            })
        if "booking_ref" in payload:
            # booking_ref 可能是 poi_id，先查 booking status
            return self._http_get("/api/booking/status", {
                "poi_id": payload["booking_ref"],
            })
        return self._http_get("/api/booking/status", {
            "poi_id": payload.get("poi_id", ""),
        })

    def queue_status(self, payload: dict) -> dict:
        """GET /api/queue/status?poi_id="""
        return self._http_get("/api/queue/status", {
            "poi_id": payload.get("poi_id", payload.get("booking_ref", "")),
        })

    def alternatives_search(self, payload: dict) -> dict:
        """GET /api/alternatives/search?affected_node_id=&scenario=&reason="""
        return self._http_get("/api/alternatives/search", {
            "affected_node_id": payload.get("node_id", payload.get("affected_node_id", "")),
            "scenario": payload.get("scenario", "family"),
            "reason": payload.get("reason", "general"),
        })

    # ── 事件触发（用于 EnvSimulator）──

    def trigger_event(self, payload: dict) -> dict:
        """POST /api/sandbox/trigger-event"""
        return self._http_post("/api/sandbox/trigger-event", payload)

    def apply_llm_event(self, payload: dict) -> dict:
        """POST /api/sandbox/apply-llm-event"""
        return self._http_post("/api/sandbox/apply-llm-event", payload)

    def reset(self) -> dict:
        """POST /api/sandbox/reset"""
        return self._http_post("/api/sandbox/reset")

    def create_watch(self, payload: dict) -> dict:
        """POST /api/watch/create"""
        return self._http_post("/api/watch/create", payload)

    def memory_update(self, payload: dict) -> dict:
        """POST /api/memory/update"""
        return self._http_post("/api/memory/update", payload)

    def open_fulfillment_link(self, payload: dict) -> dict:
        """POST /api/fulfillment/open-link"""
        return self._http_post("/api/fulfillment/open-link", payload)

    def confirm_user_action(self, payload: dict) -> dict:
        """POST /api/fulfillment/confirm-user-action"""
        return self._http_post("/api/fulfillment/confirm-user-action", payload)

    def citywalk_detail(self, payload: dict) -> dict:
        """GET /api/citywalk/detail?citywalk_id="""
        return self._http_get("/api/citywalk/detail", {
            "citywalk_id": payload.get("citywalk_id", ""),
        })
