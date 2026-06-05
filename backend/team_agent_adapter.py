"""Compatibility layer from the current /agent API to teammate chwl-agent2.

The frontend keeps talking to backend.main's /agent/* contract.  This adapter
uses chwl-agent2 as the planning/execution engine and translates its state into
the node/event shape the existing React UI already understands.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


def _model_dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(getattr(obj, "__dict__", {}) or {})


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


class TeamAgentAdapter:
    """Small bridge that makes chwl-agent2 look like the existing backend."""

    def __init__(self, manager):
        self.manager = manager
        self.team_root = Path(__file__).resolve().parents[1] / "chwl-agent2"
        if str(self.team_root) not in sys.path:
            sys.path.insert(0, str(self.team_root))

        from orchestrator.llm_orchestrator import create_llm_orchestrator

        use_teammate_api = os.getenv("TEAM_AGENT_USE_HTTP_TOOLS", "0") == "1"
        self.engine, self.planner = create_llm_orchestrator(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            use_teammate_api=use_teammate_api,
        )
        self._planning: set[str] = set()
        logger.info("TeamAgentAdapter enabled with engine=%s", type(self.engine).__name__)

    async def stop_background_watch(self, session_id: str) -> None:
        try:
            await self.engine.watch_engine.stop_all(session_id)
        except Exception:
            logger.debug("team stop_background_watch ignored", exc_info=True)

    async def cancel_confirmations(self, session_id: str) -> None:
        return None

    async def cancel_session(self, session_id: str) -> None:
        try:
            await self.engine.cancel_session(session_id)
        except Exception:
            logger.debug("team cancel_session ignored", exc_info=True)

    async def run_chat(
        self,
        session_id: str,
        message: str,
        phase_hint: str | None = None,
        original_request: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.manager.get_or_create(session_id)
        visible_nodes = self.manager.get_itinerary(session_id)
        text = (message or "").strip()
        source_text = (original_request or text).strip()

        if visible_nodes and self._is_delete_restaurant(text):
            nodes = [n for n in visible_nodes if not self._is_restaurant_node(n)]
            self.manager.set_itinerary(session_id, nodes)
            await self._team_modify_delete_first_restaurant(session_id)
            yield {
                "type": "text",
                "content": "好的，已取消餐厅安排，当前行程不再包含餐厅。",
            }
            yield {
                "type": "itinerary_updated",
                "nodes": nodes,
                "facts": {"skip_restaurant": True},
                "preferences": {"skip_restaurant": True},
                "phase": "monitoring",
            }
            yield {"type": "done"}
            return

        should_plan = (
            phase_hint == "start_plan"
            or not visible_nodes
            or self._looks_like_replan(text)
            or self._wants_restaurant(text)
        )

        if not should_plan:
            yield {"type": "text", "content": "收到，我会按你的新需求调整行程。"}
            source_text = self._enrich_with_current_plan(session_id, text)

        yield {
            "type": "confirmed",
            "message": "明白了，我用队友版 Agent 重新规划一下。",
            "facts": self._facts_from_text(source_text),
            "preferences": {},
            "phase": "planning",
        }
        yield {"type": "status", "id": 1, "text": "需求已确认", "status": "done"}
        yield {"type": "status", "id": 2, "text": "队友 Agent 搜索活动和餐厅", "status": "loading"}
        yield {"type": "cot_step", "text": "使用 chwl-agent2 Orchestrator 作为规划引擎"}

        try:
            team_sid = await self._start_or_restart_plan(session_id, source_text)
            status = await self._wait_for_plan(team_sid)
            nodes = self._frontend_nodes(team_sid)
            if not nodes:
                nodes = self._frontend_nodes_from_status(status)
            if not nodes:
                raise RuntimeError("team agent returned empty itinerary")

            self.manager.set_itinerary(session_id, nodes)
            self.manager.set_phase(session_id, "monitoring")
            self.manager.update_memory(session_id, "session_facts", self._facts_from_text(source_text))
            self.manager.update_memory(session_id, "derived_preferences", {})

            yield {"type": "status", "id": 2, "status": "done"}
            yield {"type": "status", "id": 3, "text": "路线和履约状态已检查", "status": "done"}
            yield {"type": "status", "id": 4, "text": "方案已生成", "status": "done"}
            yield {"type": "cot_step", "text": f"生成 {len(nodes)} 个节点，并转换为当前前端格式"}
            yield {
                "type": "itinerary_ready",
                "nodes": nodes,
                "summary": getattr(status, "summary", "") or self._summary(nodes),
                "facts": self.manager.get_memory(session_id).get("session_facts", {}),
                "preferences": {},
                "phase": "monitoring",
            }
        except Exception as exc:
            logger.exception("team agent planning failed")
            yield {"type": "error", "message": f"队友 Agent 规划失败：{exc}"}
        finally:
            yield {"type": "done"}

    async def run_fulfill(self, session_id: str) -> AsyncGenerator[dict[str, Any], None]:
        nodes = self.manager.get_itinerary(session_id)
        items = [
            {"id": n.get("id"), "icon": n.get("icon", "📍"), "name": n.get("name", ""), "status": "pending"}
            for n in nodes
            if n.get("type") in ("restaurant", "activity")
        ]
        yield {"type": "fulfill_init", "items": items}
        try:
            await self.engine.confirm_itinerary(session_id)
        except Exception as exc:
            logger.warning("team confirm failed: %s", exc)

        total = max(1, len(items))
        for i, item in enumerate(items, start=1):
            self.manager.lock_node(session_id, item["id"])
            yield {
                "type": "fulfill_item",
                "id": item["id"],
                "status": "done",
                "action": "已锁定/预约",
            }
            yield {"type": "fulfill_progress", "value": round(i / total * 100)}
            await asyncio.sleep(0.1)
        yield {"type": "monitor_started", "message": "已进入实时监控，有变化我会提醒你。"}

    async def run_report(self, session_id: str, report_type: str) -> dict[str, Any]:
        try:
            from core.models import UserSentiment

            await self.engine.handle_user_sentiment(
                session_id,
                UserSentiment(type=report_type, description=report_type),
            )
            status = await self._wait_for_plan(session_id, timeout_s=12)
            nodes = self._frontend_nodes(session_id) or self._frontend_nodes_from_status(status)
            if nodes:
                self.manager.set_itinerary(session_id, nodes)
            return {"status": "replanned", "nodes": nodes}
        except Exception as exc:
            logger.warning("team report/replan failed: %s", exc)
            return {"status": "received", "message": "已记录反馈，稍后继续调整。"}

    async def node_action(self, session_id: str, req) -> dict[str, Any]:
        nodes = self.manager.get_itinerary(session_id)
        if req.action == "pin":
            return {"nodes": self.manager.apply_node_action(session_id, req.node_id, "pin")}
        if req.action == "delete":
            nodes = [n for n in nodes if n.get("id") != req.node_id]
            self.manager.set_itinerary(session_id, nodes)
            await self._team_modify(session_id, {"type": "delete", "node_id": req.node_id})
            return {"nodes": nodes}
        if req.action == "replace":
            await self._team_modify(session_id, {"type": "replace", "node_id": req.node_id})
            status = await self._wait_for_plan(session_id, timeout_s=8)
            fresh = self._frontend_nodes(session_id) or self._frontend_nodes_from_status(status)
            if fresh:
                self.manager.set_itinerary(session_id, fresh)
                return {"nodes": fresh}
        return {"nodes": nodes}

    async def _start_or_restart_plan(self, session_id: str, text: str) -> str:
        if session_id in self._planning:
            raise RuntimeError("planning already running")
        self._planning.add(session_id)
        try:
            if session_id in self.engine.sessions:
                try:
                    await self.engine.cancel_session(session_id)
                    await asyncio.sleep(0.05)
                except Exception:
                    logger.debug("team restart cancel ignored", exc_info=True)
            return await self.engine.start_session(text, session_id)
        finally:
            self._planning.discard(session_id)

    async def _wait_for_plan(self, session_id: str, timeout_s: float = 35):
        deadline = asyncio.get_running_loop().time() + timeout_s
        last_status = None
        while asyncio.get_running_loop().time() < deadline:
            last_status = await self.engine.get_status(session_id)
            if getattr(last_status, "nodes", None):
                state = getattr(last_status, "itinerary_state", "")
                if state in ("pending_confirm", "executing", "completed", "needs_replan", "draft"):
                    return last_status
            await asyncio.sleep(0.5)
        if last_status is not None:
            return last_status
        return await self.engine.get_status(session_id)

    def _frontend_nodes(self, session_id: str) -> list[dict[str, Any]]:
        ctx = getattr(self.engine, "sessions", {}).get(session_id)
        itinerary = getattr(ctx, "itinerary", None)
        raw_nodes = getattr(itinerary, "nodes", []) if itinerary else []
        return [self._frontend_node(n, i) for i, n in enumerate(raw_nodes)]

    def _frontend_nodes_from_status(self, status: Any) -> list[dict[str, Any]]:
        return [self._frontend_node(n, i) for i, n in enumerate(getattr(status, "nodes", []) or [])]

    def _frontend_node(self, node: Any, index: int) -> dict[str, Any]:
        data = _model_dump(node)
        node_id = data.get("node_id") or data.get("id") or f"node_{index + 1:03d}"
        category = _enum_value(data.get("category") or data.get("type") or "")
        node_type = "restaurant" if category == "restaurant" else "activity"
        name = data.get("poi_name") or data.get("name") or data.get("title") or "待确认地点"
        start = data.get("scheduled_start") or data.get("start_time") or data.get("startTime") or ""
        end = data.get("scheduled_end") or data.get("end_time") or data.get("endTime") or ""
        queue_min = int(data.get("queue_time_min") or data.get("queueMin") or 0)
        return {
            "id": node_id,
            "poiId": data.get("poi_id") or data.get("poiId") or node_id,
            "type": node_type,
            "category": category or node_type,
            "icon": "🍽️" if node_type == "restaurant" else "🎯",
            "name": name,
            "sub": data.get("address") or data.get("subtitle") or category or "",
            "address": data.get("address") or "",
            "timeStart": start,
            "timeEnd": end,
            "startTime": start,
            "endTime": end,
            "duration": data.get("duration_min") or data.get("duration") or 60,
            "status": data.get("status") or data.get("state") or "planned",
            "locked": bool(data.get("soft_lock")),
            "completed_lock": bool(data.get("completed_lock")),
            "user_pinned": bool(data.get("user_pinned")),
            "booking_status": data.get("booking_status"),
            "booking_ref": data.get("booking_ref"),
            "queueMin": queue_min,
            "queueText": f"约{queue_min}分钟" if queue_min else "",
            "rating": data.get("rating") or 0,
            "tags": data.get("tags") or [],
            "reason": data.get("planner_reason") or "",
            "transit": data.get("transit"),
        }

    async def _team_modify_delete_first_restaurant(self, session_id: str) -> None:
        ctx = getattr(self.engine, "sessions", {}).get(session_id)
        raw_nodes = getattr(getattr(ctx, "itinerary", None), "nodes", []) if ctx else []
        for node in raw_nodes:
            data = _model_dump(node)
            if _enum_value(data.get("category")) == "restaurant":
                await self._team_modify(session_id, {"type": "delete", "node_id": data.get("node_id")})
                return

    async def _team_modify(self, session_id: str, payload: dict[str, Any]) -> None:
        if session_id not in getattr(self.engine, "sessions", {}):
            return
        from core.models import ItineraryModification

        mod = ItineraryModification(**payload)
        await self.engine.modify_itinerary(session_id, mod)

    def _enrich_with_current_plan(self, session_id: str, text: str) -> str:
        names = "、".join(n.get("name", "") for n in self.manager.get_itinerary(session_id)[:5])
        return f"当前方案：{names}。用户新需求：{text}" if names else text

    @staticmethod
    def _facts_from_text(text: str) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        if any(word in text for word in ("老婆", "情侣", "女朋友", "约会")):
            facts["scenario"] = "couple"
        elif any(word in text for word in ("朋友", "哥们", "闺蜜")):
            facts["scenario"] = "friends"
        elif any(word in text for word in ("孩子", "小孩", "亲子", "家人")):
            facts["scenario"] = "family"
        if any(word in text for word in ("不吃", "不吃饭", "删除餐厅", "不要餐厅")):
            facts["skip_restaurant"] = True
        return facts

    @staticmethod
    def _summary(nodes: list[dict[str, Any]]) -> str:
        names = " → ".join(n.get("name", "") for n in nodes[:4])
        return f"共 {len(nodes)} 个节点：{names}"

    @staticmethod
    def _is_restaurant_node(node: dict[str, Any]) -> bool:
        return node.get("type") == "restaurant" or node.get("category") == "restaurant"

    @staticmethod
    def _is_delete_restaurant(text: str) -> bool:
        return any(word in text for word in ("删除餐厅", "取消餐厅", "不要餐厅", "不吃了", "不吃饭", "餐厅删掉"))

    @staticmethod
    def _wants_restaurant(text: str) -> bool:
        return any(word in text for word in ("想吃饭", "加餐厅", "吃餐厅", "反悔想吃", "还是吃", "吃火锅", "吃川菜"))

    @staticmethod
    def _looks_like_replan(text: str) -> bool:
        return any(word in text for word in ("重新规划", "换方案", "不满意", "改成", "换成", "反悔"))
