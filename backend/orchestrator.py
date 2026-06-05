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
from .background_watch import BackgroundWatch, build_watch_configs_for_itinerary
from .confirmation_gateway import ConfirmationGateway
from .output_validator import validate_itinerary_nodes
from .session import SessionManager

logger = logging.getLogger(__name__)
PLANNING_STALE_SECONDS = 90
REPLAN_WORDS = ("重新规划", "重规划", "换方案", "重来")
STUCK_WORDS = ("卡住", "超时", "没结果", "一直", "不动", "等这一轮")


def _emit(type_: str, **data) -> dict:
    return {"type": type_, **data}


def _voucher_code() -> str:
    return "MT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


class Orchestrator:
    def __init__(self, manager: SessionManager):
        self.manager = manager
        self.confirmation_gateway = ConfirmationGateway(manager)
        self.background_watch = BackgroundWatch(manager, self.confirmation_gateway)

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
        session = self.manager.get(session_id) or {}
        logger.info(f"run_chat phase={phase} hint={phase_hint} session={session_id[:8]}")

        if phase == "planning" and self._planning_is_stale(session):
            self.manager.set_phase(session_id, "needs_replan")
            self.manager.add_monitor_event(
                session_id,
                "main_agent",
                "规划超时，已允许用户重新规划",
                "planning_timeout",
            )
            phase = self.manager.get_phase(session_id)
            session = self.manager.get(session_id) or {}

        # Pending exception has priority over ordinary planning/chat routes.
        pending_exception = session.get("pending_exception")
        if pending_exception and phase in ("monitoring", "needs_replan") and phase_hint != "start_plan":
            yield _emit(
                "monitor_alert",
                content=pending_exception.get("message", "当前行程有异常需要先处理。"),
                severity=pending_exception.get("severity", "medium"),
            )
            yield _emit(
                "text",
                content="当前有一个行程异常待确认，我会优先处理它。请先在异常卡片里确认是否切换方案，再继续提出新的调整。",
            )
            yield _emit("done")
            return

        if phase_hint == "new_round":
            await self._prepare_next_round(session_id)
            yield _emit("text", content="收到，我会在本页面开启新一轮规划。")
            async for evt in self._run_clarify(session_id, message):
                yield evt
            return

        # ── "开始规划" button was clicked ──────────────────────────────
        if phase_hint == "start_plan":
            if phase == "confirming":
                async for evt in self._run_confirm_and_plan(session_id, message):
                    yield evt
            elif phase == "gathering":
                src = original_request or message
                if not original_request:
                    yield _emit("clarify",
                                message="抱歉，刚刚服务重启了，麻烦重新告诉我你的出行需求～"
                                        "\n比如：「今天下午2点，带孩子去玩」",
                                ready_to_plan=False,
                                phase="confirming")
                    yield _emit("done")
                    return
                async for evt in self._run_clarify_and_plan(session_id, src):
                    yield evt
            elif phase == "planning":
                if self._message_requests_replan(message):
                    self._mark_planning_failed(session_id, "用户打断卡住的规划")
                    yield _emit("text", content="收到，上一轮规划看起来卡住了。我现在重新开始规划。")
                    async for evt in self._run_monitoring_chat(session_id, "重新规划"):
                        yield evt
                else:
                    yield _emit("text", content="我还在规划中。如果一直没有结果，直接说「重新规划」我会立刻重来。")
                    yield _emit("done")
            elif phase in ("monitoring", "needs_replan"):
                async for evt in self._run_monitoring_chat(session_id, message):
                    yield evt
            elif phase == "completed":
                await self._prepare_next_round(session_id)
                src = original_request or message
                async for evt in self._run_clarify_and_plan(session_id, src):
                    yield evt
            else:
                yield _emit("text", content="当前行程已取消或不可继续，请重置会话后重新规划。")
                yield _emit("done")
            return

        # ── Normal routing by phase ────────────────────────────────────
        if phase == "gathering":
            async for evt in self._run_clarify(session_id, message):
                yield evt
        elif phase == "confirming":
            async for evt in self._run_refine_clarify(session_id, message):
                yield evt
        elif phase == "planning":
            if self._message_requests_replan(message):
                self._mark_planning_failed(session_id, "用户打断卡住的规划")
                yield _emit("text", content="收到，上一轮规划看起来卡住了。我现在重新开始规划。")
                async for evt in self._run_monitoring_chat(session_id, "重新规划"):
                    yield evt
            else:
                yield _emit("text", content="我正在规划中。如果刚才那轮卡住了，直接说「重新规划」，我会重新开始。")
                yield _emit("done")
        elif phase in ("monitoring", "needs_replan"):
            async for evt in self._run_monitoring_chat(session_id, message):
                yield evt
        elif phase == "completed":
            await self._prepare_next_round(session_id)
            yield _emit("text", content="上一轮已经完成，我会在本页面为你开启新一轮规划。")
            async for evt in self._run_clarify(session_id, message):
                yield evt
        elif phase == "cancelled":
            yield _emit("text", content="当前行程已取消，请重置会话后重新开始。")
            yield _emit("done")
        else:
            self.manager.set_phase(session_id, "gathering")
            async for evt in self._run_clarify(session_id, message):
                yield evt

    async def _prepare_next_round(self, session_id: str) -> None:
        await self.stop_background_watch(session_id)
        await self.cancel_confirmations(session_id)
        self.manager.reset_for_next_round(session_id)
        self.manager.add_monitor_event(
            session_id,
            "main_agent",
            "上一轮已完成，已在同一会话开启新一轮规划",
            "next_round",
        )

    # ── Fast-path: clarify + plan in one shot (session recovery) ─────

    async def _run_clarify_and_plan(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """
        Used when session was reset but user clicked "开始规划".
        We silently clarify and immediately proceed to planning — no extra user round.
        """
        current_time = time.strftime("%H:%M")
        result = await skills.clarify_needs(message, current_time)
        inferred = result.get("inferred", {})

        base_preferences = {
            "food": inferred.get("food_preferences", []),
            "venue": inferred.get("venue_preference"),
            "skip_restaurant": inferred.get("skip_restaurant", False),
        }
        session_facts, preferences = skills.merge_confirmed_state(
            inferred,
            base_preferences,
            {},
            {},
            message,
        )

        self.manager.update_memory(session_id, "session_facts", session_facts)
        self.manager.update_memory(session_id, "derived_preferences", preferences)
        self._mark_planning_started(session_id)
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"服务重启恢复：重新识别请求，场景={session_facts.get('scenario')}，直接规划",
            "session_recovery"
        )

        yield _emit("confirmed",
                    message=skills.append_confirmed_requirements("好的，马上帮你规划！", session_facts, preferences),
                    facts=session_facts,
                    preferences=preferences,
                    phase="planning")
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
            inferred.setdefault("home_area", loc.get("district") or loc.get("address"))

        inferred_prefs = {
            "food": inferred.get("food_preferences", []),
            "venue": inferred.get("venue_preference"),
            "skip_restaurant": inferred.get("skip_restaurant", False),
        }
        inferred, inferred_prefs = skills.merge_confirmed_state(
            inferred,
            inferred_prefs,
            {},
            {},
            message,
        )

        self.manager.set_pending_inference(session_id, inferred)
        self.manager.set_phase(session_id, "confirming")
        # Store original message for context-aware refinement later
        self.manager.clear_clarify_history(session_id)
        self.manager.append_clarify_message(session_id, message)

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

        base_msg = skills.append_confirmed_requirements(
            result.get("confirm_message", "请告诉我更多出行信息～"),
            inferred,
            inferred_prefs,
        )
        if all_clear:
            msg = base_msg + "\n\n信息都齐了！直接点「开始规划」，我马上帮你安排 👇"
        else:
            msg = base_msg + "\n\n有需要补充或纠正的话直接告诉我，确认好了再点「开始规划」。"

        yield _emit("clarify",
                    message=msg,
                    ready_to_plan=True,
                    phase="confirming",
                    facts=inferred,
                    preferences=inferred_prefs)
        yield _emit("done")

    # ── Phase: confirming (refinement) ──────────────────────────────
    async def _run_refine_clarify(self, session_id: str, message: str) -> AsyncGenerator[dict, None]:
        """User sent a refinement message while in confirming phase.
        Update the pending inference and re-emit clarify (button stays visible).
        """
        inferred = self.manager.get_pending_inference(session_id) or {}
        current_memory = self.manager.get_memory(session_id)
        previous_facts = current_memory.get("session_facts", {})
        previous_preferences = current_memory.get("derived_preferences", {})
        # Bootstrap previous_preferences from inferred so LLM null-return won't wipe venue/food
        if not previous_preferences:
            previous_preferences = {
                "food": inferred.get("food_preferences", []),
                "venue": inferred.get("venue_preference"),
                "skip_restaurant": inferred.get("skip_restaurant", False),
            }
        # Append this message then pass full history so LLM can resolve references like "按老婆的来"
        self.manager.append_clarify_message(session_id, message)
        history = self.manager.get_clarify_history(session_id)
        confirmed = await skills.confirm_preferences(inferred, message, history=history)
        updated_facts, updated_preferences = skills.merge_confirmed_state(
            previous_facts or inferred,
            previous_preferences,
            confirmed.get("session_facts", {}),
            confirmed.get("preferences", {}),
            message,
        )

        # Persist the updated inference so "开始规划" uses fresh data
        if updated_facts:
            self.manager.set_pending_inference(session_id, updated_facts)
            self.manager.update_memory(session_id, "session_facts", updated_facts)
        if updated_preferences:
            self.manager.update_memory(session_id, "derived_preferences", updated_preferences)

        # Build a short acknowledgement + prompt to start planning
        ack = skills.append_confirmed_requirements(
            confirmed.get("start_message", "好的，已更新！"),
            updated_facts or inferred,
            updated_preferences,
        )
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
        memory = self.manager.get_memory(session_id)
        memory_facts = memory.get("session_facts", {})
        memory_preferences = memory.get("derived_preferences", {})

        # Avoid re-confirming with a generic "好的" because that can erase concrete
        # user demands. Planning uses the latest confirmed snapshot directly.
        base_preferences = {
            "food": inferred.get("food_preferences", []),
            "venue": inferred.get("venue_preference"),
            "skip_restaurant": inferred.get("skip_restaurant", False),
        }
        session_facts, preferences = skills.merge_confirmed_state(
            inferred,
            base_preferences,
            memory_facts,
            memory_preferences,
            "",
        )

        self.manager.update_memory(session_id, "session_facts", session_facts)
        self.manager.update_memory(session_id, "derived_preferences", preferences)
        self.manager.set_pending_inference(session_id, None)
        self._mark_planning_started(session_id)

        start_msg = skills.append_confirmed_requirements("明白了，马上帮你规划！", session_facts, preferences)

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

        handled, skip_msg = self._try_remove_restaurants_for_skip(
            session_id, session_facts, preferences
        )
        if handled:
            yield _emit("text", content=skip_msg)
            yield _emit("itinerary_updated",
                        nodes=self.manager.get_itinerary(session_id),
                        facts=session_facts,
                        preferences=preferences,
                        phase="monitoring")
            yield _emit("done")
            return

        handled, add_msg = await self._try_add_restaurant_when_requested(
            session_id, message, session_facts, preferences
        )
        if handled:
            yield _emit("text", content=add_msg)
            yield _emit("itinerary_updated",
                        nodes=self.manager.get_itinerary(session_id),
                        facts=session_facts,
                        preferences=preferences,
                        phase="monitoring")
            yield _emit("done")
            return

        handled, action_msg = self._try_apply_natural_language_node_action(
            session_id, message
        )
        if handled:
            yield _emit("text", content=action_msg)
            yield _emit("itinerary_updated",
                        nodes=self.manager.get_itinerary(session_id),
                        facts=session_facts,
                        preferences=preferences,
                        phase="monitoring")
            yield _emit("done")
            return

        handled, replace_msg = await self._try_replace_restaurant_by_food(
            session_id, message, session_facts, preferences
        )
        if handled:
            yield _emit("text", content=replace_msg)
            yield _emit("itinerary_updated",
                        nodes=self.manager.get_itinerary(session_id),
                        facts=session_facts,
                        preferences=preferences,
                        phase="monitoring")
            yield _emit("done")
            return

        # Check if user wants to adjust something specific
        if any(w in message for w in ['重新规划', '换方案', '重规划']):
            yield _emit("text", content="好的，重新为你规划一个方案！")
            self._mark_planning_started(session_id)
            async for evt in self._run_plan_core(session_id, session_facts, preferences):
                yield evt
        else:
            # General response - just relay the message context
            yield _emit("text", content=f"已收到你的调整需求，正在为你处理：{message[:30]}...")
            self._mark_planning_started(session_id)
            async for evt in self._run_plan_core(session_id, session_facts, preferences):
                yield evt

    # ── Core: Plan ───────────────────────────────────────────────────

    def _try_remove_restaurants_for_skip(self, session_id: str,
                                         session_facts: dict,
                                         preferences: dict) -> tuple[bool, str]:
        """Remove restaurant nodes immediately when the user opts out of dining."""
        if not (session_facts.get("skip_restaurant") or preferences.get("skip_restaurant")):
            return False, ""
        itinerary = self.manager.get_itinerary(session_id)
        if not any(node.get("type") == "restaurant" or node.get("category") == "restaurant" for node in itinerary):
            return False, ""
        nodes = [
            node for node in itinerary
            if node.get("type") != "restaurant" and node.get("category") != "restaurant"
        ]
        self.manager.set_itinerary(session_id, nodes)
        self.manager.add_monitor_event(
            session_id, "main_agent",
            "User opted out of restaurants; restaurant nodes removed",
            "restaurant_removed",
        )
        return True, "好的，已按你的要求取消餐厅安排，当前行程不再包含餐厅。"

    def _try_apply_natural_language_node_action(self, session_id: str,
                                                message: str) -> tuple[bool, str]:
        """Map user natural language to direct itinerary node actions."""
        text = message or ""
        delete_words = (
            "删除", "去掉", "取消", "移除", "删掉",
            "不去", "不要", "别去", "不安排", "去除",
        )
        if not any(word in text for word in delete_words):
            return False, ""

        itinerary = self.manager.get_itinerary(session_id)
        if not itinerary:
            return False, ""

        target = self._find_node_from_message(itinerary, text)
        if not target:
            return False, ""

        if target.get("completed_lock") or target.get("_checked"):
            return True, f"「{target.get('name', '该地点')}」已经完成打卡，不能删除。"

        if target.get("locked") or target.get("booking_status") == "confirmed":
            return True, f"「{target.get('name', '该地点')}」已预约或锁定，涉及释放资源，请在行程卡片里确认取消。"

        nodes = [node for node in itinerary if node.get("id") != target.get("id")]
        self.manager.set_itinerary(session_id, nodes)
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"Natural language node delete: {target.get('name')}",
            "node_deleted",
            target.get("poiId"),
        )
        return True, f"好的，已删除「{target.get('name', '该地点')}」。"

    def _find_node_from_message(self, itinerary: list[dict], text: str) -> dict | None:
        restaurant_words = ("餐厅", "吃饭", "用餐", "餐食", "午餐", "晚餐", "饭店", "饭馆")
        activity_words = ("活动", "景点", "地点", "游玩", "公园", "商场", "展览", "桌游", "密室", "剧本杀")
        light_words = ("轻活动", "收尾", "最后")

        if any(word in text for word in restaurant_words):
            return next((node for node in itinerary if node.get("type") == "restaurant" or node.get("category") == "restaurant"), None)

        ordinal_map = {
            "第一个": 0, "第一": 0, "1": 0,
            "第二个": 1, "第二": 1, "2": 1,
            "第三个": 2, "第三": 2, "3": 2,
            "第四个": 3, "第四": 3, "4": 3,
        }
        for token, index in ordinal_map.items():
            if token in text and index < len(itinerary):
                if "活动" in text:
                    activities = [node for node in itinerary if node.get("type") == "activity"]
                    return activities[index] if index < len(activities) else None
                return itinerary[index]

        if any(word in text for word in light_words):
            return next((node for node in reversed(itinerary) if node.get("type") == "light"), itinerary[-1])

        for node in itinerary:
            searchable = " ".join(
                str(value)
                for value in [
                    node.get("name", ""),
                    node.get("sub", ""),
                    node.get("reason", ""),
                    " ".join(node.get("tags", []) or []),
                ]
            )
            if searchable and any(part and part in text for part in [node.get("name"), node.get("poiId")]):
                return node
            for tag in node.get("tags", []) or []:
                if tag and tag in text:
                    return node

        if any(word in text for word in activity_words):
            return next((node for node in itinerary if node.get("type") == "activity"), None)
        return None

    async def _try_replace_restaurant_by_food(self, session_id: str, message: str,
                                              session_facts: dict,
                                              preferences: dict) -> tuple[bool, str]:
        """Replace the current restaurant directly when the user asks for a cuisine."""
        detected = skills.detect_specific_preferences(message or "")
        requested_food = detected.get("food") or []
        if not requested_food:
            return False, ""

        itinerary = self.manager.get_itinerary(session_id)
        target_index = next(
            (
                index for index, node in enumerate(itinerary)
                if node.get("type") == "restaurant"
                and not node.get("completed_lock")
                and not node.get("_checked")
            ),
            None,
        )
        if target_index is None:
            return False, ""

        scenario = session_facts.get("scenario", "family")
        candidates = await tools.get_restaurants(
            scenario,
            preferences=requested_food,
            radius_km=20.0,
        )
        current_poi = itinerary[target_index].get("poiId")
        candidates = [item for item in candidates if item.get("poi_id") != current_poi]
        if not candidates:
            food_text = "、".join(requested_food)
            return False, f"我暂时没有找到可替换的{food_text}餐厅，继续为你重新规划。"

        def food_match_count(item: dict) -> int:
            searchable = " ".join(
                str(value)
                for value in [
                    item.get("name", ""),
                    item.get("cuisine", ""),
                    " ".join(item.get("tags", []) or []),
                    " ".join(item.get("menu_features", []) or []),
                ]
            )
            return sum(1 for food in requested_food if food and food in searchable)

        def score(item: dict) -> float:
            queue_min = int(item.get("queue_min") or 0)
            distance = float(item.get("distance_km") or 0)
            rating = float(item.get("rating") or 4.0)
            score_value = food_match_count(item) * 100
            score_value += rating * 5
            score_value -= distance * 1.5
            score_value -= max(queue_min - 15, 0) * 0.4
            if session_facts.get("has_children") and item.get("facilities", {}).get("child_seat"):
                score_value += 8
            return score_value

        best = sorted(candidates, key=score, reverse=True)[0]
        old_node = itinerary[target_index]
        queue_min = int(best.get("queue_min") or 0)
        tags = list(best.get("tags", []) or [])[:3] or [best.get("cuisine") or "餐厅"]
        reason_bits = []
        if food_match_count(best):
            reason_bits.append("符合" + "、".join(requested_food))
        if session_facts.get("has_children") and best.get("facilities", {}).get("child_seat"):
            reason_bits.append("儿童椅")
        if queue_min <= 15:
            reason_bits.append("排队较短")

        updated_node = {
            **old_node,
            "icon": "🍽️",
            "name": best.get("name", old_node.get("name", "")),
            "sub": best.get("address", old_node.get("sub", "")),
            "distance": f"{float(best.get('distance_km') or 0):.1f}公里",
            "queueMin": queue_min,
            "queueText": f"约{queue_min}分钟" if queue_min > 0 else "无需排队",
            "price": f"¥{best.get('avg_price', 80)}/位",
            "rating": best.get("rating", old_node.get("rating", 4.5)),
            "tags": tags,
            "reason": "·".join(reason_bits)[:25] or (best.get("cuisine") or "匹配你的新口味"),
            "poiId": best.get("poi_id", old_node.get("poiId")),
            "booking_urgent": best.get("booking_required", False),
        }
        new_nodes = list(itinerary)
        new_nodes[target_index] = updated_node
        self.manager.set_itinerary(session_id, new_nodes)
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"Restaurant replaced by cuisine request: {old_node.get('name')} -> {updated_node.get('name')}",
            "restaurant_replaced",
            updated_node.get("poiId"),
        )
        return True, f"已按{'、'.join(requested_food)}帮你把餐厅换成：{updated_node.get('name')}。"

    async def _try_add_restaurant_when_requested(self, session_id: str, message: str,
                                                 session_facts: dict,
                                                 preferences: dict) -> tuple[bool, str]:
        """Add a restaurant node when the user reverses a previous no-dining choice."""
        detected = skills.detect_specific_preferences(message or "")
        if not detected.get("wants_restaurant"):
            return False, ""

        itinerary = self.manager.get_itinerary(session_id)
        if not itinerary:
            return False, ""
        if any(node.get("type") == "restaurant" or node.get("category") == "restaurant" for node in itinerary):
            return False, ""

        requested_food = (
            detected.get("food")
            or preferences.get("food")
            or session_facts.get("food_preferences")
            or []
        )
        scenario = session_facts.get("scenario", "family")
        candidates = await tools.get_restaurants(
            scenario,
            preferences=requested_food or None,
            radius_km=20.0,
        )
        if not candidates:
            return False, "我理解你想加餐厅，但暂时没找到合适的餐厅候选，我会继续为你重新规划。"

        def score(item: dict) -> float:
            queue_min = int(item.get("queue_min") or 0)
            distance = float(item.get("distance_km") or 0)
            rating = float(item.get("rating") or 4.0)
            value = rating * 10 - distance * 2 - max(queue_min - 15, 0) * 0.5
            searchable = " ".join(
                str(v)
                for v in [
                    item.get("name", ""),
                    item.get("cuisine", ""),
                    " ".join(item.get("tags", []) or []),
                    " ".join(item.get("menu_features", []) or []),
                ]
            )
            value += sum(25 for food in requested_food if food and food in searchable)
            if session_facts.get("has_children") and item.get("facilities", {}).get("child_seat"):
                value += 8
            return value

        best = sorted(candidates, key=score, reverse=True)[0]
        prev = itinerary[-1]
        start_min = self._parse_node_time(prev.get("endTime") or prev.get("end_time")) + 15
        if start_min <= 15:
            start_min = self._parse_node_time(session_facts.get("start_time", "14:00")) + 120
        end_min = start_min + 75
        queue_min = int(best.get("queue_min") or 0)
        tags = list(best.get("tags", []) or [])[:3] or [best.get("cuisine") or "餐厅"]
        restaurant_node = {
            "id": f"node_{len(itinerary) + 1:03d}",
            "poiId": best.get("poi_id"),
            "type": "restaurant",
            "category": "restaurant",
            "icon": "🍽️",
            "name": best.get("name", "附近餐厅"),
            "sub": best.get("address", ""),
            "address": best.get("address", ""),
            "startTime": self._format_node_time(start_min),
            "endTime": self._format_node_time(end_min),
            "duration": 75,
            "distance": f"{float(best.get('distance_km') or 0):.1f}公里",
            "queueMin": queue_min,
            "queueText": f"约{queue_min}分钟" if queue_min > 0 else "无需排队",
            "price": f"¥{best.get('avg_price', 80)}/位",
            "rating": best.get("rating", 4.5),
            "tags": tags,
            "reason": "按你的新需求补充餐厅",
            "booking_required": best.get("booking_required", False),
            "booking_urgent": best.get("booking_required", False),
        }
        self.manager.set_itinerary(session_id, [*itinerary, restaurant_node])
        self.manager.add_monitor_event(
            session_id, "main_agent",
            f"Restaurant added by natural language request: {restaurant_node.get('name')}",
            "restaurant_added",
            restaurant_node.get("poiId"),
        )
        return True, f"可以，已按你的新想法加上餐厅：{restaurant_node.get('name')}。"

    @staticmethod
    def _parse_node_time(value: str | None) -> int:
        try:
            hour, minute = (value or "00:00").split(":")[:2]
            return int(hour) * 60 + int(minute)
        except Exception:
            return 0

    @staticmethod
    def _format_node_time(total_minutes: int) -> str:
        total_minutes = max(0, total_minutes)
        return f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}"

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

        try:
            agent_stream = skills.run_agent_plan(sf_with_pinned, preferences)
            while True:
                try:
                    item = await asyncio.wait_for(
                        anext(agent_stream),
                        timeout=PLANNING_STALE_SECONDS,
                    )
                except StopAsyncIteration:
                    break
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
                        self._mark_planning_failed(session_id, "工具调用持续失败")
                        yield _emit("error", message="工具调用持续失败，请检查 Mock API 是否正常运行后重试")
                        yield _emit("done")
                    return

                elif t == "result":
                    plan = item["plan"]
                    yield _emit("status", id=2, status="done")
                    yield _emit("status", id=3, status="done")
                    yield _emit("status", id=4, status="done")
        except asyncio.TimeoutError:
            self._mark_planning_failed(session_id, "Agent 规划超时")
            yield _emit("error", message="Agent 规划超时了，已为你开放重新规划。请直接说「重新规划」或补充新的具体需求。")
            yield _emit("done")
            return
        except Exception as exc:
            logger.exception("Agent planning failed")
            self._mark_planning_failed(session_id, str(exc))
            yield _emit("error", message=f"Agent 规划中断：{exc}。已为你开放重新规划。")
            yield _emit("done")
            return

        if not plan:
            self._mark_planning_failed(session_id, "Agent 未能生成行程")
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

        if session_facts.get("skip_restaurant") or preferences.get("skip_restaurant"):
            before = len(nodes)
            nodes = [
                node for node in nodes
                if node.get("type") != "restaurant" and node.get("category") != "restaurant"
            ]
            if before != len(nodes):
                yield _emit("cot_step", text=f"⚠️ 已按用户要求移除 {before - len(nodes)} 个餐厅节点")

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

        # ── Feasibility + output validation ───────────────────────────
        legacy_risks = skills.validate_feasibility(nodes, session_facts)
        validation = validate_itinerary_nodes(nodes, session_facts, preferences)
        risks = []
        for risk in [*legacy_risks, *validation.issues]:
            if risk not in risks:
                risks.append(risk)

        if risks:
            for r in risks:
                yield _emit("cot_step", text=f"⚠️ 合理性警告：{r}")
            self.manager.add_monitor_event(
                session_id, "main_agent",
                f"合理性检查：{'; '.join(risks[:2])}", "feasibility_warning"
            )
        else:
            yield _emit("cot_step", text="✅ 合理性自检通过：结构/偏好/通勤/时间均合格")

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
        self._clear_planning_started(session_id)
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
            done_text = _done_label(node)
            voucher = _voucher_code() if node["type"] != "light" else None

            self.manager.update_node(session_id, node["id"], {"booking_status": "queued"})
            yield _emit("fulfill_item", id=node["id"], status="loading", action="已进入履约队列...")
            await asyncio.sleep(0.2)

            self.manager.update_node(session_id, node["id"], {"booking_status": "processing"})
            yield _emit("fulfill_item", id=node["id"], status="loading", action="正在确认资源...")

            booking_result = await tools.booking_execute(node)
            if booking_result.get("status") == "failed":
                self.manager.update_node(session_id, node["id"], {"booking_status": "failed"})
                yield _emit("fulfill_item", id=node["id"], status="done", action="履约失败，请稍后重试")
                continue

            self.manager.update_node(session_id, node["id"], {
                "booking_status": "confirmed",
                "booking_ref": booking_result.get("booking_ref"),
            })
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
        watch_configs = build_watch_configs_for_itinerary(self.manager.get_itinerary(session_id))
        if watch_configs:
            await self.background_watch.stop_all(session_id)
            await self.background_watch.start_watch_group(session_id, watch_configs)
            self.manager.add_monitor_event(
                session_id, "background_watch",
                f"已启动 {len(watch_configs)} 个后台监控任务", "watch_started"
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
        confirmed = bool(data.get("confirmed", True))
        request_id = data.get("request_id")

        if not request_id:
            pending = self.confirmation_gateway.find_latest_pending(session_id, "replan")
            request_id = pending.get("request_id") if pending else None

        if request_id:
            await self.confirmation_gateway.resolve(
                session_id,
                request_id,
                confirmed,
                {"exception_type": exception_type, "recommended": recommended},
            )

        if not confirmed:
            self.manager.clear_exception(session_id)
            self.manager.add_monitor_event(
                session_id, "main_agent",
                "用户选择保留原方案，异常确认已关闭", "replan_rejected"
            )
            yield _emit("text", content="好的，已保留原方案。我会继续监控，有变化再提醒你。")
            yield _emit("done")
            return

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

        if total and done_count == total:
            await self.background_watch.stop_all(session_id)
            self.manager.add_monitor_event(
                session_id, "background_watch",
                "行程已全部完成，后台监控已停止", "watch_stopped"
            )

        return {"nodes": updated, "next_node": next_node,
                "message": msg, "done_count": done_count, "total": total}

    async def stop_background_watch(self, session_id: str) -> None:
        await self.background_watch.stop_all(session_id)

    async def cancel_confirmations(self, session_id: str) -> None:
        await self.confirmation_gateway.cancel_session(session_id)

    def _mark_planning_started(self, session_id: str) -> None:
        session = self.manager.get(session_id)
        if session is not None:
            session["planning_started_at"] = time.time()
            session["planning_error"] = None
        self.manager.set_phase(session_id, "planning")

    def _clear_planning_started(self, session_id: str) -> None:
        session = self.manager.get(session_id)
        if session is not None:
            session["planning_started_at"] = None
            session["planning_error"] = None

    def _mark_planning_failed(self, session_id: str, reason: str) -> None:
        session = self.manager.get(session_id)
        if session is not None:
            session["planning_error"] = reason
            session["planning_started_at"] = None
        self.manager.set_phase(session_id, "needs_replan")
        self.manager.add_monitor_event(
            session_id,
            "main_agent",
            f"规划失败/超时: {reason}",
            "planning_failed",
        )

    def _planning_is_stale(self, session: dict) -> bool:
        started = session.get("planning_started_at")
        if started:
            return time.time() - float(started) > PLANNING_STALE_SECONDS
        return session.get("phase") == "planning"

    def _message_requests_replan(self, message: str) -> bool:
        return any(word in message for word in (*REPLAN_WORDS, *STUCK_WORDS))

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
