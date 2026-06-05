"""Main Agent orchestration. Each flow is an async generator yielding SSE events."""
import asyncio
import json
import logging
import random
import string
import time
import uuid
from typing import AsyncGenerator

from . import skills, tools
from .session import SessionManager

logger = logging.getLogger(__name__)


def _emit(type_: str, **data) -> dict:
    return {"type": type_, **data}


def _voucher_code() -> str:
    return "MT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


class Orchestrator:
    def __init__(self, manager: SessionManager):
        self.manager = manager

    # ── Flow 0: Smart Chat (phase-aware entry point) ─────────────────

    async def run_chat(self, session_id: str, message: str,
                       phase_hint: str = None,
                       original_request: str = None) -> AsyncGenerator[dict, None]:
        """
        Phase-aware chat handler.

        phase_hint="start_plan" means the user explicitly clicked "开始规划".
        When session is alive and phase=confirming → normal confirm+plan.
        When session was reset (phase=gathering) but hint says start_plan →
          use original_request to re-clarify+plan in one shot without another user round.
        """
        phase = self.manager.get_phase(session_id)
        logger.info(f"run_chat phase={phase} hint={phase_hint} session={session_id[:8]}")

        # ── "开始规划" button was clicked ──────────────────────────────
        if phase_hint == "start_plan":
            if phase == "confirming":
                # Normal path: session intact, go to confirm+plan
                async for evt in self._run_confirm_and_plan(session_id, message):
                    yield evt
            else:
                # Session was reset (e.g. dev reload) — re-run clarify with the
                # original request so we immediately have context, then plan.
                src = original_request or message
                if not original_request:
                    # We have no context; prompt user to re-enter
                    yield _emit("clarify",
                                message="抱歉，刚刚服务重启了，麻烦重新告诉我你的出行需求～"
                                        "\n比如：「今天下午2点，带孩子去玩」",
                                ready_to_plan=False,
                                phase="confirming")
                    yield _emit("done")
                    return
                # Re-clarify using original request, then immediately plan
                async for evt in self._run_clarify_and_plan(session_id, src):
                    yield evt
            return

        # ── Normal routing by phase ────────────────────────────────────
        if phase == "gathering":
            async for evt in self._run_clarify(session_id, message):
                yield evt
        elif phase == "confirming":
            # User typed a refinement — update inference, re-show clarify + button
            # (Planning only starts when user explicitly clicks "开始规划")
            async for evt in self._run_refine_clarify(session_id, message):
                yield evt
        else:
            async for evt in self._run_monitoring_chat(session_id, message):
                yield evt

    # ── Fast-path: clarify + plan in one shot (session recovery) ─────

    async def _run_clarify_and_plan(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """
        Used when session was reset but user clicked "开始规划".
        We silently clarify and immediately proceed to planning — no extra user round.
        """
        current_time = time.strftime("%H:%M")
        result = await skills.clarify_needs(message, current_time)
        inferred = result.get("inferred", {})

        # Confirm prefs using a silent "yes" response
        confirmed = await skills.confirm_preferences(inferred, "好的，就按这个规划")
        session_facts = confirmed.get("session_facts", {})
        preferences   = confirmed.get("preferences", {})

        self.manager.update_memory(session_id, "session_facts", session_facts)
        self.manager.update_memory(session_id, "derived_preferences", preferences)
        self.manager.set_phase(session_id, "planning")
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"服务重启恢复：重新识别请求，场景={session_facts.get('scenario')}，直接规划",
            "session_recovery"
        )

        yield _emit("confirmed", message=confirmed.get("start_message", "好的，马上帮你规划！"))
        async for evt in self._run_plan_core(session_id, session_facts, preferences):
            yield evt

    # ── Phase: gathering ─────────────────────────────────────────────

    async def _run_clarify(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """Infer user needs and ask for confirmation."""
        current_time = time.strftime("%H:%M")
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"收到用户请求，正在推测出行信息...", "info"
        )

        loc = await tools.get_user_location()
        location_hint = ""
        if loc.get("address"):
            location_hint = (
                f"系统检测到用户当前位置：{loc['address']}（{loc.get('district', '')}），"
                f"请在 confirm_message 中核实出发地是否正确"
            )

        result = await skills.clarify_needs(message, current_time, location_hint=location_hint)
        inferred = result.get("inferred", {})
        if loc.get("address"):
            inferred["detected_location"] = loc["address"]

        self.manager.set_pending_inference(session_id, inferred)
        self.manager.set_phase(session_id, "confirming")

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"推测场景: {inferred.get('scenario','?')} | 出发: {inferred.get('start_time','?')} | 置信度: {result.get('confidence','?')}",
            "info"
        )

        confidence = result.get("confidence", "medium")
        missing    = result.get("missing_fields", [])
        all_clear  = (
            confidence in ("high", "very_high")
            or (isinstance(confidence, (int, float)) and confidence >= 0.8)
        ) and len(missing) == 0

        base_msg = result.get("confirm_message", "请告诉我更多出行信息～")
        if all_clear:
            msg = base_msg + "\n\n信息都齐了！直接点「开始规划」，我马上帮你安排 👇"
        else:
            msg = base_msg + "\n\n有需要补充或纠正的话直接告诉我，确认好了再点「开始规划」。"

        yield _emit("clarify",
                    message=msg,
                    ready_to_plan=True,
                    phase="confirming",
                    facts=inferred,
                    preferences={
                        "food": inferred.get("food_preferences", []),
                        "venue": inferred.get("venue_preference"),
                    })
        yield _emit("done")

    # ── Phase: confirming (refinement) ──────────────────────────────
    async def _run_refine_clarify(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """User sent a refinement message while in confirming phase.
        Update the pending inference and re-emit clarify (button stays visible).
        """
        inferred = self.manager.get_pending_inference(session_id) or {}
        confirmed = await skills.confirm_preferences(inferred, message)
        updated_facts = confirmed.get("session_facts", {})
        updated_preferences = confirmed.get("preferences", {})

        # Persist the updated inference so "开始规划" uses fresh data
        if updated_facts:
            self.manager.set_pending_inference(session_id, updated_facts)
            self.manager.update_memory(session_id, "session_facts", updated_facts)
        if updated_preferences:
            self.manager.update_memory(session_id, "derived_preferences", updated_preferences)

        # Build a short acknowledgement + prompt to start planning
        ack = confirmed.get("start_message", "好的，已更新！")
        yield _emit("clarify",
                    message=f"{ack}\n\n信息确认好了吗？点击下方「开始规划」，我马上帮你安排 👇",
                    ready_to_plan=True,
                    phase="confirming",
                    facts=updated_facts or inferred,
                    preferences=updated_preferences)
        yield _emit("done")

    # ── Phase: confirming (execute plan) ────────────────────────────

    async def _run_confirm_and_plan(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """User clicked '开始规划' — use the already-refined pending inference to plan."""
        inferred = self.manager.get_pending_inference(session_id) or {}

        # pending_inference is a session_facts dict (already updated by _run_refine_clarify)
        # Wrap it back into confirm_preferences format to derive preferences
        confirmed = await skills.confirm_preferences(inferred, "好的，就按这个来")
        session_facts = confirmed.get("session_facts", {}) or inferred
        preferences   = confirmed.get("preferences", {})

        self.manager.update_memory(session_id, "session_facts", session_facts)
        self.manager.update_memory(session_id, "derived_preferences", preferences)
        self.manager.set_pending_inference(session_id, None)
        self.manager.set_phase(session_id, "planning")

        start_msg = confirmed.get("start_message", "明白了，马上帮你规划！")

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"偏好已确认 → 开始规划 | 场景:{session_facts.get('scenario')} 出发:{session_facts.get('start_time')}",
            "info"
        )

        yield _emit("confirmed", message=start_msg,
                    facts=session_facts, preferences=preferences, phase="planning")

        # Now run the full plan using confirmed prefs
        async for evt in self._run_plan_core(session_id, session_facts, preferences):
            yield evt

    # ── Phase: monitoring (adjustment) ───────────────────────────────

    async def _run_monitoring_chat(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """Handle user adjustments during monitoring phase."""
        # For now: re-run planning with updated request
        memory = self.manager.get_memory(session_id)
        session_facts = memory.get("session_facts", {})
        preferences   = memory.get("derived_preferences", {})

        session_facts, preferences = skills.merge_specific_preferences(
            session_facts, preferences, message
        )
        self.manager.update_memory(session_id, "session_facts", session_facts)
        self.manager.update_memory(session_id, "derived_preferences", preferences)
        yield _emit("profile_updated", facts=session_facts,
                    preferences=preferences, phase="monitoring")

        # Check if user wants to adjust something specific
        if any(w in message for w in ['重新规划', '换方案', '重规划']):
            yield _emit("text", content="好的，重新为你规划一个方案！")
            async for evt in self._run_plan_core(session_id, session_facts, preferences):
                yield evt
        else:
            # General response - just relay the message context
            yield _emit("text", content=f"已收到你的调整需求，正在为你处理：{message[:30]}...")
            async for evt in self._run_plan_core(session_id, session_facts, preferences):
                yield evt

    # ── Core: Plan ───────────────────────────────────────────────────

    async def _run_plan_core(self, session_id: str, session_facts: dict,
                             preferences: dict,
                             _retry: int = 0) -> AsyncGenerator[dict, None]:
        """
        Core planning: Agent autonomously calls Mock API tools to gather
        real-time data, scores candidates, and generates the optimal itinerary.
        The LLM decides which tools to call and in what order (Function Calling).
        """
        scenario = session_facts.get("scenario", "family")

        # ── Preserve user-pinned nodes across replanning ──────────────
        existing = self.manager.get_itinerary(session_id)
        pinned_nodes = [
            n for n in existing
            if n.get("user_pinned") or n.get("pinned")
        ]
        # Pass pinned slot info to planner so LLM avoids those time ranges
        sf_with_pinned = {**session_facts, "_pinned_nodes": pinned_nodes}

        yield _emit("status", id=1, text="正在解析您的需求", status="done")
        yield _emit("status", id=2, text="Agent 工具调用：获取实时数据…", status="loading")
        yield _emit("status", id=3, text="Agent 评估候选：评分与筛选…", status="loading")
        yield _emit("status", id=4, text="AI 综合推理：编排最优时间线…", status="loading")

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"启动 Agent 规划循环 scenario={scenario}，LLM 自主调用工具", "agent_start"
        )

        # ── Agent Tool-Use Loop ──────────────────────────────────────
        plan = None
        tool_call_count = 0

        async for item in skills.run_agent_plan(sf_with_pinned, preferences):
            t = item.get("_type", "")

            if t == "cot_step":
                text = item["text"]
                yield _emit("cot_step", text=text)
                # Count tool calls for monitor log
                if text.startswith("🔧"):
                    tool_call_count += 1
                await asyncio.sleep(0.05)

            elif t == "tool_error_limit":
                if _retry < 1:
                    yield _emit("cot_step", text="🔄 工具连续失败，自动重新规划中…")
                    async for evt in self._run_plan_core(
                        session_id, session_facts, preferences, _retry=1
                    ):
                        yield evt
                else:
                    yield _emit("error", message="工具调用持续失败，请检查 Mock API 是否正常运行后重试")
                    yield _emit("done")
                return

            elif t == "result":
                plan = item["plan"]
                yield _emit("status", id=2, status="done")
                yield _emit("status", id=3, status="done")
                yield _emit("status", id=4, status="done")

        if not plan:
            yield _emit("error", message="Agent 未能生成行程，请重试")
            yield _emit("done")
            return

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"Agent 规划完成：{tool_call_count} 次工具调用，"
            f"{len(plan.get('nodes', []))} 个节点",
            "agent_done"
        )

        nodes = plan.get("nodes", [])
        if not nodes:
            yield _emit("error", message="行程生成失败：没有找到合适的节点，请重新发送出行需求后重试。")
            yield _emit("done")
            return

        # ── Consecutive restaurant check ─────────────────────────────
        def _is_restaurant(node: dict) -> bool:
            return (node.get("type") == "restaurant"
                    or node.get("category") == "restaurant")

        consecutive_rest = any(
            _is_restaurant(nodes[i]) and _is_restaurant(nodes[i + 1])
            for i in range(len(nodes) - 1)
        )
        if consecutive_rest:
            yield _emit("cot_step", text="⚠️ 检测到连续两个餐厅节点，已记录警告（LLM 规划应避免此情况）")

        # ── Feasibility check (from teammate's validation logic) ──────
        risks = skills.validate_feasibility(nodes, session_facts)
        if risks:
            for r in risks:
                yield _emit("cot_step", text=f"⚠️ 合理性警告：{r}")
            self.manager.add_monitor_event(
                session_id, "main_agent",
                f"合理性检查：{'; '.join(risks[:2])}", "feasibility_warning"
            )
        else:
            yield _emit("cot_step", text="✅ 合理性自检通过：通勤比例/时间无重叠/结束时间均合格")

        # Add transit to first node (from home/departure point to first POI)
        if nodes:
            # Parse distance string like "2.8公里" → float
            try:
                raw_dist = nodes[0].get("distance", "")
                first_dist = float(raw_dist.replace("公里", "").strip()) if raw_dist else 3.0
            except Exception:
                first_dist = 3.0
            nodes[0]["transit"] = {
                "from_poi_id": "home",
                "to_poi_id":   nodes[0].get("poiId", ""),
                "mode":        "taxi",
                "duration_min": 20,          # fixed 20-min commute matching time offset
                "distance_km":  first_dist,
            }

        # Fetch route estimates between consecutive nodes from Mock API
        for i in range(1, len(nodes)):
            prev_poi = nodes[i - 1].get("poiId", "")
            curr_poi = nodes[i].get("poiId", "")
            if prev_poi and curr_poi and prev_poi != "walk_001" and curr_poi != "walk_001":
                try:
                    route = await tools.get_route(prev_poi, curr_poi, "taxi")
                    nodes[i]["transit"] = {
                        "from_poi_id": prev_poi,
                        "to_poi_id":   curr_poi,
                        "mode":        "taxi",
                        "duration_min": route.get("duration_min", 12),
                        "distance_km":  route.get("distance_km", 2.5),
                    }
                except Exception:
                    nodes[i]["transit"] = {
                        "from_poi_id": prev_poi, "to_poi_id": curr_poi,
                        "mode": "taxi", "duration_min": 12, "distance_km": 2.5,
                    }

        # ── Merge pinned nodes back, drop time-overlapping new nodes ────
        if pinned_nodes:
            def _to_min(t: str) -> int:
                try:
                    h, m = map(int, t.split(":"))
                    return h * 60 + m
                except Exception:
                    return -1

            def _overlaps(ns: str, ne: str, ps: str, pe: str) -> bool:
                ns_m, ne_m = _to_min(ns), _to_min(ne)
                ps_m, pe_m = _to_min(ps), _to_min(pe)
                if -1 in (ns_m, ne_m, ps_m, pe_m):
                    return False
                return ns_m < pe_m and ne_m > ps_m

            pinned_ids = {p["id"] for p in pinned_nodes}

            def _conflicts_with_pinned(node: dict) -> bool:
                if node["id"] in pinned_ids:
                    return True
                ns, ne = node.get("timeStart", ""), node.get("timeEnd", "")
                for p in pinned_nodes:
                    if _overlaps(ns, ne, p.get("timeStart", ""), p.get("timeEnd", "")):
                        return True
                return False

            new_nodes = [n for n in nodes if not _conflicts_with_pinned(n)]
            nodes = sorted(
                pinned_nodes + new_nodes,
                key=lambda n: _to_min(n.get("timeStart", "99:99")),
            )

        self.manager.set_itinerary(session_id, nodes)
        self.manager.set_phase(session_id, "monitoring")

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"规划完成，共 {len(nodes)} 个节点，进入监控模式",
            "planning_done"
        )
        for node in nodes:
            if node.get("booking_urgent"):
                self.manager.add_monitor_event(
                    session_id, "main_agent",
                    f"需提前预约: {node.get('name')}",
                    "booking_urgent", node.get("poiId")
                )

        yield _emit("itinerary_ready",
                    nodes=nodes,
                    summary=plan.get("summary", ""),
                    session_id=session_id,
                    facts=session_facts,
                    preferences=preferences,
                    phase="monitoring")
        yield _emit("done")

    # ── Flow 1: Planning (legacy, still used by /plan endpoint) ──────

    async def run_plan(self, session_id: str, data: dict) -> AsyncGenerator[dict, None]:
        """Legacy planning flow - kept for backward compatibility."""
        scenario = data.get("scenario", "family")
        mode     = data.get("mode", "full")
        message  = data.get("message", "")
        tags     = data.get("tags", [])

        yield _emit("status", id=1, text="正在解析您的需求", status="loading")
        await asyncio.sleep(0.1)

        # Check if memory already has confirmed prefs (from chat flow)
        memory = self.manager.get_memory(session_id)
        if memory.get("session_facts") and memory["session_facts"].get("scenario"):
            session_facts = memory["session_facts"]
            preferences   = memory.get("derived_preferences", {})
        else:
            prefs = await skills.extract_preferences(message, tags, scenario)
            session_facts = prefs.get("session_facts", {})
            preferences   = prefs.get("preferences", {})
            self.manager.update_memory(session_id, "session_facts", session_facts)
            self.manager.update_memory(session_id, "derived_preferences", preferences)

        yield _emit("status", id=1, status="done")

        async for evt in self._run_plan_core(session_id, session_facts, preferences):
            yield evt

    # ── Flow 2: Fulfillment ───────────────────────────────────────────

    async def run_fulfill(self, session_id: str) -> AsyncGenerator[dict, None]:
        """Booking flow: emit progress per node. Does NOT auto-trigger exceptions."""
        itinerary = self.manager.get_itinerary(session_id)
        memory = self.manager.get_memory(session_id)

        if not itinerary:
            yield _emit("error", message="行程为空，请先规划")
            yield _emit("done")
            return

        items = [
            *[{"id": n["id"], "icon": n["icon"], "name": n["name"],
               "action": _action_label(n), "status": "loading"}
              for n in itinerary],
            {"id": "route", "icon": "🗺️", "name": "全程路线",
             "action": "生成中...", "status": "pending"},
        ]
        yield _emit("fulfill_init", items=items)

        # Process urgent bookings first
        for node in itinerary:
            if node.get("booking_urgent") and node.get("poiId") not in self.manager.get(session_id).get("booking_warned", []):
                yield _emit("booking_reminder",
                            poi_id=node.get("poiId"),
                            name=node.get("name"),
                            message=f"📅 {node.get('name')}需要提前预约，建议现在操作，以免名额被抢！")
                s = self.manager.get(session_id)
                if s:
                    s.setdefault("booking_warned", []).append(node.get("poiId"))

        for i, node in enumerate(itinerary):
            await asyncio.sleep(0.9 + i * 0.15)
            done_text = _done_label(node)
            voucher = _voucher_code() if node["type"] != "light" else None
            self.manager.lock_node(session_id, node["id"])
            yield _emit("fulfill_item",
                        id=node["id"],
                        status="done",
                        action=done_text,
                        voucher=voucher)

            # Popup only for nodes that actually need user booking action
            needs_redirect = (
                node["type"] == "restaurant"
                or node.get("booking_required")
                or node.get("booking_urgent")
            )
            if needs_redirect:
                site = "美团·排号" if node["type"] == "restaurant" else "美团·预约"
                yield _emit("booking_redirect",
                            name=node.get("name", ""),
                            node_id=node["id"],
                            site=site,
                            mock_url=f"https://i.meituan.com/mock/{node.get('poiId','')}")
                await asyncio.sleep(0.3)

        await asyncio.sleep(0.7)
        yield _emit("fulfill_item", id="route", status="done", action="路线已生成")
        yield _emit("fulfill_progress", value=100)

        # Start monitoring mode
        self.manager.set_phase(session_id, "monitoring")
        self.manager.add_monitor_event(
            session_id, "main_agent",
            "履约完成，开始后台监控排队和天气...", "monitoring_start"
        )

        # Check initial queue status for restaurants
        await asyncio.sleep(0.5)
        await self._check_queue_and_emit(session_id, itinerary)

        yield _emit("monitor_started",
                    message="✅ 行程已全部安排好！\n\n我会持续监控餐厅排队和天气情况，有变化会立刻通知你。")
        yield _emit("done")

    async def _check_queue_and_emit(self, session_id: str, itinerary: list):
        """Check queue status for restaurants and emit advice if needed."""
        current_time = time.strftime("%H:%M")
        for node in itinerary:
            if node.get("type") == "restaurant":
                poi_id = node.get("poiId", "")
                queue_info = self.manager.get_queue(session_id, poi_id)
                wait = queue_info.get("estimated_wait_min", 0)
                if wait > 0:
                    self.manager.update_queue_history(session_id, poi_id, wait)
                    trend = self.manager.get_queue_trend(session_id, poi_id)
                    self.manager.add_monitor_event(
                        session_id, "main_agent",
                        f"初始排队检测: {node.get('name')} → {wait}分钟 ({trend})",
                        "queue_check", poi_id
                    )

    # ── Flow 3: Exception Confirm ─────────────────────────────────────

    async def run_exception_confirm(self, session_id: str, data: dict) -> AsyncGenerator[dict, None]:
        exception_type = data.get("exception_type", "queue_spike")
        recommended    = data.get("recommended", {})
        original_node_id = data.get("original_node_id")

        itinerary = self.manager.get_itinerary(session_id)

        def _is_protected(n: dict) -> bool:
            return n.get("completed_lock") or n.get("user_pinned") or n.get("pinned") or n.get("locked")

        target_node = next(
            (n for n in itinerary if not _is_protected(n)), None
        )
        if original_node_id:
            specific = next((n for n in itinerary if n["id"] == original_node_id), None)
            # Only use the specific node if it is not protected
            if specific and not specific.get("completed_lock") and not specific.get("user_pinned"):
                target_node = specific

        if target_node and recommended:
            updates = {
                "name": recommended.get("name", target_node["name"]),
                "sub": recommended.get("sub", target_node.get("sub", "")),
                "icon": recommended.get("icon", target_node["icon"]),
                "queueText": recommended.get("queueText", ""),
                "distance": recommended.get("distance", ""),
                "tags": recommended.get("tags", []),
                "reason": recommended.get("reason", ""),
                "poiId": recommended.get("poi_id", target_node["poiId"]),
            }
            self.manager.update_node(session_id, target_node["id"], updates)
        elif target_node and not recommended:
            # No specific alternative — call Mock API for alternatives then replan
            memory   = self.manager.get_memory(session_id)
            scenario = memory.get("session_facts", {}).get("scenario", "family")
            try:
                alt_data   = await tools.get_alternatives(
                    scenario, exception_type, target_node.get("poiId")
                )
                event_data = {"type": exception_type, "message": "用户确认需要换方案"}
                replan     = await skills.replan_partial(event_data, itinerary, alt_data, memory)
                rec        = replan.get("recommended", {})
                if rec and rec.get("name"):
                    self.manager.update_node(session_id, target_node["id"], {
                        "name":      rec.get("name", target_node["name"]),
                        "sub":       rec.get("sub", target_node.get("sub", "")),
                        "icon":      rec.get("icon", target_node["icon"]),
                        "queueText": rec.get("queueText", ""),
                        "distance":  rec.get("distance", ""),
                        "tags":      rec.get("tags", []),
                        "reason":    rec.get("reason", ""),
                        "poiId":     rec.get("poi_id", target_node.get("poiId", "")),
                    })
                    self.manager.add_monitor_event(
                        session_id, "main_agent",
                        f"调用 Mock API 获取备选，切换至: {rec.get('name')}", "replan_from_api"
                    )
            except Exception as e:
                logger.warning(f"exception_confirm replan from Mock API failed: {e}")

        # Always emit itinerary_updated so frontend spinner clears
        yield _emit("itinerary_updated", nodes=self.manager.get_itinerary(session_id))
        await asyncio.sleep(0.3)

        self.manager.clear_exception(session_id)
        self.manager.add_monitor_event(
            session_id, "main_agent",
            "用户确认切换方案，行程已更新", "replan_confirmed"
        )
        yield _emit("replan_done",
                    message="已切换方案，履约完成",
                    nodes=self.manager.get_itinerary(session_id))
        yield _emit("done")

    # ── Flow 4: User Report ──────────────────────────────────────────

    async def run_report(self, session_id: str, report_type: str) -> dict:
        itinerary = self.manager.get_itinerary(session_id)

        if report_type == "child_tired":
            # Remove light-activity nodes, but preserve completed or user-pinned ones
            itinerary = [
                n for n in itinerary
                if n.get("type") != "light"
                or n.get("completed_lock")
                or n.get("user_pinned")
                or n.get("pinned")
            ]
            msg = "已删除轻活动节点，行程已缩短"
        elif report_type == "queue_too_long":
            msg = "建议换一个方案，请在行程卡片中点击「换一个」"
        elif report_type == "weather":
            # Update tags for non-completed nodes only
            itinerary = [
                {**n, "tags": ["室内"] + [t for t in n.get("tags", []) if t != "户外"]}
                if not n.get("completed_lock")
                else n
                for n in itinerary
            ]
            msg = "已标记天气风险，建议选室内备选"
        else:
            msg = "已记录问题"

        self.manager.set_itinerary(session_id, itinerary)
        return {"nodes": itinerary, "message": msg}

    # ── Flow 5: Simulator Advance ────────────────────────────────────

    async def run_simulator_advance(self, session_id: str) -> dict:
        """
        Simulator Agent: generate an LLM event and write it to Mock API.
        The User Agent reads queue/weather from Mock API, so this change
        is immediately visible to the User Agent on the next plan/poll.
        """
        s = self.manager.get(session_id)
        if not s:
            return {"error": "session_not_found"}

        itinerary = s.get("itinerary", [])
        memory    = self.manager.get_memory(session_id)
        scenario  = memory.get("session_facts", {}).get("scenario", "family")
        cur_time  = time.strftime("%H:%M")

        # Read live state from Mock API for context
        weather, events = await asyncio.gather(
            tools.get_weather(),
            tools.poll_events(),
        )

        context = {
            "scenario": scenario,
            "current_time": cur_time,
            "itinerary": itinerary,
            "weather": weather,
            "queues": {},   # LLM generates queue changes; Mock API is the truth
            "bookings": {},
            "scenario_script": {},
            "recent_events": s.get("monitor_events", [])[-10:],
        }

        self.manager.add_monitor_event(
            session_id, "simulator",
            f"模拟器Agent: 读取 Mock API 状态，天气 {weather.get('condition','?')}，"
            f"分析场景 [{scenario}]，生成事件...",
            "simulator_thinking"
        )

        event = await skills.generate_simulator_event(context)

        # Write event to Mock API (shared state — User Agent will see this)
        api_result = await tools.apply_llm_event(event)

        agent_dialogue = event.get("agent_dialogue", event.get("message", "触发了环境事件"))
        self.manager.add_monitor_event(
            session_id, "simulator",
            f"{agent_dialogue}（已写入 Mock API）",
            event.get("event_type", "info"),
            event.get("target_poi_id")
        )
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"检测到 Mock API 事件 [{event.get('event_type')}]，准备通知用户...",
            "event_detected"
        )

        # Set pending chat notification for User Agent's polling loop
        event_record = api_result.get("event", event)
        s["pending_monitor_msg"] = {
            "type": event.get("event_type", "custom"),
            "message": event.get("message", ""),
            "severity": event.get("severity", "medium"),
            "poi_id": event.get("target_poi_id"),
        }

        return {
            "event": event,
            "event_record": event_record,
            "agent_dialogue": agent_dialogue,
        }

    # ── Flow 6: Node Checkin ────────────────────────────────────────

    async def run_node_checkin(self, session_id: str, node_id: str) -> dict:
        """User marks a node as visited. Agent acknowledges and hints at next step."""
        itinerary = self.manager.get_itinerary(session_id)
        node = next((n for n in itinerary if n["id"] == node_id), None)
        if not node:
            return {"error": "node_not_found"}

        updated = self.manager.complete_node(session_id, node_id)
        done_count = len([n for n in updated if n.get("_checked")])
        total = len(updated)

        next_node = next((n for n in updated if not n.get("_checked")), None)
        node_name = node.get("name", "节点")

        if next_node:
            msg = (
                f"✓ {node_name}打卡完成！({done_count}/{total})\n\n"
                f"下一站：{next_node.get('icon','')} {next_node['name']}，"
                f"计划 {next_node.get('timeStart','')} 出发。"
            )
            q = next_node.get("queueMin", 0)
            if q and q > 20:
                msg += f"\n💡 {next_node['name']}目前排队约{q}分钟，建议适当提前出发。"
        else:
            msg = f"🎉 {node_name}完成！今日所有行程已全部打卡，希望你们玩得开心～"

        s = self.manager.get(session_id)
        if s:
            s["pending_monitor_msg"] = {
                "type": "node_checkin",
                "message": msg,
                "severity": "low",
            }

        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"用户打卡 {node_name} ({done_count}/{total})",
            "node_checkin", node_id,
        )

        return {"nodes": updated, "next_node": next_node,
                "message": msg, "done_count": done_count, "total": total}

    # ── Flow 7: Simulator Inject (natural language → event) ─────────

    async def run_simulator_inject(self, session_id: str, text: str) -> dict:
        """
        Simulator Agent: parse natural language → structured event → write to Mock API.
        The User Agent reads data from Mock API, so this change propagates automatically.
        """
        s = self.manager.get(session_id)
        if not s:
            return {"error": "session_not_found"}

        itinerary = s.get("itinerary", [])
        memory    = self.manager.get_memory(session_id)

        # Fetch live context from Mock API
        weather = await tools.get_weather()
        context = {
            "scenario": memory.get("session_facts", {}).get("scenario", "family"),
            "itinerary": itinerary,
            "weather": weather,
            "queues": {},
        }

        self.manager.add_monitor_event(
            session_id, "simulator",
            f"收到自然语言事件: 「{text[:30]}」，解析中...",
            "inject_received"
        )

        event = await skills.interpret_simulator_event(text, context)

        # Write to Mock API (User Agent reads from here)
        api_result = await tools.apply_llm_event(event)
        event_record = api_result.get("event", event)

        agent_dialogue = event.get("agent_dialogue", f"已注入: {text[:30]}")
        self.manager.add_monitor_event(
            session_id, "simulator",
            f"{agent_dialogue}（已写入 Mock API）",
            event.get("event_type", "custom"),
            event.get("target_poi_id")
        )
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"检测到注入事件 [{event.get('event_type')}]，准备通知用户...",
            "event_detected"
        )

        # Notify User Agent via pending chat event
        s["pending_monitor_msg"] = {
            "type": event.get("event_type", "custom"),
            "message": event.get("message", text[:60]),
            "severity": event.get("severity", "medium"),
            "poi_id": event.get("target_poi_id"),
        }

        return {
            "event": event,
            "event_record": event_record,
            "agent_dialogue": agent_dialogue,
        }

    # ── Flow 8: Queue advice (reads from Mock API) ───────────────────

    async def run_queue_advice(self, session_id: str) -> list[dict]:
        """Check real-time queue status from Mock API for all restaurants."""
        itinerary    = self.manager.get_itinerary(session_id)
        current_time = time.strftime("%H:%M")
        advices      = []

        for node in itinerary:
            if node.get("type") == "restaurant":
                poi_id     = node.get("poiId", "")
                queue_info = await tools.get_queue_status(poi_id)
                wait       = queue_info.get("estimated_wait_min", 0)
                if wait == 0:
                    continue

                self.manager.update_queue_history(session_id, poi_id, wait)
                trend   = self.manager.get_queue_trend(session_id, poi_id)
                history = [r[1] for r in
                           self.manager.get(session_id).get("queue_history", {}).get(poi_id, [])]

                advice = await skills.get_queue_advice(
                    restaurant_name=node.get("name", "餐厅"),
                    current_wait=wait, trend=trend, history=history,
                    planned_time=node.get("timeStart", "18:00"),
                    current_time=current_time,
                )
                advice["poi_id"]    = poi_id
                advice["node_name"] = node.get("name")
                advices.append(advice)

        return advices


# ── Helpers ──────────────────────────────────────────────────────────

def _action_label(node: dict) -> str:
    if node["type"] == "restaurant":
        return "取号排队中..."
    if node["type"] == "light":
        return "生成路线..."
    if node.get("booking_required") or node.get("booking_urgent"):
        return "预约中..."
    return "加入行程..."


def _done_label(node: dict) -> str:
    if node["type"] == "restaurant":
        return "取号成功"
    if node["type"] == "light":
        return "路线就绪"
    if node.get("booking_required") or node.get("booking_urgent"):
        return "预约成功"
    return "已加入行程"

