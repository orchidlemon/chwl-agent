from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import copy
import json
import time
import uuid

from mock_data import BASE_STATE, CITYWALKS, POIS, ROUTES
from memory_store import BASE_MEMORY


HOST = "127.0.0.1"
PORT = 8000

STATE = copy.deepcopy(BASE_STATE)
MEMORY = copy.deepcopy(BASE_MEMORY)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def first(query, key, default=None):
    values = query.get(key)
    if not values:
        return default
    return values[0]


def split_csv(value):
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def get_poi(poi_id):
    for poi in POIS:
        if poi["poi_id"] == poi_id:
            return poi
    return None


def with_dynamic_status(poi):
    item = copy.deepcopy(poi)
    queue = STATE["queues"].get(item["poi_id"])
    if queue is not None:
        item["queue_min"] = queue["estimated_wait_min"]
        item["queue_tables"] = queue["queue_tables"]
        if queue["estimated_wait_min"] >= 45:
            item.setdefault("risk_facts", [])
            if "排队时间偏长" not in item["risk_facts"]:
                item["risk_facts"].append("排队时间偏长")
    booking = STATE["bookings"].get(item["poi_id"])
    if booking is not None:
        item["booking_required"] = booking["booking_required"]
        item["availability"] = booking["availability"]
        item["available_slots"] = booking["available_slots"]
        if booking.get("risk_facts"):
            item.setdefault("risk_facts", [])
            for risk in booking["risk_facts"]:
                if risk not in item["risk_facts"]:
                    item["risk_facts"].append(risk)
    return item


def filter_by_common_params(items, query):
    scenario = first(query, "scenario")
    categories = split_csv(first(query, "categories", ""))
    radius_raw = first(query, "radius_km")
    radius = float(radius_raw) if radius_raw else None

    result = []
    for item in items:
        if scenario and item.get("scenario") not in (scenario, "both"):
            continue
        if categories and item.get("category") not in categories:
            continue
        if radius is not None and item.get("distance_km", 0) > radius:
            continue
        result.append(with_dynamic_status(item))
    return result


def item_matches_preferences(item, preferences):
    if not preferences:
        return True
    preference_aliases = {
        "清淡": ("light_food_options",),
        "低油": ("low_oil_options",),
        "减脂": ("light_food_options", "low_oil_options"),
        "儿童椅": ("child_seat",),
        "室内": ("indoor",),
        "商场": ("mall",),
    }
    searchable = set(item.get("facts_tags", []))
    searchable.update(item.get("menu_features", []))
    searchable.update(item.get("activity_features", []))
    searchable.update(item.get("location_features", []))
    searchable.update(k for k, v in item.get("facilities", {}).items() if v)
    venue = item.get("venue", {})
    if venue.get("environment"):
        searchable.add(venue["environment"])

    for pref in preferences:
        aliases = preference_aliases.get(pref, (pref,))
        if any(alias in searchable for alias in aliases):
            return True
    return False


def is_indoor_activity(item):
    return item.get("venue", {}).get("environment") == "indoor"


def make_event(event_type, target_poi_id=None, message=None, severity="medium"):
    event = {
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "type": event_type,
        "severity": severity,
        "poi_id": target_poi_id,
        "message": message or event_type,
        "occurred_at": now_iso(),
        "requires_user_confirmation": True,
    }
    STATE["events"].append(event)
    return event


def trigger_event(event_type, target_poi_id=None):
    if event_type == "queue_spike":
        poi_id = target_poi_id or "rest_family_001"
        STATE["queues"][poi_id] = {
            "queue_tables": 26,
            "estimated_wait_min": 70,
            "can_take_number": True,
            "status": "queue_spike",
            "updated_at": now_iso(),
        }
        return make_event(
            "queue_spike",
            poi_id,
            "餐厅排队从18分钟升至70分钟，可能影响晚饭节奏",
            "high",
        )
    if event_type == "weather_heavy_rain":
        STATE["weather"]["condition"] = "heavy_rain"
        STATE["weather"]["rain_level"] = "heavy"
        STATE["weather"]["risk_level"] = "high"
        STATE["weather"]["updated_at"] = now_iso()
        return make_event(
            "weather_heavy_rain",
            target_poi_id,
            "北京朝阳区降雨增强，户外活动体验风险升高",
            "high",
        )
    if event_type == "booking_full":
        poi_id = target_poi_id or "act_friend_001"
        STATE["bookings"][poi_id] = {
            "booking_required": True,
            "availability": "full",
            "available_slots": [],
            "external_entry": {
                "type": "wechat_official_account",
                "name": "预约入口",
                "url": f"mock://wechat/reservation/{poi_id}",
            },
            "risk_facts": ["今日预约已满", "需要实名预约"],
        }
        return make_event(
            "booking_full",
            poi_id,
            "目标活动今日预约已满，需要切换备用节点",
            "high",
        )
    if event_type == "child_tired":
        return make_event(
            "child_tired",
            target_poi_id,
            "用户反馈孩子累了，建议缩短后续行程",
            "medium",
        )
    if event_type == "restaurant_closed":
        poi_id = target_poi_id or "rest_family_001"
        return make_event(
            "restaurant_closed",
            poi_id,
            "餐厅临时暂停接待，需要替换餐厅节点",
            "high",
        )
    return make_event(event_type, target_poi_id, f"已触发事件：{event_type}", "medium")


def apply_llm_event(payload):
    event_type = payload.get("event_type", "llm_environment_event")
    target_poi_id = payload.get("target_poi_id")
    severity = payload.get("severity", "medium")
    message = payload.get("message", "大模型模拟了一个环境变化事件")
    state_patch = payload.get("state_patch", {})

    queue_patch = state_patch.get("queue")
    if queue_patch and target_poi_id:
        current = STATE["queues"].get(target_poi_id, {})
        current.update(queue_patch)
        current["updated_at"] = now_iso()
        STATE["queues"][target_poi_id] = current

    weather_patch = state_patch.get("weather")
    if weather_patch:
        STATE["weather"].update(weather_patch)
        STATE["weather"]["updated_at"] = now_iso()

    booking_patch = state_patch.get("booking")
    if booking_patch and target_poi_id:
        current = STATE["bookings"].get(target_poi_id, {})
        current.update(booking_patch)
        STATE["bookings"][target_poi_id] = current

    event = make_event(event_type, target_poi_id, message, severity)
    event["source"] = "llm_simulator"
    event["recommended_poll_after_sec"] = payload.get("recommended_poll_after_sec", 30)
    event["llm_reason"] = payload.get("reason", "")
    return event


class MockApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            json_response(self, 200, {"status": "ok", "service": "meituan-local-life-mock-api", "version": "llm-simulator-v2"})
            return

        if path == "/api/memory/profile":
            json_response(self, 200, MEMORY)
            return

        if path == "/api/activities/search":
            items = [poi for poi in POIS if poi["type"] == "activity"]
            json_response(self, 200, {"items": filter_by_common_params(items, query)})
            return

        if path == "/api/citywalk/detail":
            citywalk_id = first(query, "citywalk_id")
            detail = CITYWALKS.get(citywalk_id)
            if not detail:
                json_response(self, 404, {"error": "citywalk_not_found"})
                return
            json_response(self, 200, detail)
            return

        if path == "/api/restaurants/search":
            items = [poi for poi in POIS if poi["type"] == "restaurant"]
            result = filter_by_common_params(items, query)
            preferences = split_csv(first(query, "preferences", ""))
            if preferences:
                result = [item for item in result if item_matches_preferences(item, preferences)]
            json_response(self, 200, {"items": result})
            return

        if path == "/api/route/estimate":
            from_id = first(query, "from")
            to_id = first(query, "to")
            mode = first(query, "mode", "taxi")
            route = ROUTES.get((from_id, to_id, mode)) or ROUTES.get((to_id, from_id, mode))
            if not route:
                route = {
                    "from": from_id,
                    "to": to_id,
                    "mode": mode,
                    "distance_km": 3.2,
                    "duration_min": 16,
                    "walk_distance_m": 120,
                    "traffic_level": "normal",
                    "source": "mock_default",
                }
            json_response(self, 200, route)
            return

        if path == "/api/weather/current":
            json_response(self, 200, STATE["weather"])
            return

        if path == "/api/queue/status":
            poi_id = first(query, "poi_id")
            status = STATE["queues"].get(poi_id)
            if not status:
                status = {
                    "poi_id": poi_id,
                    "queue_tables": 0,
                    "estimated_wait_min": 0,
                    "can_take_number": False,
                    "status": "unknown",
                    "updated_at": now_iso(),
                }
            else:
                status = {"poi_id": poi_id, **status}
            json_response(self, 200, status)
            return

        if path == "/api/booking/status":
            poi_id = first(query, "poi_id")
            poi = get_poi(poi_id)
            status = STATE["bookings"].get(poi_id)
            if not status:
                status = {
                    "poi_id": poi_id,
                    "name": poi["name"] if poi else poi_id,
                    "booking_required": bool(poi and poi.get("booking_required")),
                    "availability": poi.get("availability", "available") if poi else "unknown",
                    "available_slots": poi.get("available_slots", []) if poi else [],
                    "external_entry": poi.get("external_entry") if poi else None,
                    "risk_facts": poi.get("risk_facts", []) if poi else [],
                }
            else:
                status = {"poi_id": poi_id, "name": poi["name"] if poi else poi_id, **status}
            json_response(self, 200, status)
            return

        if path == "/api/fulfillment/status":
            poi_id = first(query, "poi_id")
            if poi_id:
                json_response(self, 200, STATE["fulfillment"].get(poi_id, {"poi_id": poi_id, "status": "none"}))
            else:
                json_response(self, 200, {"items": list(STATE["fulfillment"].values())})
            return

        if path == "/api/watch/status":
            watch_id = first(query, "watch_id")
            if watch_id:
                json_response(self, 200, STATE["watches"].get(watch_id, {"watch_id": watch_id, "status": "not_found"}))
            else:
                json_response(self, 200, {"items": list(STATE["watches"].values())})
            return

        if path == "/api/events/poll":
            json_response(self, 200, {"events": STATE["events"]})
            return

        if path in ("/api/sandbox/scenario-script", "/api/sandbox/scenario_script") or path.startswith("/api/sandbox/scenario"):
            json_response(self, 200, STATE["scenario_script"])
            return

        if path == "/api/alternatives/search":
            reason = first(query, "reason", "general")
            scenario = first(query, "scenario", "family")
            affected_node_id = first(query, "affected_node_id")
            items = []
            if reason in ("queue_spike", "restaurant_closed") or (affected_node_id or "").startswith("rest_"):
                items = [poi for poi in POIS if poi["type"] == "restaurant" and poi["scenario"] in (scenario, "both")]
            elif reason in ("weather_heavy_rain", "child_tired"):
                items = [
                    poi for poi in POIS
                    if poi["type"] == "activity"
                    and poi["scenario"] in (scenario, "both")
                    and is_indoor_activity(poi)
                ]
            else:
                items = [poi for poi in POIS if poi["scenario"] in (scenario, "both")]
            dynamic = [with_dynamic_status(item) for item in items if item["poi_id"] != affected_node_id]
            json_response(
                self,
                200,
                {
                    "affected_node_id": affected_node_id,
                    "reason": reason,
                    "recommended": dynamic[:2],
                    "more_options": dynamic[2:],
                    "replacement_scope": "pending_nodes_only",
                    "protected_nodes": STATE["protected_nodes"],
                },
            )
            return

        json_response(self, 404, {"error": "not_found", "path": path})

    def do_POST(self):
        global STATE, MEMORY

        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = read_json(self)
        except json.JSONDecodeError:
            json_response(self, 400, {"error": "invalid_json"})
            return

        if path == "/api/memory/update":
            user_id = payload.get("user_id") or MEMORY.get("user_id", "xiaoming")
            MEMORY["user_id"] = user_id
            updates = payload.get("updates", {})
            source = payload.get("source", "user_confirmed")
            scope = payload.get("scope", "session_facts")
            if scope not in ("session_facts", "confirmed_preferences", "derived_preferences"):
                json_response(self, 400, {"error": "invalid_memory_scope"})
                return
            if scope == "confirmed_preferences" and not MEMORY["consent"].get("can_store_long_term_preferences"):
                json_response(
                    self,
                    403,
                    {
                        "error": "long_term_memory_not_allowed",
                        "message": "需要用户明确授权后才能写入长期偏好",
                    },
                )
                return
            MEMORY.setdefault(scope, {}).update(updates)
            MEMORY["status"] = "has_user_or_agent_written_context"
            if source == "user_confirmed":
                MEMORY["last_confirmed_at"] = now_iso()
            MEMORY.setdefault("recent_updates", []).append({
                "source": source,
                "scope": scope,
                "updates": updates,
                "updated_at": now_iso(),
            })
            json_response(self, 200, {"status": "updated", "profile": MEMORY})
            return

        if path == "/api/fulfillment/open-link":
            poi_id = payload.get("poi_id")
            action = payload.get("action")
            poi = get_poi(poi_id)
            entry = (poi or {}).get("external_entry") or {
                "type": "mock_page",
                "url": f"mock://external/{poi_id}/{action}",
            }
            json_response(
                self,
                200,
                {
                    "action": action,
                    "poi_id": poi_id,
                    "entry_type": entry.get("type"),
                    "url": entry.get("url"),
                    "display_text": "已打开外部履约入口，请完成后返回确认",
                },
            )
            return

        if path == "/api/fulfillment/confirm-user-action":
            poi_id = payload.get("poi_id")
            result = payload.get("result", "completed")
            action = payload.get("action", "reserve")
            status = {
                "poi_id": poi_id,
                "action": action,
                "result": result,
                "user_note": payload.get("user_note", ""),
                "fulfillment_status": f"{action}_{result}_by_user",
                "voucher_code": f"MT-MOCK-{uuid.uuid4().hex[:6].upper()}",
                "locked": result == "completed",
                "updated_at": now_iso(),
            }
            STATE["fulfillment"][poi_id] = status
            if status["locked"] and poi_id not in STATE["protected_nodes"]:
                STATE["protected_nodes"].append(poi_id)
            json_response(self, 200, status)
            return

        if path == "/api/watch/create":
            watch_id = f"watch_{uuid.uuid4().hex[:8]}"
            watch = {
                "watch_id": watch_id,
                "status": "running",
                "watch_targets": payload.get("watch_targets", []),
                "created_at": now_iso(),
                "message": "已开始后台盯排队、天气、预约状态",
            }
            STATE["watches"][watch_id] = watch
            json_response(self, 200, watch)
            return

        if path == "/api/sandbox/trigger-event":
            event = trigger_event(payload.get("event_type"), payload.get("target_poi_id"))
            json_response(self, 200, {"status": "triggered", "event": event})
            return

        if path == "/api/sandbox/apply-llm-event":
            event = apply_llm_event(payload)
            json_response(self, 200, {"status": "applied", "event": event})
            return

        if path == "/api/sandbox/reset":
            STATE = copy.deepcopy(BASE_STATE)
            MEMORY = copy.deepcopy(BASE_MEMORY)
            json_response(self, 200, {"status": "reset", "updated_at": now_iso()})
            return

        json_response(self, 404, {"error": "not_found", "path": path})

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), MockApiHandler)
    print(f"Meituan local-life Mock API running at http://{HOST}:{PORT}")
    print("Try: http://127.0.0.1:8000/api/health")
    server.serve_forever()


if __name__ == "__main__":
    main()
