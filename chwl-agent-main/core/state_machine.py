"""
通用状态机引擎 (FSM)

提供：
- 泛型 StateMachine，支持 itinerary 级和 node 级的状态转换
- 守卫条件 (guard)、进入/离开动作 (on_enter/on_exit)
- 状态变更事件通知
"""
from __future__ import annotations

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

# ─── 状态定义 ─────────────────────────────────────────────────

class ItineraryState(str, Enum):
    """行程级状态机"""
    INIT = "init"
    DRAFT = "draft"
    PENDING_CONFIRM = "pending_confirm"
    EXECUTING = "executing"
    COMPLETED = "completed"
    NEEDS_REPLAN = "needs_replan"
    CANCELLED = "cancelled"


class NodeState(str, Enum):
    """节点级状态机"""
    PLANNED = "planned"              # 规划完成，待履约
    PENDING = "pending"              # 等待执行
    PROCESSING = "processing"        # 正在履约（booking 中）
    SUCCESS = "success"              # 履约成功
    FAILED = "failed"                # 履约失败
    REPLANNED = "replanned"          # 已被 replan 替换
    COMPLETED_LOCK = "completed_lock"  # 已完成锁定（不可修改）
    USER_PINNED = "user_pinned"      # 用户固定（绝对保护）
    SOFT_LOCK = "soft_lock"          # 软锁（修改需确认）
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 等待用户确认


# ─── 转换定义 ─────────────────────────────────────────────────

StateT = TypeVar("StateT", bound=Enum)


@dataclass
class Transition(Generic[StateT]):
    """一条状态转换规则"""
    from_state: StateT
    to_state: StateT
    guard: Callable[[Any], bool] = lambda ctx: True
    on_exit: Callable[[Any], None] = lambda ctx: None
    on_enter: Callable[[Any], None] = lambda ctx: None
    label: str = ""


@dataclass
class StateChangeEvent:
    """状态变更事件"""
    machine_name: str
    from_state: Any
    to_state: Any
    context: Any


# ─── 异常 ─────────────────────────────────────────────────────

class InvalidTransitionError(Exception):
    """无效的状态转换"""
    def __init__(self, machine_name: str, from_state: Any, to_state: Any, reason: str = ""):
        self.machine_name = machine_name
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(f"[{machine_name}] {from_state} -> {to_state} 不合法: {reason}")


# ─── FSM ──────────────────────────────────────────────────────

class StateMachine(Generic[StateT]):
    """通用有限状态机"""

    def __init__(self, name: str, initial_state: StateT, transitions: list[Transition[StateT]]):
        self.name = name
        self.state = initial_state
        self._transitions = transitions
        self._listeners: list[Callable[[StateChangeEvent], Any]] = []
        self._valid_states = {t.from_state for t in transitions} | {t.to_state for t in transitions}

    # ── 查询 ──

    def can_transition_to(self, target: StateT, context: Any = None) -> bool:
        return any(
            t.from_state == self.state
            and t.to_state == target
            and t.guard(context)
            for t in self._transitions
        )

    def is_terminal(self) -> bool:
        """是否处于终态（没有出边）"""
        return not any(t.from_state == self.state for t in self._transitions)

    def possible_transitions(self) -> list[Transition]:
        """返回当前可用的转换列表"""
        return [t for t in self._transitions if t.from_state == self.state and t.guard(None)]

    # ── 转换 ──

    async def transition_to(self, target: StateT, context: Any = None) -> StateChangeEvent:
        """执行状态转换（异步，因为 on_enter/on_exit 可能是 async）"""
        matched = [t for t in self._transitions
                   if t.from_state == self.state and t.to_state == target]

        if not matched:
            raise InvalidTransitionError(
                self.name, self.state, target,
                f"无匹配转换规则（可用: {[t.to_state.value for t in self.possible_transitions()]})"
            )

        transition = matched[0]

        if not transition.guard(context):
            raise InvalidTransitionError(
                self.name, self.state, target, "守卫条件不通过"
            )

        old_state = self.state

        # on_exit
        result = transition.on_exit(context)
        if hasattr(result, "__await__"):
            await result

        self.state = target

        # on_enter
        result = transition.on_enter(context)
        if hasattr(result, "__await__"):
            await result

        # 触发事件
        event = StateChangeEvent(self.name, old_state, target, context)
        await self._emit(event)
        logger.debug("[%s] %s -> %s ✓", self.name, old_state.value, target.value)
        return event

    # ── 事件 ──

    def on_change(self, listener: Callable[[StateChangeEvent], Any]):
        self._listeners.append(listener)

    async def _emit(self, event: StateChangeEvent):
        for listener in self._listeners:
            result = listener(event)
            if hasattr(result, "__await__"):
                await result


# ─── 工厂函数 ─────────────────────────────────────────────────

def create_itinerary_fsm() -> StateMachine[ItineraryState]:
    """创建行程级状态机"""
    return StateMachine(
        name="itinerary",
        initial_state=ItineraryState.INIT,
        transitions=[
            Transition(ItineraryState.INIT, ItineraryState.DRAFT, label="用户输入"),
            Transition(ItineraryState.DRAFT, ItineraryState.PENDING_CONFIRM, label="方案就绪"),
            Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.DRAFT,
                       guard=lambda ctx: bool(ctx and getattr(ctx, 'has_user_edits', False)),
                       label="用户编辑"),
            Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.EXECUTING, label="一键安排"),
            Transition(ItineraryState.EXECUTING, ItineraryState.COMPLETED, label="全部履约完成"),
            Transition(ItineraryState.EXECUTING, ItineraryState.NEEDS_REPLAN, label="部分失败"),
            Transition(ItineraryState.NEEDS_REPLAN, ItineraryState.PENDING_CONFIRM, label="重规划就绪"),
            Transition(ItineraryState.NEEDS_REPLAN, ItineraryState.COMPLETED, label="无需重规划"),
            Transition(ItineraryState.DRAFT, ItineraryState.CANCELLED, label="用户取消"),
            Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.CANCELLED, label="用户取消"),
            Transition(ItineraryState.EXECUTING, ItineraryState.CANCELLED, label="用户取消"),
        ]
    )


def create_node_fsm() -> StateMachine[NodeState]:
    """创建节点级状态机"""
    return StateMachine(
        name="node",
        initial_state=NodeState.PLANNED,
        transitions=[
            Transition(NodeState.PLANNED, NodeState.PENDING, label="进入履约队列"),
            Transition(NodeState.PENDING, NodeState.PROCESSING, label="开始履约"),
            Transition(NodeState.PROCESSING, NodeState.SUCCESS, label="履约成功"),
            Transition(NodeState.PROCESSING, NodeState.FAILED, label="履约失败"),
            Transition(NodeState.PROCESSING, NodeState.AWAITING_CONFIRMATION, label="需用户确认"),
            Transition(NodeState.AWAITING_CONFIRMATION, NodeState.PROCESSING, label="用户确认继续"),
            Transition(NodeState.AWAITING_CONFIRMATION, NodeState.FAILED, label="用户拒绝/超时"),
            Transition(NodeState.SUCCESS, NodeState.COMPLETED_LOCK, label="锁定完成"),
            Transition(NodeState.FAILED, NodeState.REPLANNED, label="已替换"),
            Transition(NodeState.FAILED, NodeState.PENDING, label="重试"),
            Transition(NodeState.PENDING, NodeState.FAILED, label="取消"),
            Transition(NodeState.PROCESSING, NodeState.PENDING, label="重新处理"),
            Transition(NodeState.PLANNED, NodeState.USER_PINNED, label="用户固定"),
            Transition(NodeState.USER_PINNED, NodeState.PLANNED, label="取消固定"),
            Transition(NodeState.PROCESSING, NodeState.SOFT_LOCK, label="资源占用软锁"),
            Transition(NodeState.SOFT_LOCK, NodeState.COMPLETED_LOCK, label="软锁确认完成"),
            Transition(NodeState.SOFT_LOCK, NodeState.FAILED, label="软锁取消/释放"),
        ]
    )
