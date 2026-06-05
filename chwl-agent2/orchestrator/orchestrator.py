"""
Central Orchestrator — 中央协调器

实现三个核心流程：
Phase 1 - 规划：并行数据采集 → LLM 评分 → 路线计算 → 行程生成
Phase 2 - 履约：并行 Booking → 状态监控 → 完成/部分失败 → Fallback
Phase 3 - 重规划：异常触发 → 保护锁定节点 → 替换方案 → 用户确认

对外暴露的 API（供 Planner/UI 层调用）：
  start_session()
  get_status()
  modify_itinerary()
  confirm_itinerary()
  handle_user_sentiment()
  resolve_confirmation()
  cancel_session()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from core.state_machine import (
    ItineraryState, NodeState,
    create_itinerary_fsm, create_node_fsm,
    StateChangeEvent, InvalidTransitionError,
)
from core.models import (
    ItineraryData, ItineraryNode, NodeCategory, ResourceType,
    UserContext, UserSentiment, ItineraryModification,
    SessionStatus, NodeStatus, ConfirmationRequest as ConfRequestModel,
    BookingAction, ModeType, SceneType, ConflictInfo,
)
from core.tool_registry import ToolRegistry
from core.memory_store import MemoryStore
from orchestrator.event_bus import EventBus
from orchestrator.confirmation_gateway import ConfirmationGateway
from orchestrator.background_watch import BackgroundWatch, WatchConfig, WatchType

logger = logging.getLogger(__name__)

# Pydantic 兼容：v2 用 model_dump，v1 用 dict
def _d(obj):
    return obj.model_dump() if hasattr(obj, 'model_dump') else obj.dict()


# ─── 会话上下文 ──────────────────────────────────────────────

@dataclass
class ExecutionContext:
    """一次规划-履约会话的完整上下文"""
    session_id: str
    user_input: str = ""

    # 原始数据
    location: dict = field(default_factory=dict)
    user_context: UserContext = field(default_factory=UserContext)
    weather: dict = field(default_factory=dict)
    raw_candidates: list[dict] = field(default_factory=list)       # 未评分
    scored_candidates: list[dict] = field(default_factory=list)     # 已评分
    routes: list[dict] = field(default_factory=list)

    # 行程
    itinerary: Optional[ItineraryData] = None
    itinerary_sm: Any = None              # StateMachine[ItineraryState]
    node_sms: dict[str, Any] = field(default_factory=dict)  # node_id -> StateMachine[NodeState]

    # 履约跟踪
    booking_tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    # 编辑状态
    has_user_edits: bool = False
    edit_count: int = 0

    # 外部依赖
    tools: Optional[ToolRegistry] = None
    event_bus: Optional[EventBus] = None
    confirmation: Optional[ConfirmationGateway] = None


# ─── Orchestrator ─────────────────────────────────────────────

class Orchestrator:
    """
    中央协调器。

    用法:
        tools = ToolRegistry()
        tools.register_mock("weather", my_handler)
        orch = Orchestrator(tools)
        sid = await orch.start_session("test", "下午出去玩")
        status = await orch.get_status(sid)
        await orch.confirm_itinerary(sid)
    """

    def __init__(self, tool_registry: ToolRegistry,
                 event_bus: Optional[EventBus] = None):
        self.tools = tool_registry
        self.event_bus = event_bus or EventBus()
        self.confirmation = ConfirmationGateway(self.event_bus)
        self.watch_engine = BackgroundWatch(self.event_bus, self.tools)
        self.memory = MemoryStore()
        self.sessions: dict[str, ExecutionContext] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        # 订阅后台监控告警 → 自动触发重规划
        self.event_bus.subscribe("watch_alert", self._on_watch_alert)

    # ═══════════════════════════════════════════════════════════
    # 公开 API（供 Planner / UI 层调用）
    # ═══════════════════════════════════════════════════════════

    async def start_session(self, user_input: str,
                             session_id: str = "") -> str:
        """
        启动新会话。

        1. 创建 ExecutionContext
        2. 初始化状态机
        3. 异步启动 Phase 1 规划
        4. 立即返回 session_id（非阻塞）
        """
        sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        ctx = ExecutionContext(
            session_id=sid,
            user_input=user_input,
            tools=self.tools,
            event_bus=self.event_bus,
            confirmation=self.confirmation,
        )
        ctx.itinerary_sm = create_itinerary_fsm()
        await ctx.itinerary_sm.transition_to(ItineraryState.DRAFT, ctx)
        self.sessions[sid] = ctx

        logger.info("[Session] %s 创建: %s", sid[:12], user_input[:50])

        # 异步启动 Phase 1
        task = asyncio.create_task(
            self._phase1_plan(ctx), name=f"phase1_{sid}"
        )
        self._running_tasks[f"phase1_{sid}"] = task
        task.add_done_callback(
            lambda t: self._running_tasks.pop(f"phase1_{sid}", None)
        )

        return sid

    async def get_status(self, session_id: str) -> SessionStatus:
        """查询会话状态"""
        ctx = self._get_ctx(session_id)
        nodes = []
        for node in (ctx.itinerary.nodes if ctx.itinerary else []):
            sm = ctx.node_sms.get(node.node_id)
            nodes.append({
                "node_id": node.node_id,
                "name": node.poi_name,
                "state": sm.state.value if sm else node.status,
                "booking_status": node.booking_status,
                "start_time": node.scheduled_start,
                "end_time": node.scheduled_end,
            })

        pending_reqs = self.confirmation.list_pending()
        return SessionStatus(
            session_id=session_id,
            itinerary_state=ctx.itinerary_sm.state.value if ctx.itinerary_sm else "init",
            mode=ctx.user_context.mode,
            scene=ctx.user_context.scene,
            nodes=nodes,
            progress_pct=self._calc_progress(ctx),
            has_pending_confirmation=len(pending_reqs) > 0,
            pending_confirmation_type=pending_reqs[0].type if pending_reqs else None,
            summary=ctx.itinerary.summary if ctx.itinerary else "",
        )

    async def modify_itinerary(self, session_id: str,
                                mod: ItineraryModification) -> ItineraryData:
        """
        用户编辑行程。

        支持：replace / delete / insert / reorder
        编辑后 itinerary 回到 DRAFT 状态（如果之前是 PENDING_CONFIRM）。
        """
        ctx = self._get_ctx(session_id)
        if not ctx.itinerary:
            raise ValueError("行程尚未生成")

        self._apply_modification(ctx, mod)
        ctx.has_user_edits = True
        ctx.edit_count += 1

        # 如果当前是 PENDING_CONFIRM，退回 DRAFT 允许编辑
        if ctx.itinerary_sm.state == ItineraryState.PENDING_CONFIRM:
            try:
                await ctx.itinerary_sm.transition_to(ItineraryState.DRAFT, ctx)
            except InvalidTransitionError:
                pass  # guard 条件不满足时保持当前状态

        # 更新 summary
        ctx.itinerary.summary = self._generate_summary(ctx.itinerary)

        await self.event_bus.emit("plan_modified", ctx, {
            "itinerary": _d(ctx.itinerary),
            "modification": _d(mod),
        })
        return ctx.itinerary

    async def confirm_itinerary(self, session_id: str) -> dict:
        """
        用户点击"一键安排"。

        转换到 EXECUTING 状态，启动 Phase 2 并行履约。
        """
        ctx = self._get_ctx(session_id)
        if not ctx.itinerary:
            raise ValueError("行程尚未生成，无法确认")

        # 合理性最终校验（不通过则警告但继续，保证 Demo 主链路）
        if ctx.itinerary.feasibility_check and not ctx.itinerary.feasibility_check.passed:
            await self.event_bus.emit("status_update", ctx, {
                "message": "行程合理性存在风险，但仍可继续",
            })

        await ctx.itinerary_sm.transition_to(ItineraryState.EXECUTING, ctx)

        # 所有节点转入 PENDING
        for node in ctx.itinerary.nodes:
            if node.needs_booking and not node.completed_lock:
                sm = ctx.node_sms.get(node.node_id)
                if sm and sm.state in (NodeState.PLANNED,):
                    await sm.transition_to(NodeState.PENDING, ctx)

        logger.info("[Session] %s 用户确认，开始履约", session_id[:12])

        # 异步启动 Phase 2
        task = asyncio.create_task(
            self._phase2_execute(ctx), name=f"phase2_{session_id}"
        )
        self._running_tasks[f"phase2_{session_id}"] = task

        return {"status": "executing", "session_id": session_id}

    async def handle_user_sentiment(self, session_id: str,
                                     sentiment: UserSentiment) -> dict:
        """
        用户报告情绪/状态变化（如"孩子累了"）。

        触发 Phase 3 重规划。
        """
        ctx = self._get_ctx(session_id)
        logger.info("[Session] %s 用户上报: %s", session_id[:12], _d(sentiment))

        await self.event_bus.emit("user_sentiment", ctx, {
            "sentiment": _d(sentiment),
        })

        # 异步启动 Phase 3
        task = asyncio.create_task(
            self._phase3_replan(ctx, sentiment), name=f"phase3_{session_id}"
        )
        self._running_tasks[f"phase3_{session_id}"] = task

        return {"status": "replanning", "session_id": session_id}

    async def resolve_confirmation(self, request_id: str, approved: bool,
                                    modifications: dict = None) -> bool:
        """用户响应确认请求"""
        # 找到对应的 session
        for sid, ctx in self.sessions.items():
            req = ctx.confirmation.get_pending(request_id) if ctx.confirmation else None
            if req:
                await ctx.confirmation.resolve(request_id, approved,
                                                modifications or {}, ctx)
                return True
        return False

    async def cancel_session(self, session_id: str) -> dict:
        """取消会话（含资源损失警告）"""
        ctx = self._get_ctx(session_id)

        # 资源损失警告：检查有成本的节点
        resource_losses = []
        if ctx.itinerary:
            for node in ctx.itinerary.nodes:
                if node.booking_ref and node.booking_status == "confirmed":
                    resource_losses.append({
                        "node_id": node.node_id,
                        "name": node.poi_name,
                        "booking_ref": node.booking_ref,
                        "type": node.resource_type.value if node.resource_type else "unknown",
                    })
                if node.soft_lock:
                    resource_losses.append({
                        "node_id": node.node_id,
                        "name": node.poi_name,
                        "reason": node.soft_lock_reason or "资源已占用",
                    })

        if resource_losses:
            await self.event_bus.emit("resource_loss_warning", ctx, {
                "message": "取消当前计划将释放以下预约/排队资格",
                "losses": resource_losses,
            })

        try:
            await ctx.itinerary_sm.transition_to(ItineraryState.CANCELLED, ctx)
        except InvalidTransitionError:
            pass
        # 取消正在运行的 booking
        for node_id, task in ctx.booking_tasks.items():
            task.cancel()
        # 停止后台监控
        await self.watch_engine.stop_all(session_id)
        # 清理会话记忆
        self.memory.clear_session(session_id)
        await self.event_bus.emit("session_cancelled", ctx, {"reason": "user_cancelled"})
        logger.info("[Session] %s 已取消", session_id[:12])
        return {"status": "cancelled", "session_id": session_id}

    # ═══════════════════════════════════════════════════════════
    # Phase 1: 规划 (Planning)
    # ═══════════════════════════════════════════════════════════

    async def _phase1_plan(self, ctx: ExecutionContext):
        """
        Phase 1 规划流程:

        Step 1: 并行采集 5 路数据
        Step 2: 合并候选 → LLM 评分
        Step 3: 路线计算
        Step 4: 行程生成
        """
        try:
            await self.event_bus.emit("status_update", ctx, {
                "message": "正在获取当前位置、天气和活动信息..."
            })

            # ── Step 1: 5 路并行数据采集 ──
            data = await self.tools.invoke_parallel([
                ("location", {"session_id": ctx.session_id}),
                ("user_context", {"session_id": ctx.session_id, "input": ctx.user_input,
                                  "start_time": "14:00"}),
                ("weather", {"city": "北京", "business_area": "望京"}),
                ("activities_search", {"session_id": ctx.session_id, "scene": "auto"}),
                ("restaurants_search", {"session_id": ctx.session_id, "party_size": 3}),
            ])

            # 处理结果
            for name, result in data.items():
                if not result.get("success"):
                    logger.warning("[Phase1] %s 采集失败: %s", name,
                                   result.get("error", {}).get("code", "unknown"))
                    await self.event_bus.emit("tool_warning", ctx, {
                        "tool": name, "error": result.get("error", {}),
                    })

            ctx.location = data.get("location", {}).get("data", {})
            ctx.user_context = UserContext(
                **data.get("user_context", {}).get("data", {}))
            ctx.weather = data.get("weather", {}).get("data", {})

            # ── 写入 MemoryStore 会话事实 ──
            self.memory.put_batch(ctx.session_id, "session_facts", {
                "scene": ctx.user_context.scene.value if ctx.user_context.scene else "family",
                "mode": ctx.user_context.mode.value if ctx.user_context.mode else "light_managed",
                "start_time": ctx.user_context.start_time or "14:00",
                "location": ctx.location.get("business_area", "未知"),
                "weather": ctx.weather.get("weather", "未知"),
            }, source="user_context", confidence=0.9)

            # 写入特殊需求（低置信度 derived）
            if ctx.user_context.special_requirements:
                for req in ctx.user_context.special_requirements:
                    self.memory.put(ctx.session_id, "derived_preferences",
                                    f"requirement_{req}",
                                    req, confidence=0.6, source="user_input")

            # 合并候选
            acts = data.get("activities_search", {}).get("data", {}).get("activities", [])
            rests = data.get("restaurants_search", {}).get("data", {}).get("restaurants", [])
            ctx.raw_candidates = acts + rests

            if not ctx.raw_candidates:
                raise ValueError("未找到任何活动/餐厅候选，请调整搜索条件")

            await self.event_bus.emit("status_update", ctx, {
                "message": f"已找到 {len(acts)} 个活动和 {len(rests)} 个餐厅，正在评分..."
            })

            # ── Step 2: LLM 评分 ──
            score_result = await self.tools.invoke("candidates_score", {
                "candidates": ctx.raw_candidates,
                "user_context": _d(ctx.user_context),
                "weather": ctx.weather,
                "mode": ctx.user_context.mode.value,
            })

            if score_result.get("success"):
                # 合并评分结果和原始数据（评分结果只有 poi_id + score，缺 category/name）
                scored_list = score_result["data"].get("scored", [])
                scored_map = {s["poi_id"]: s for s in scored_list if s.get("poi_id")}
                ctx.scored_candidates = []
                for c in ctx.raw_candidates:
                    pid = c.get("poi_id", "")
                    if pid in scored_map:
                        merged = dict(scored_map[pid])
                        merged["name"] = c.get("name", "")
                        merged["category"] = c.get("category", "")
                        merged["distance_km"] = c.get("distance_km", 0)
                        merged["rating"] = c.get("rating", 0)
                        merged["tags"] = c.get("tags", [])
                        merged["estimated_duration_min"] = c.get("estimated_duration_min", 60)
                        ctx.scored_candidates.append(merged)
                    else:
                        ctx.scored_candidates.append(c)
            else:
                logger.warning("[Phase1] 评分失败，使用未评分候选")
                ctx.scored_candidates = [
                    {"poi_id": c.get("poi_id"), **c} for c in ctx.raw_candidates
                ]

            # ── Step 3: 路线计算 ──
            # 选评分最高的活动 + 餐厅
            top_activity = self._pick_best(ctx.scored_candidates, ["indoor_playground", "museum",
                                           "exhibition", "outdoor_walk", "entertainment"])
            top_restaurant = self._pick_best(ctx.scored_candidates, ["restaurant"])
            top_walk = self._get_mock_walk()

            route_calls = []
            pairs = [
                ("route_home_act", "route_check",
                 {"origin": "current_location", "destinations": [top_activity.get("poi_id", "")]}),
            ]
            if top_restaurant:
                pairs.append(("route_act_res", "route_check",
                             {"origin": top_activity.get("poi_id", ""),
                              "destinations": [top_restaurant.get("poi_id", "")]}))
            if top_walk:
                pairs.append(("route_res_walk", "route_check",
                             {"origin": top_restaurant.get("poi_id", ""),
                              "destinations": [top_walk.get("poi_id", "")]}))

            route_results = await self.tools.invoke_parallel([
                (name, payload) for name, _, payload in pairs
            ])
            ctx.routes = [r.get("data", {}) for _, r in route_results.items()]

            # ── Step 4: 生成行程 ──
            await self.event_bus.emit("status_update", ctx, {
                "message": "正在生成行程方案..."
            })

            scene = ctx.user_context.scene.value
            gen_result = await self.tools.invoke("itinerary_generate", {
                "departure_time": ctx.user_context.start_time or "14:00",
                "selected_nodes": {
                    "main_activity": top_activity,
                    "restaurant": top_restaurant,
                    "optional_activity": top_walk,
                },
                "scene": scene,
                "mode": ctx.user_context.mode.value,
                "user_feedback": ctx.user_input,
                "companions": getattr(ctx.user_context, "companions", []),
            })

            if not gen_result.get("success"):
                raise ValueError(f"行程生成失败: {gen_result.get('error', {})}")

            itinerary_data = gen_result["data"]
            ctx.itinerary = ItineraryData(
                itinerary_id=itinerary_data.get("itinerary_id", f"iti_{uuid.uuid4().hex[:8]}"),
                session_id=ctx.session_id,
                mode=ctx.user_context.mode,
                scene=ctx.user_context.scene,
                start_time=ctx.user_context.start_time or "14:00",
                status="pending_confirm",
                total_duration_min=itinerary_data.get("total_duration_min", 0),
                summary=itinerary_data.get("summary", ""),
                weather=ctx.weather,
                location=ctx.location,
            )

            # 从 itinerary_data 构建节点
            for raw_node in itinerary_data.get("nodes", []):
                node = ItineraryNode(
                    node_id=raw_node.get("node_id", f"n_{uuid.uuid4().hex[:4]}"),
                    poi_id=raw_node.get("poi_id", ""),
                    poi_name=raw_node.get("poi_name", ""),
                    address=raw_node.get("address", ""),
                    category=self._map_category(raw_node.get("category", "")),
                    scheduled_start=raw_node.get("start_time", ""),
                    scheduled_end=raw_node.get("end_time", ""),
                    duration_min=raw_node.get("duration_min", 60),
                    status="planned",
                    tags=raw_node.get("tags", []),
                )
                ctx.itinerary.nodes.append(node)

                # 创建 node 状态机
                sm = create_node_fsm()
                ctx.node_sms[node.node_id] = sm

            # 补全时间数据：LLM 可能缺失 scheduled_end，用 duration_min 推算
            for i, n in enumerate(ctx.itinerary.nodes):
                if not n.scheduled_end and n.scheduled_start and n.duration_min:
                    # 用 scheduled_start + duration_min 推算 end_time
                    h, m = map(int, n.scheduled_start.split(":"))
                    total = h * 60 + m + n.duration_min
                    n.scheduled_end = f"{total // 60:02d}:{total % 60:02d}"
                if not n.scheduled_start and i > 0:
                    # 用上一个节点的 end_time 作为 start_time
                    prev = ctx.itinerary.nodes[i - 1]
                    if prev.scheduled_end:
                        n.scheduled_start = prev.scheduled_end

            # 合理性校验（不通过则通知前端，但继续执行让用户看到方案）
            self._validate_feasibility(ctx)
            fc = ctx.itinerary.feasibility_check
            if not fc.passed:
                risks = ctx.event_bus.get_history("feasibility_risks", 1) if ctx.event_bus else []
                risk_msgs = risks[0]["data"]["risk_flags"] if risks else []
                logger.warning("[Phase1] %s 合理性校验不通过: %s",
                               ctx.session_id[:12], risk_msgs)
                await self.event_bus.emit("feasibility_risks", ctx, {
                    "passed": False, "risk_flags": risk_msgs,
                })

            # 转换到 PENDING_CONFIRM（如果会话已被取消则跳过）
            if ctx.itinerary_sm.state == ItineraryState.CANCELLED:
                logger.info("[Phase1] %s 会话已取消，跳过状态转换", ctx.session_id[:12])
                return
            await ctx.itinerary_sm.transition_to(
                ItineraryState.PENDING_CONFIRM, ctx)

            # 通知外部
            await self.event_bus.emit("plan_complete", ctx, {
                "itinerary": _d(ctx.itinerary),
            })
            logger.info("[Phase1] %s 规划完成，%d 个节点",
                        ctx.session_id[:12], len(ctx.itinerary.nodes))

        except Exception as e:
            logger.error("[Phase1] %s 规划失败: %s", ctx.session_id[:12], e, exc_info=True)
            await self.event_bus.emit("plan_failed", ctx, {"error": str(e)})

    # ═══════════════════════════════════════════════════════════
    # Phase 2: 履约 (Execution)
    # ═══════════════════════════════════════════════════════════

    async def _phase2_execute(self, ctx: ExecutionContext):
        """
        Phase 2 履约流程:

        1. 对所有 needs_booking 节点并行发起 booking
        2. 每个 booking 启动独立的状态监控协程
        3. 等待所有完成
        4. 评估结果：全部成功 → COMPLETED；部分失败 → NEEDS_REPLAN
        """
        await self.event_bus.emit("execution_started", ctx, {
            "session_id": ctx.session_id,
            "node_count": len([n for n in ctx.itinerary.nodes if n.needs_booking]),
        })

        # ── Step 1: 并行发起所有 booking ──
        for node in ctx.itinerary.nodes:
            if node.needs_booking and node.status in ("planned", "pending"):
                sm = ctx.node_sms.get(node.node_id)
                if sm and sm.state == NodeState.PENDING:
                    await sm.transition_to(NodeState.PROCESSING, ctx)

                # 创建独立的 booking 协程
                task = asyncio.create_task(
                    self._book_single_node(ctx, node),
                    name=f"book_{ctx.session_id}_{node.node_id}",
                )
                ctx.booking_tasks[node.node_id] = task

        # ── 自动启动 Background Watch（排队 + 天气）──
        watch_configs = self._build_watch_configs(ctx)
        if watch_configs:
            asyncio.create_task(
                self.watch_engine.start_watch_group(ctx.session_id, watch_configs)
            )

        # ── Step 2: 等待所有 booking 完成 ──
        if ctx.booking_tasks:
            done, _ = await asyncio.wait(
                ctx.booking_tasks.values(),
                return_when=asyncio.ALL_COMPLETED,
            )

        # ── Step 3: 评估结果 ──
        all_success = all(
            ctx.node_sms[n.node_id].state == NodeState.COMPLETED_LOCK
            for n in ctx.itinerary.nodes
            if n.needs_booking and n.node_id in ctx.node_sms
        )
        any_failed = any(
            ctx.node_sms[n.node_id].state in (NodeState.FAILED,)
            for n in ctx.itinerary.nodes
            if n.needs_booking and n.node_id in ctx.node_sms
        )

        if all_success:
            await ctx.itinerary_sm.transition_to(ItineraryState.COMPLETED, ctx)
            await self.event_bus.emit("execution_complete", ctx, {
                "itinerary": _d(ctx.itinerary),
            })
            logger.info("[Phase2] %s 全部履约完成", ctx.session_id[:12])

        elif any_failed:
            await ctx.itinerary_sm.transition_to(ItineraryState.NEEDS_REPLAN, ctx)
            failed_ids = [n.node_id for n in ctx.itinerary.nodes
                          if ctx.node_sms.get(n.node_id) and
                          ctx.node_sms[n.node_id].state == NodeState.FAILED]
            await self.event_bus.emit("execution_partial_failure", ctx, {
                "itinerary": _d(ctx.itinerary),
                "failed_nodes": failed_ids,
            })
            logger.info("[Phase2] %s 部分失败: %s", ctx.session_id[:12], failed_ids)

    async def _book_single_node(self, ctx: ExecutionContext, node: ItineraryNode):
        """Booking 一个节点 + 启动状态监控"""
        try:
            # 发起 booking
            result = await self.tools.invoke("booking_execute", {
                "node_id": node.node_id,
                "resource_id": node.poi_id,
                "type": node.resource_type.value,
                "time_slot": f"{node.scheduled_start}-{node.scheduled_end}",
            })

            if not result.get("success"):
                await ctx.node_sms[node.node_id].transition_to(NodeState.FAILED, ctx)
                await self._handle_node_failure(ctx, node,
                    result.get("error", {}))
                return

            booking_data = result.get("data", {})
            node.booking_ref = booking_data.get("booking_ref", "")
            node.booking_status = booking_data.get("status", "")

            await self.event_bus.emit("booking_status_changed", ctx, {
                "node_id": node.node_id,
                "name": node.poi_name,
                "status": node.booking_status,
            })

            # 启动状态监控
            await self._monitor_booking(ctx, node)

        except asyncio.CancelledError:
            logger.debug("[Book] %s 被取消", node.node_id)
        except Exception as e:
            logger.error("[Book] %s 异常: %s", node.node_id, e)
            if node.node_id in ctx.node_sms:
                try:
                    await ctx.node_sms[node.node_id].transition_to(NodeState.FAILED, ctx)
                except InvalidTransitionError:
                    pass
                await self._handle_node_failure(ctx, node, {"code": "internal_error",
                                              "message": str(e)})

    async def _monitor_booking(self, ctx: ExecutionContext, node: ItineraryNode):
        """轮询 booking 状态直到终态"""
        max_polls = 30  # 最多轮询 30 次（约 150s）
        poll_count = 0

        while poll_count < max_polls:
            await asyncio.sleep(5)  # 5 秒间隔
            poll_count += 1

            if not node.booking_ref:
                break

            result = await self.tools.invoke("booking_status", {
                "booking_ref": node.booking_ref,
            })

            if not result.get("success"):
                continue

            status = result.get("data", {}).get("status", "")

            # 通知外部
            if status != node.booking_status:
                node.booking_status = status
                await self.event_bus.emit("booking_status_changed", ctx, {
                    "node_id": node.node_id,
                    "name": node.poi_name,
                    "status": status,
                })

            if status == "confirmed":
                sm = ctx.node_sms.get(node.node_id)
                if sm:
                    await sm.transition_to(NodeState.SUCCESS, ctx)
                    await sm.transition_to(NodeState.COMPLETED_LOCK, ctx)
                node.status = "completed_lock"
                node.completed_lock = True
                return

            elif status == "failed":
                sm = ctx.node_sms.get(node.node_id)
                if sm:
                    await sm.transition_to(NodeState.FAILED, ctx)
                await self._handle_node_failure(ctx, node,
                    result.get("data", {}).get("error", {"code": "booking_failed"}))
                return

            # 排队过长检查
            elif status == "queued":
                estimated = result.get("data", {}).get("estimated_wait_minutes", 0)
                threshold = self._get_queue_threshold(ctx, node)

                if estimated > threshold:
                    await self.event_bus.emit("queue_too_long", ctx, {
                        "node_id": node.node_id,
                        "name": node.poi_name,
                        "queue_time": estimated,
                        "threshold": threshold,
                    })
                    # 触发 fallback
                    sm = ctx.node_sms.get(node.node_id)
                    if sm:
                        await sm.transition_to(NodeState.FAILED, ctx)
                    await self._handle_node_failure(ctx, node,
                        {"code": "queue_too_long", "queue_time": estimated})
                    return

        # 超时
        logger.warning("[Monitor] %s 监控超时", node.node_id)

    # ═══════════════════════════════════════════════════════════
    # Phase 3: 重规划 (Replan)
    # ═══════════════════════════════════════════════════════════

    async def _phase3_replan(self, ctx: ExecutionContext,
                              sentiment: UserSentiment):
        """
        Phase 3 重规划：

        1. 识别已锁定节点（completed_lock + user_pinned）
        2. 标记未执行节点
        3. 调用 replan API
        4. 通过确认网关等待用户确认
        """
        try:
            locked_ids = [
                n.node_id for n in ctx.itinerary.nodes
                if n.completed_lock or n.user_pinned
            ]

            # 获取所有未完成的节点
            pending_ids = [
                n.node_id for n in ctx.itinerary.get_pending_nodes()
            ]

            await self.event_bus.emit("status_update", ctx, {
                "message": "正在根据您的反馈调整行程..."
            })

            # 调用重规划 API
            replan_result = await self.tools.invoke("itinerary_replan", {
                "session_id": ctx.session_id,
                "trigger": {
                    "type": sentiment.type,
                    "description": sentiment.description,
                    "affected_node_id": sentiment.node_id,
                },
                "policy": {
                    "protect_completed": True,
                    "protect_reserved": True,
                    "modify_future_only": True,
                    "locked_nodes": locked_ids,
                    "pending_nodes": pending_ids,
                },
            })

            if not replan_result.get("success"):
                await self.event_bus.emit("status_update", ctx, {
                    "message": "调整失败，保留原方案",
                })
                return

            replan_data = replan_result.get("data", {})

            if not replan_data.get("need_user_confirm"):
                # 不需要确认，直接应用
                self._apply_replan(ctx, replan_data)
                await self.event_bus.emit("replan_applied", ctx, {
                    "itinerary": _d(ctx.itinerary),
                })
                return

            # 需要用户确认
            changed = replan_data.get("changed_nodes", [])
            req_id = await self.confirmation.request(
                type="replan",
                title="行程调整建议",
                description=(
                    f"根据您的反馈「{sentiment.description}」，"
                    f"建议调整 {len(changed)} 个活动"
                ),
                context={
                    "old_nodes": changed,
                    "sentiment": _d(sentiment),
                },
                options=[
                    {"label": "同意调整", "value": "approve", "recommended": True},
                    {"label": "保持原计划", "value": "reject"},
                ],
                callback=lambda approved, mods, _ctx: self._on_replan_response(
                    ctx, approved, mods, replan_data,
                ),
            )

            await self.event_bus.emit("replan_ready", ctx, {
                "request_id": req_id,
                "old_nodes": changed,
                "sentiment": _d(sentiment),
            })

        except Exception as e:
            logger.error("[Phase3] 重规划失败: %s", e, exc_info=True)
            await self.event_bus.emit("status_update", ctx, {
                "message": "调整处理异常，请稍后重试",
            })

    def _on_replan_response(self, ctx: ExecutionContext, approved: bool,
                             modifications: dict, replan_data: dict):
        """用户对重规划方案的响应"""
        if approved:
            self._apply_replan(ctx, replan_data)
            asyncio.create_task(self.event_bus.emit("replan_applied", ctx, {
                "itinerary": _d(ctx.itinerary),
            }))
            # 如果还有未完成的 booking，继续
            if ctx.itinerary_sm.state == ItineraryState.NEEDS_REPLAN:
                asyncio.create_task(
                    ctx.itinerary_sm.transition_to(ItineraryState.PENDING_CONFIRM, ctx)
                )
        else:
            asyncio.create_task(self.event_bus.emit("status_update", ctx, {
                "message": "已保持原计划不变",
            }))

    def _apply_replan(self, ctx: ExecutionContext, replan_data: dict):
        """应用重规划结果"""
        changed = replan_data.get("changed_nodes", [])
        for change in changed:
            old_id = change.get("old_node_id", "")
            old_node = ctx.itinerary.get_node(old_id)
            if old_node:
                # 标记旧节点
                old_node.status = "replanned"
                sm = ctx.node_sms.get(old_id)
                if sm and sm.state != NodeState.COMPLETED_LOCK:
                    asyncio.create_task(
                        sm.transition_to(NodeState.REPLANNED, ctx))

                # 更新为新节点
                old_node.poi_id = change.get("new_poi_id", old_node.poi_id)
                old_node.poi_name = change.get("new_name", old_node.poi_name)
                old_node.status = "planned"
                if change.get("new_scheduled_time"):
                    old_node.scheduled_start = change["new_scheduled_time"]

        ctx.itinerary.summary = self._generate_summary(ctx.itinerary)

    # ═══════════════════════════════════════════════════════════
    # Fallback 系统
    # ═══════════════════════════════════════════════════════════

    async def _handle_node_failure(self, ctx: ExecutionContext,
                                    node: ItineraryNode, error: dict):
        """
        5 级 Fallback：

        Level 1: Retry（临时错误自动重试）
        Level 2: 替代资源（同类 POI 替换）
        Level 3: 跳过非关键节点
        Level 4: 分段重规划（调用 replan API）
        Level 5: 中止（人工处理）
        """
        error_code = error.get("code", "unknown")
        error_class = self._classify_error(error)

        await self.event_bus.emit("node_failed", ctx, {
            "node_id": node.node_id,
            "name": node.poi_name,
            "error": error,
            "fallback_level": error_class[1],
        })

        # Level 1: Retry
        if error_class[0] == "retryable" and error_class[1] == 1:
            logger.info("[Fallback] L1 Retry: %s", node.node_id)
            sm = ctx.node_sms.get(node.node_id)
            if sm:
                await sm.transition_to(NodeState.PENDING, ctx)
            asyncio.create_task(self._book_single_node(ctx, node))
            return

        # Level 2: 替代资源（多候选 + 用户选择）
        if error_class[0] == "alternative" and error_class[1] == 2:
            logger.info("[Fallback] L2 多候选替代: %s", node.node_id)
            alternatives = await self._find_alternatives(ctx, node)
            if alternatives:
                recommended = alternatives[:2]
                more = alternatives[2:]

                # 创建一个结构化选单
                options = []
                for i, alt in enumerate(recommended):
                    options.append({
                        "label": f"⭐ 推荐: {alt.get('name', '')}",
                        "value": alt.get("poi_id", ""),
                        "recommended": i == 0,
                        "description": alt.get("planner_reason", ""),
                    })
                for alt in more[:3]:
                    options.append({
                        "label": f"  {alt.get('name', '')}",
                        "value": alt.get("poi_id", ""),
                        "recommended": False,
                        "description": alt.get("planner_reason", ""),
                    })
                # 加一个"手动调整"选项
                options.append({
                    "label": "我自己看看",
                    "value": "manual",
                    "recommended": False,
                    "description": "暂不替换，我手动调整",
                })

                # 通过确认网关让用户选择
                req_id = await self.confirmation.request(
                    type="alternative_choice",
                    title=f"「{node.poi_name}」需要替换",
                    description=f"由于 {error.get('message', error_code)}，为您找到以下替代方案",
                    context={
                        "node_id": node.node_id,
                        "old_poi_id": node.poi_id,
                        "old_name": node.poi_name,
                        "error": error,
                        "alternatives": [alt for alt in alternatives],
                    },
                    options=options,
                    callback=lambda approved, mods, _ctx: self._on_alternative_response(
                        ctx, node, approved, mods, alternatives,
                    ),
                    timeout_s=120,
                )

                await self.event_bus.emit("alternatives_ready", ctx, {
                    "request_id": req_id,
                    "node_id": node.node_id,
                    "node_name": node.poi_name,
                    "alternatives": alternatives,
                })
                return  # 等待用户选择

        # Level 3: 跳过
        if not self._is_critical_node(node):
            logger.info("[Fallback] L3 跳过: %s", node.node_id)
            ctx.itinerary.remove_node(node.node_id)
            await self.event_bus.emit("node_skipped", ctx, {
                "node_id": node.node_id,
                "name": node.poi_name,
                "reason": error_code,
            })
            return

        # Level 4: 分段重规划（触发确认后 return，等待用户响应）
        logger.info("[Fallback] L4 重规划: %s", node.node_id)
        req_id = await self.confirmation.request(
            type="replan",
            title=f"「{node.poi_name}」需要调整",
            description=f"由于 {error.get('message', error_code)}，该活动无法按计划进行",
            context={"node_id": node.node_id, "error": error},
            callback=lambda approved, mods, _ctx: self._on_replan_response(
                ctx, approved, mods, {"changed_nodes": [{
                    "old_node_id": node.node_id,
                    "old_poi_id": node.poi_id,
                    "new_poi_id": "",
                    "new_name": "",
                }]}),
        )
        return  # 等待用户确认，不继续到 L5

        # Level 5: 中止（最后手段 — 关键节点且所有 Fallback 都失败）
        # （前面 L2-L4 都有 return，能走到这里说明都不是关键节点）
        logger.warning("[Fallback] L5 中止: %s", node.node_id)
        await self.event_bus.emit("session_cancelled", ctx, {
            "reason": f"节点 {node.poi_name} 无法执行且无可用替代，建议重新规划",
        })

    async def _find_alternatives(self, ctx: ExecutionContext,
                                   node: ItineraryNode) -> list[dict]:
        """
        寻找同类替代 POI，返回排序后的多候选列表。

        返回格式:
        [
            {"poi_id": "res_fam_003", "name": "亲子轻食餐厅",
             "category": "restaurant", "planner_reason": "同商圈，排队20分钟，清淡且有儿童椅",
             "queue_time_min": 20, "distance_km": 0.8, "rating": 4.5, "avg_price": 78},
            ...
        ]
        """
        candidates = ctx.raw_candidates
        used_ids = {n.poi_id for n in ctx.itinerary.nodes if n.poi_id != node.poi_id}
        results = []
        node_cat = node.category.value

        # 第一优先：同类型未使用
        for c in candidates:
            poi_id = c.get("poi_id", "")
            if poi_id == node.poi_id or poi_id in used_ids:
                continue
            if c.get("category", "") == node_cat:
                results.append({
                    "poi_id": poi_id,
                    "name": c.get("name", ""),
                    "category": c.get("category", ""),
                    "planner_reason": self._generate_alt_reason(c),
                    "queue_time_min": c.get("queue_time_min", 0),
                    "distance_km": c.get("distance_km", 0),
                    "rating": c.get("rating", 0),
                    "avg_price": c.get("avg_price", 0),
                    "tags": c.get("tags", []),
                })

        # 第二优先：任何其他未使用的候选
        for c in candidates:
            poi_id = c.get("poi_id", "")
            if poi_id == node.poi_id or poi_id in used_ids:
                continue
            if poi_id not in [r["poi_id"] for r in results]:
                results.append({
                    "poi_id": poi_id,
                    "name": c.get("name", ""),
                    "category": c.get("category", ""),
                    "planner_reason": self._generate_alt_reason(c),
                    "queue_time_min": c.get("queue_time_min", 0),
                    "distance_km": c.get("distance_km", 0),
                    "rating": c.get("rating", 0),
                    "avg_price": c.get("avg_price", 0),
                    "tags": c.get("tags", []),
                })

        # 按评分排序（假设 rating 越高越好，queue 越短越好）
        results.sort(key=lambda x: (x["rating"] * 10 - x["queue_time_min"]), reverse=True)
        return results

    async def _on_alternative_response(self, ctx: ExecutionContext,
                                        node: ItineraryNode,
                                        approved: bool, modifications: dict,
                                        alternatives: list[dict]):
        """
        用户对替代方案的选择响应。
        - approved=True 且 modifications 包含选中的 poi_id → 应用替换
        - approved=False → 保持原节点（标记 fallback_failed）
        """
        if not approved:
            logger.info("[Replan] 用户拒绝替代方案，保持原节点: %s", node.node_id)
            node.fallback_failed = True
            await self.event_bus.emit("status_update", ctx, {
                "message": f"已保持「{node.poi_name}」不变",
            })
            return

        # 用户选择了某个替代
        selected_poi_id = modifications.get("value", "")
        if not selected_poi_id or selected_poi_id == "manual":
            logger.info("[Replan] 用户选择手动调整: %s", node.node_id)
            await self.event_bus.emit("status_update", ctx, {
                "message": "请手动调整行程",
            })
            return

        # 找到选中的替代
        alt = next((a for a in alternatives if a["poi_id"] == selected_poi_id), None)
        if not alt:
            logger.warning("[Replan] 用户选择的替代不存在: %s", selected_poi_id)
            return

        # 应用替换
        old_name = node.poi_name
        node.poi_id = alt["poi_id"]
        node.poi_name = alt["name"]
        node.status = "planned"
        node.booking_ref = None
        node.booking_status = None

        # 状态机: FAILED → PENDING（可重试）
        sm = ctx.node_sms.get(node.node_id)
        if sm:
            try:
                await sm.transition_to(NodeState.PENDING, ctx)
            except InvalidTransitionError:
                # 如果不在 FAILED 状态（如 AWAITING_CONFIRMATION），先回退
                pass
        asyncio.create_task(self._book_single_node(ctx, node))

        await self.event_bus.emit("node_replaced", ctx, {
            "old_node_id": node.node_id,
            "old_name": old_name,
            "new_node": alt,
        })
        await self.event_bus.emit("status_update", ctx, {
            "message": f"已替换为「{alt['name']}」",
        })
        logger.info("[Replan] 用户选择替代: %s → %s", old_name, alt["name"])

    @staticmethod
    def _generate_alt_reason(candidate: dict) -> str:
        """为替代候选生成推荐理由"""
        parts = []
        queue = candidate.get("queue_time_min", 0)
        if queue == 0:
            parts.append("无需排队")
        elif queue <= 15:
            parts.append(f"排队仅{queue}分钟")
        else:
            parts.append(f"排队{queue}分钟")

        dist = candidate.get("distance_km", 0)
        if 0 < dist <= 1:
            parts.append("步行可达")
        elif dist <= 3:
            parts.append("距离近")

        tags = candidate.get("tags", [])
        if "亲子" in tags:
            parts.append("亲子友好")
        if "低卡" in tags or "轻食" in tags:
            parts.append("健康低卡")
        if child_friendly := candidate.get("child_friendly"):
            parts.append("适合儿童")

        return "，".join(parts) if parts else "同类推荐"

    # ═══════════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════════

    ERROR_CLASSIFICATION = {
        "timeout": ("retryable", 1),
        "network_error": ("retryable", 1),
        "rate_limited": ("retryable_backoff", 1),
        "internal_error": ("retryable", 1),
        "resource_unavailable": ("alternative", 2),
        "booking_rejected": ("alternative", 2),
        "queue_too_long": ("alternative", 2),
        "weather_alert": ("replan_segment", 4),
        "invalid_input": ("fail_fast", 5),
        "not_found": ("fail_fast", 5),
        "session_expired": ("fail_fast", 5),
        "booking_failed": ("alternative", 2),
    }

    def _classify_error(self, error: dict) -> tuple[str, int]:
        code = error.get("code", "unknown")
        return self.ERROR_CLASSIFICATION.get(code, ("fail_fast", 5))

    def _get_ctx(self, session_id: str) -> ExecutionContext:
        ctx = self.sessions.get(session_id)
        if not ctx:
            raise ValueError(f"会话不存在: {session_id}")
        return ctx

    def _calc_progress(self, ctx: ExecutionContext) -> float:
        if not ctx.itinerary or not ctx.itinerary.nodes:
            return 0.0
        states = []
        for node in ctx.itinerary.nodes:
            sm = ctx.node_sms.get(node.node_id)
            if sm:
                states.append(sm.state)
        if not states:
            return 0.0
        terminal = sum(1 for s in states if s in (
            NodeState.COMPLETED_LOCK, NodeState.FAILED, NodeState.REPLANNED))
        return round(terminal / len(states) * 100, 1)

    async def _on_watch_alert(self, ctx: Any, event: dict):
        """后台监控告警处理器 — 触发重规划"""
        data = event.get("data", {})
        alert = data.get("alert", {})
        session_id = data.get("session_id", "")
        node_id = data.get("node_id", "")
        severity = alert.get("severity", "warning")
        watch_type = alert.get("type", "")

        if not session_id or session_id not in self.sessions:
            return

        session_ctx = self.sessions[session_id]
        logger.info("[WatchAlert] %s: %s (severity=%s)",
                    watch_type, alert.get("message", ""), severity)

        # 只有 critical 级别的告警触发重规划（如暴雨、预约失败）
        if severity == "critical" and watch_type in ("weather", "booking", "availability"):
            sentiment = UserSentiment(
                type="weather_uncomfortable" if watch_type == "weather" else "other",
                description=alert.get("message", ""),
                node_id=node_id or "",
            )
            await self.handle_user_sentiment(session_id, sentiment)

        # warning 级别通过事件展示给用户（如排队过长）
        elif severity == "warning":
            await self.event_bus.emit("status_update", session_ctx, {
                "message": alert.get("message", ""),
            })

    def _build_watch_configs(self, ctx: ExecutionContext) -> list[WatchConfig]:
        """根据行程生成后台监控配置"""
        configs = []
        threshold = self._get_queue_threshold(ctx, None)

        for node in ctx.itinerary.nodes:
            # 排队监控（餐厅节点）
            if node.category == NodeCategory.RESTAURANT:
                configs.append(WatchConfig(
                    type=WatchType.QUEUE,
                    poi_id=node.poi_id,
                    node_id=node.node_id,
                    threshold={"estimated_wait_min_gt": threshold},
                    poll_interval_s=10,
                    auto_resolve=True,
                ))

            # 预约状态监控（需预约的节点）
            if node.needs_booking and node.booking_ref:
                configs.append(WatchConfig(
                    type=WatchType.BOOKING,
                    poi_id=node.booking_ref,
                    node_id=node.node_id,
                    threshold={},
                    poll_interval_s=15,
                ))

        # 全局天气监控（只需一个）
        if ctx.location:
            configs.append(WatchConfig(
                type=WatchType.WEATHER,
                location=f"{ctx.location.get('city', '北京')}{ctx.location.get('district', '')}",
                threshold={"rain_level_in": ["暴雨", "大暴雨", "heavy_rain", "storm"]},
                poll_interval_s=30,
                auto_resolve=True,
            ))

        return configs

    def _get_queue_threshold(self, ctx: ExecutionContext,
                              node: ItineraryNode = None) -> int:
        if ctx.user_context.mode == ModeType.FULL:
            return 30
        return 45

    def _is_critical_node(self, node: ItineraryNode) -> bool:
        return node.category in (NodeCategory.MAIN_ACTIVITY, NodeCategory.RESTAURANT)

    def _validate_feasibility(self, ctx: ExecutionContext):
        """
        行程合理性校验（PRD 第五章 6 条规则）

        规则一：通勤/游玩时间比例（单程 ≤30%，往返 ≤40%）
        规则二：核心活动时间保障（≥45min，含儿童 ≥60min）
        规则三：交通方式与距离匹配（距离限制、步行风险标注）
        规则四：意图确认优先于方案优化（在 LLM 层处理，这里标记）
        规则五：节奏合理性（节点数 ≤6、缓冲时间、儿童 20:00 前结束）
        规则六：季节与天气适配（雨天/极端天气禁止户外）
        """
        fc = ctx.itinerary.feasibility_check
        nodes = ctx.itinerary.nodes
        risk_flags = []
        has_child = "child" in str(getattr(ctx.user_context, "companions_text", "")).lower()
        # 也检查 companions 字段
        if hasattr(ctx.user_context, "companions") and ctx.user_context.companions:
            companion_str = " ".join(str(c) for c in ctx.user_context.companions)
            has_child = has_child or any(k in companion_str for k in ("child", "儿童", "小孩"))
        mode = ctx.user_context.mode.value if ctx.user_context else "light_managed"

        if len(nodes) < 1:
            fc.passed = False
            return

        # ── 规则五：节奏合理性 - 节点数检查 ──
        if len(nodes) > 6:
            risk_flags.append(f"节点数 {len(nodes)} 超过 6 个上限")
            fc.passed = False

        # ── 时间相关检查（需要起止时间）──
        first_time = nodes[0].scheduled_start
        last_end = nodes[-1].scheduled_end

        if first_time and last_end:
            try:
                fh, fm = map(int, first_time.split(":"))
                eh, em = map(int, last_end.split(":"))
                span = (eh * 60 + em) - (fh * 60 + fm)
                activity = sum(n.duration_min for n in nodes if n.duration_min > 0)
                commute = max(0, span - activity)
                commute_ratio = commute / span if span > 0 else 0

                # 规则一：通勤/游玩时间比例
                ratio_pct = commute_ratio * 100
                fc.commute_ratio = f"{ratio_pct:.0f}%"
                if commute_ratio > 0.4:
                    risk_flags.append(f"通勤占比 {ratio_pct:.0f}% > 40% 上限")
                    fc.passed = False
                elif commute_ratio > 0.3:
                    risk_flags.append(f"通勤占比 {ratio_pct:.0f}% 接近上限")

                # 规则二：核心活动时间保障
                for n in nodes:
                    if n.category in (NodeCategory.MAIN_ACTIVITY,) and n.duration_min < 45:
                        risk_flags.append(f"「{n.poi_name}」活动时间 {n.duration_min}min < 45min")
                        fc.passed = False
                    if has_child and n.duration_min > 0 and n.duration_min < 60:
                        risk_flags.append(f"「{n.poi_name}」儿童活动时间 {n.duration_min}min < 60min")
                fc.activity_time_ok = not any(
                    "活动时间" in f for f in risk_flags
                )

                # 规则五：儿童结束时间检查
                if has_child and (eh > 20 or (eh == 20 and em > 0)):
                    risk_flags.append(f"结束时间 {last_end} 超过 20:00（儿童场景）")
                    fc.passed = False

            except (ValueError, IndexError):
                pass

        # 规则五：节点间缓冲时间
        for i in range(len(nodes) - 1):
            cur = nodes[i]
            nxt = nodes[i + 1]
            if cur.scheduled_end and nxt.scheduled_start:
                try:
                    ch, cm = map(int, cur.scheduled_end.split(":"))
                    nh, nm = map(int, nxt.scheduled_start.split(":"))
                    gap = (nh * 60 + nm) - (ch * 60 + cm)
                    if gap <= 0:
                        risk_flags.append(f"「{cur.poi_name}」→「{nxt.poi_name}」时间重叠")
                    elif gap < 5 and has_child:
                        risk_flags.append(f"「{cur.poi_name}」→「{nxt.poi_name}」缓冲仅 {gap}min，建议 ≥5min")
                except (ValueError, IndexError):
                    pass

        # 规则三：交通方式与距离（简化 — 标记风险）
        # 距离检查需要 route_check API 数据（通过 ctx.routes），这里做标签级检查
        has_outdoor = any(
            "户外" in n.tags or "公园" in n.tags or "散步" in n.tags
            for n in nodes
        )
        if has_child and has_outdoor:
            risk_flags.append("含儿童场景，户外活动建议打车前往")
            fc.transport_match = False

        # 规则六：天气适配
        weather = ctx.weather.get("weather", "") if ctx.weather else ""
        if weather in ("暴雨", "大暴雨", "heavy_rain", "storm"):
            outdoor_nodes = [n for n in nodes if "户外" in str(n.tags)]
            if outdoor_nodes:
                for n in outdoor_nodes:
                    risk_flags.append(f"「{n.poi_name}」为户外活动，当前 {weather} 不适宜")

        # 汇总
        fc.passed = fc.passed and len([f for f in risk_flags if "接近" not in f]) == 0
        if risk_flags and hasattr(ctx, "event_bus") and ctx.event_bus:
            asyncio.create_task(ctx.event_bus.emit("feasibility_risks", ctx, {
                "passed": fc.passed,
                "risk_flags": risk_flags,
            }))

    def _apply_modification(self, ctx: ExecutionContext, mod: ItineraryModification):
        """应用用户编辑"""
        if mod.type == "replace" and mod.node_id:
            old_node = ctx.itinerary.get_node(mod.node_id)
            if old_node and mod.new_resource:
                for key, value in mod.new_resource.items():
                    if hasattr(old_node, key):
                        setattr(old_node, key, value)

        elif mod.type == "delete" and mod.node_id:
            ctx.itinerary.remove_node(mod.node_id)

        elif mod.type == "insert" and mod.target_node_id and mod.new_resource:
            new_node = ItineraryNode(**mod.new_resource)
            ctx.itinerary.insert_node_after(mod.target_node_id, new_node)
            ctx.node_sms[new_node.node_id] = create_node_fsm()

    def _pick_best(self, candidates: list[dict],
                    preferred_categories: list[str]) -> dict:
        """从评分候选中选最佳"""
        scored = [c for c in candidates if c.get("category", "") in preferred_categories]
        if not scored:
            scored = candidates
        scored.sort(key=lambda c: c.get("score", 0), reverse=True)
        return scored[0] if scored else {}

    @staticmethod
    def _generate_summary(itinerary: ItineraryData) -> str:
        n = len(itinerary.nodes)
        names = [n.poi_name for n in itinerary.nodes[:3]]
        return f"共 {n} 个活动: {' → '.join(names)}，预计 {itinerary.total_duration_min} 分钟"

    @staticmethod
    def _map_category(cat: str) -> NodeCategory:
        mapping = {
            "main_activity": NodeCategory.MAIN_ACTIVITY,
            "restaurant": NodeCategory.RESTAURANT,
            "optional_activity": NodeCategory.OPTIONAL_ACTIVITY,
            "indoor_playground": NodeCategory.MAIN_ACTIVITY,
            "museum": NodeCategory.MAIN_ACTIVITY,
            "exhibition": NodeCategory.MAIN_ACTIVITY,
            "outdoor_walk": NodeCategory.OPTIONAL_ACTIVITY,
            "entertainment": NodeCategory.MAIN_ACTIVITY,
            "indoor_game": NodeCategory.MAIN_ACTIVITY,
            "shopping": NodeCategory.OPTIONAL_ACTIVITY,
            "park": NodeCategory.OPTIONAL_ACTIVITY,
            "street_food": NodeCategory.OPTIONAL_ACTIVITY,
        }
        return mapping.get(cat, NodeCategory.OPTIONAL_ACTIVITY)

    @staticmethod
    def _get_mock_walk() -> dict:
        return {
            "poi_id": "walk_001",
            "name": "商场轻松散步区",
            "category": "shopping",
            "estimated_duration_min": 40,
        }
