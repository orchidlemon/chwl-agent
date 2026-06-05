"""Per-user session sandbox. Each session is fully isolated."""
import copy
import time
import uuid
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mock_api'))
from mock_data import BASE_STATE
from .state_machine import (
    NodeState, apply_node_state, safe_phase_transition,
)

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def _now_hms():
    return time.strftime("%H:%M:%S")


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}

    # Lifecycle

    def create(self) -> str:
        sid = str(uuid.uuid4())
        self._sessions[sid] = {
            "session_id": sid,
            "created_at": time.time(),
    # Phase
            "phase": "gathering",
            # LLM-inferred prefs awaiting user confirmation
            "pending_inference": None,
    # Memory
            "memory": {
                "session_facts": {},
                "confirmed_preferences": {},
                "derived_preferences": {},
            },
    # Itinerary
            "itinerary": [],
            # Fulfillment results per node
            "fulfillment": {},
            # Watch task ids
            "watch_ids": [],
            # Per-user copy of mock dynamic state
            "sandbox": copy.deepcopy(BASE_STATE),
            # Pending exception waiting for user confirm
            "pending_exception": None,
            # Unified user confirmation requests
            "pending_confirmations": {},
            "resolved_confirmations": {},
            "confirmation_history": [],
    # Queue history and trend
            "queue_history": {},
            # Events log for monitor panel (simulator + main agent)
            "monitor_events": [],
            # Already warned about booking for these poi_ids
            "booking_warned": [],
    # Pending monitor message for chat
            "pending_monitor_msg": None,
    # Phase
            "phase_transition_log": [],
    # Conversation history during clarify phase (for context-aware refinement)
            "clarify_history": [],
        }
        return sid

    def get(self, session_id: str) -> Optional[dict]:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: Optional[str]) -> tuple[str, dict]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        sid = self.create()
        return sid, self._sessions[sid]

    # Phase

    def get_phase(self, session_id: str) -> str:
        s = self.get(session_id)
        return s.get("phase", "gathering") if s else "gathering"

    def _phase_context(self, s: dict) -> dict:
        nodes = s.get("itinerary", [])
        return {
            "has_itinerary": bool(nodes),
            "pending_exception": bool(s.get("pending_exception")),
            "all_completed": bool(nodes) and all(n.get("_checked") or n.get("completed_lock") for n in nodes),
        }

    def set_phase(self, session_id: str, phase: str):
        """Safely transition phase; fallback instead of surfacing FSM errors to users."""
        s = self.get(session_id)
        if not s:
            return None
        current = s.get("phase", "gathering")
        result = safe_phase_transition(current, phase, self._phase_context(s))
        s["phase"] = result.to_state
        s.setdefault("phase_transition_log", []).append({
            "time": _now_hms(),
            "ok": result.ok,
            "from": result.from_state,
            "requested": result.requested_state,
            "to": result.to_state,
            "fallback": result.fallback_state,
            "reason": result.reason,
        })
        s["phase_transition_log"] = s["phase_transition_log"][-20:]
        if not result.ok:
            self.add_monitor_event(
                session_id,
                "state_machine",
                f"阻断非法阶段跳转: {result.from_state} -> {result.requested_state}，fallback={result.to_state}",
                "phase_fallback",
            )
        return result

    # Clarify conversation history

    def append_clarify_message(self, session_id: str, content: str):
        s = self.get(session_id)
        if s:
            s.setdefault("clarify_history", []).append(content)
            s["clarify_history"] = s["clarify_history"][-10:]

    def get_clarify_history(self, session_id: str) -> list:
        s = self.get(session_id)
        return s.get("clarify_history", []) if s else []

    def clear_clarify_history(self, session_id: str):
        s = self.get(session_id)
        if s:
            s["clarify_history"] = []

    # Pending inference

    def get_pending_inference(self, session_id: str) -> Optional[dict]:
        s = self.get(session_id)
        return s.get("pending_inference") if s else None

    def set_pending_inference(self, session_id: str, inference: Optional[dict]):
        s = self.get(session_id)
        if s:
            s["pending_inference"] = inference

    # Memory

    def update_memory(self, session_id: str, scope: str, updates: dict):
        s = self.get(session_id)
        if s:
            s["memory"].setdefault(scope, {}).update(updates)

    def get_memory(self, session_id: str) -> dict:
        s = self.get(session_id)
        return s["memory"] if s else {}

    # Itinerary

    def set_itinerary(self, session_id: str, nodes: list):
        s = self.get(session_id)
        if s:
            s["itinerary"] = nodes

    def get_itinerary(self, session_id: str) -> list:
        s = self.get(session_id)
        return s["itinerary"] if s else []

    def apply_node_action(self, session_id: str, node_id: str, action: str) -> list:
        nodes = self.get_itinerary(session_id)
        if action == "delete":
            # completed_lock nodes are immutable; caller should check before calling
            nodes = [n for n in nodes if n["id"] != node_id]
        elif action == "pin":
            updated_nodes = []
            for n in nodes:
                if n["id"] != node_id:
                    updated_nodes.append(n)
                    continue
                target_state = NodeState.PLANNED if (n.get("user_pinned") or n.get("pinned")) else NodeState.USER_PINNED
                updated, result = apply_node_state(n, target_state)
                if not result.ok:
                    self.add_monitor_event(
                        session_id, "state_machine",
                        f"阻断非法节点跳转: {result.from_state} -> {result.requested_state}",
                        "node_state_fallback", node_id,
                    )
                updated_nodes.append(updated)
            nodes = updated_nodes
        self.set_itinerary(session_id, nodes)
        return nodes

    def complete_node(self, session_id: str, node_id: str):
        """Mark node as user-completed. Sets completed_lock which is immutable."""
        nodes = self.get_itinerary(session_id)
        updated_nodes = []
        for n in nodes:
            if n["id"] != node_id:
                updated_nodes.append(n)
                continue
            updated, result = apply_node_state(n, NodeState.COMPLETED_LOCK)
            if not result.ok:
                self.add_monitor_event(
                    session_id, "state_machine",
                    f"阻断非法节点跳转: {result.from_state} -> {result.requested_state}",
                    "node_state_fallback", node_id,
                )
                updated = {**n, "completed_lock": True, "_checked": True, "status": "completed"}
            updated_nodes.append(updated)
        self.set_itinerary(session_id, updated_nodes)
        if updated_nodes and all(n.get("_checked") or n.get("completed_lock") for n in updated_nodes):
            self.set_phase(session_id, "completed")
        return updated_nodes

    def update_node(self, session_id: str, node_id: str, updates: dict) -> list:
        nodes = self.get_itinerary(session_id)
        nodes = [{**n, **updates} if n["id"] == node_id else n for n in nodes]
        self.set_itinerary(session_id, nodes)
        return nodes

    def lock_node(self, session_id: str, node_id: str):
        nodes = self.get_itinerary(session_id)
        updated_nodes = []
        for n in nodes:
            if n["id"] != node_id:
                updated_nodes.append(n)
                continue
            updated, result = apply_node_state(n, NodeState.SOFT_LOCK)
            if not result.ok:
                self.add_monitor_event(
                    session_id, "state_machine",
                    f"阻断非法节点跳转: {result.from_state} -> {result.requested_state}",
                    "node_state_fallback", node_id,
                )
                updated = {**n, "soft_lock": True, "locked": True, "status": "done"}
            updated_nodes.append(updated)
        self.set_itinerary(session_id, updated_nodes)
        return updated_nodes

    # Sandbox (per-user dynamic state)

    def get_sandbox(self, session_id: str) -> dict:
        s = self.get(session_id)
        return s["sandbox"] if s else {}

    def trigger_queue_spike(self, session_id: str, poi_id: str = "rest_family_001") -> dict:
        sb = self.get_sandbox(session_id)
        sb["queues"][poi_id] = {
            "queue_tables": 26,
            "estimated_wait_min": 90,
            "can_take_number": True,
            "status": "queue_spike",
        }
        event = {
            "event_id": f"evt_qs_{session_id[:8]}",
            "type": "queue_spike",
            "severity": "high",
            "poi_id": poi_id,
            "message": "餐厅排队从18分钟突增到90分钟，可能影响晚餐安排",
            "requires_user_confirmation": True,
        }
        sb["events"].append(event)
        s = self.get(session_id)
        if s:
            s["pending_exception"] = event
        self.update_queue_history(session_id, poi_id, 90)
        self.add_monitor_event(session_id, "sandbox", f"排队突增: {poi_id} -> 90分钟", "queue_spike", poi_id)
        return event

    def trigger_weather_rain(self, session_id: str) -> dict:
        sb = self.get_sandbox(session_id)
        sb["weather"].update({"condition": "heavy_rain", "rain_level": "heavy", "risk_level": "high"})
        event = {
            "event_id": f"evt_wr_{session_id[:8]}",
            "type": "weather_heavy_rain",
            "severity": "high",
            "message": "北京朝阳区降雨增强，户外活动体验风险升高",
            "requires_user_confirmation": True,
        }
        sb["events"].append(event)
        s = self.get(session_id)
        if s:
            s["pending_exception"] = event
        self.add_monitor_event(session_id, "sandbox", "天气恶化: 大雨预警", "weather_heavy_rain")
        return event

    def get_queue(self, session_id: str, poi_id: str) -> dict:
        sb = self.get_sandbox(session_id)
        return sb["queues"].get(poi_id, {})

    def get_weather(self, session_id: str) -> dict:
        sb = self.get_sandbox(session_id)
        return sb.get("weather", {})

    def clear_exception(self, session_id: str):
        s = self.get(session_id)
        if s:
            s["pending_exception"] = None

    # Queue history and trend

    def update_queue_history(self, session_id: str, poi_id: str, wait_min: int):
        s = self.get(session_id)
        if not s:
            return
        history = s["queue_history"].setdefault(poi_id, [])
        history.append((time.time(), wait_min))
        s["queue_history"][poi_id] = history[-10:]

    def get_queue_trend(self, session_id: str, poi_id: str) -> str:
        s = self.get(session_id)
        if not s:
            return "stable"
        history = s.get("queue_history", {}).get(poi_id, [])
        if len(history) < 2:
            return "stable"
        last = history[-1][1]
        prev = history[-2][1]
        if last > prev + 5:
            return "rising"
        elif last < prev - 5:
            return "falling"
        return "stable"

    # Monitor events

    def add_monitor_event(self, session_id: str, source: str, message: str,
                          event_type: str = "info", poi_id: str = None):
        s = self.get(session_id)
        if not s:
            return
        s["monitor_events"].append({
            "source": source,       # "simulator" | "main_agent" | "sandbox"
            "type": event_type,
            "message": message,
            "poi_id": poi_id,
            "time": _now_hms(),
        })
        s["monitor_events"] = s["monitor_events"][-50:]

    def get_monitor_events(self, session_id: str) -> list:
        s = self.get(session_id)
        return s.get("monitor_events", []) if s else []

    # Simulator event application

    def apply_simulator_event(self, session_id: str, event: dict) -> dict:
        """Apply a simulator-generated event to the session sandbox."""
        s = self.get(session_id)
        if not s:
            return {}

        state_patch = event.get("state_patch", {})
        target_poi_id = event.get("target_poi_id")
        sb = s["sandbox"]

        queue_patch = state_patch.get("queue")
        if queue_patch and target_poi_id:
            current = sb["queues"].get(target_poi_id, {})
            current.update(queue_patch)
            current["updated_at"] = _now()
            sb["queues"][target_poi_id] = current
            if "estimated_wait_min" in queue_patch:
                self.update_queue_history(session_id, target_poi_id,
                                          queue_patch["estimated_wait_min"])
            self.add_monitor_event(
                session_id, "simulator",
                f"Mock API更新: {target_poi_id} 排队 -> {queue_patch.get('estimated_wait_min')}分钟",
                "queue_update", target_poi_id
            )

        weather_patch = state_patch.get("weather")
        if weather_patch:
            sb["weather"].update(weather_patch)
            sb["weather"]["updated_at"] = _now()
            self.add_monitor_event(
                session_id, "simulator",
                f"Mock API更新: 天气 -> {weather_patch.get('condition', '变化')}",
                "weather_update"
            )

        booking_patch = state_patch.get("booking")
        if booking_patch and target_poi_id:
            current = sb["bookings"].get(target_poi_id, {})
            current.update(booking_patch)
            sb["bookings"][target_poi_id] = current
            self.add_monitor_event(
                session_id, "simulator",
                f"Mock API更新: {target_poi_id} 预约状态变化",
                "booking_update", target_poi_id
            )

        event_record = {
            "event_id": f"evt_sim_{uuid.uuid4().hex[:8]}",
            "type": event.get("event_type", "unknown"),
            "severity": event.get("severity", "medium"),
            "poi_id": target_poi_id,
            "message": event.get("message", ""),
            "occurred_at": _now(),
            "requires_user_confirmation": True,
            "source": "llm_simulator",
        }
        sb["events"].append(event_record)
        s["pending_exception"] = event_record
        s["pending_monitor_msg"] = event_record

        return event_record

    # Pending monitor message for chat

    def pop_pending_monitor_msg(self, session_id: str) -> Optional[dict]:
        s = self.get(session_id)
        if not s:
            return None
        msg = s.get("pending_monitor_msg")
        s["pending_monitor_msg"] = None
        return msg

    # Monitor state snapshot

    def get_monitor_state(self, session_id: str,
                          live_queues: dict = None,
                          live_weather: dict = None) -> dict:
        """
        Build monitor panel state.
        live_queues / live_weather come from Mock API (real-time);
        fall back to session sandbox if not provided.
        """
        s = self.get(session_id)
        if not s:
            return {}

        sb        = s["sandbox"]
        itinerary = s.get("itinerary", [])

        restaurant_queues = []
        for node in itinerary:
            if node.get("type") == "restaurant":
                poi_id     = node.get("poiId", "")
                # Prefer live data from Mock API
                queue_info = (live_queues or {}).get(poi_id) or sb["queues"].get(poi_id, {})
                history    = s.get("queue_history", {}).get(poi_id, [])
                restaurant_queues.append({
                    "poi_id":       poi_id,
                    "name":         node.get("name", ""),
                    "wait_min":     queue_info.get("estimated_wait_min", 0),
                    "queue_tables": queue_info.get("queue_tables", 0),
                    "status":       queue_info.get("status", "normal"),
                    "trend":        self.get_queue_trend(session_id, poi_id),
                    "history":      [r[1] for r in history[-6:]],
                })

        booking_nodes = []
        for node in itinerary:
            if node.get("type") == "activity":
                poi_id  = node.get("poiId", "")
                booking = sb["bookings"].get(poi_id)
                if booking:
                    booking_nodes.append({
                        "poi_id": poi_id,
                        "name":   node.get("name", ""),
                        **booking,
                    })

        weather = live_weather if live_weather else sb.get("weather", {})

        return {
            "phase":             s.get("phase", "gathering"),
            "weather":           weather,
            "restaurant_queues": restaurant_queues,
            "booking_nodes":     booking_nodes,
            "recent_events":     s.get("monitor_events", [])[-15:],
            "pending_event":     s.get("pending_exception"),
            "itinerary_count":   len(itinerary),
        }

    # Reset

    def reset(self, session_id: str):
        s = self.get(session_id)
        if s:
            s["memory"] = {"session_facts": {}, "confirmed_preferences": {}, "derived_preferences": {}}
            s["itinerary"] = []
            s["fulfillment"] = {}
            s["sandbox"] = copy.deepcopy(BASE_STATE)
            s["pending_exception"] = None
            s["pending_confirmations"] = {}
            s["resolved_confirmations"] = {}
            s["confirmation_history"] = []
            s["phase"] = "gathering"
            s["pending_inference"] = None
            s["queue_history"] = {}
            s["monitor_events"] = []
            s["booking_warned"] = []
            s["pending_monitor_msg"] = None
            s["clarify_history"] = []

    def reset_for_next_round(self, session_id: str):
        """Start a fresh planning round while keeping the same browser session."""
        s = self.get(session_id)
        if not s:
            return
        s["memory"] = {"session_facts": {}, "confirmed_preferences": {}, "derived_preferences": {}}
        s["itinerary"] = []
        s["fulfillment"] = {}
        s["sandbox"] = copy.deepcopy(BASE_STATE)
        s["pending_exception"] = None
        s["pending_confirmations"] = {}
        s["resolved_confirmations"] = {}
        s["confirmation_history"] = []
        s["phase"] = "gathering"
        s["pending_inference"] = None
        s["queue_history"] = {}
        s["monitor_events"] = []
        s["watch_ids"] = []
        s["booking_warned"] = []
        s["pending_monitor_msg"] = None
        s["clarify_history"] = []
        s.setdefault("phase_transition_log", []).append({
            "time": _now_hms(),
            "ok": True,
            "from": "completed",
            "requested": "gathering",
            "to": "gathering",
            "fallback": None,
            "reason": "next_round",
        })
        s["phase_transition_log"] = s["phase_transition_log"][-20:]
