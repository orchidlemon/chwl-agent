"""State machines and safe transition helpers for sessions and itinerary nodes.

Adapted from the teammate backend's core/state_machine.py. The current public
contract keeps phase strings and frontend node flags, while this module provides
strict transition checks plus soft fallback results so user flows do not crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, TypeVar


class ItineraryState(str, Enum):
    INIT = "init"
    DRAFT = "draft"
    PENDING_CONFIRM = "pending_confirm"
    EXECUTING = "executing"
    COMPLETED = "completed"
    NEEDS_REPLAN = "needs_replan"
    CANCELLED = "cancelled"


class NodeState(str, Enum):
    PLANNED = "planned"
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    REPLANNED = "replanned"
    COMPLETED_LOCK = "completed_lock"
    USER_PINNED = "user_pinned"
    SOFT_LOCK = "soft_lock"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class InvalidTransitionError(Exception):
    def __init__(self, machine_name: str, from_state: Any, to_state: Any, reason: str = ""):
        self.machine_name = machine_name
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(f"[{machine_name}] {from_state} -> {to_state} is invalid. {reason}")


StateT = TypeVar("StateT", bound=Enum)


@dataclass
class Transition(Generic[StateT]):
    from_state: StateT
    to_state: StateT
    guard: Callable[[Any], bool] = lambda ctx: True
    label: str = ""


@dataclass
class StateChangeEvent:
    machine_name: str
    from_state: Any
    to_state: Any
    context: Any = None


@dataclass
class TransitionResult:
    ok: bool
    from_state: str
    requested_state: str
    to_state: str
    fallback_state: str | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


class StateMachine(Generic[StateT]):
    def __init__(self, name: str, initial_state: StateT, transitions: list[Transition[StateT]]):
        self.name = name
        self.state = initial_state
        self._transitions = transitions

    def can_transition_to(self, target: StateT, context: Any = None) -> bool:
        return any(
            transition.from_state == self.state
            and transition.to_state == target
            and transition.guard(context)
            for transition in self._transitions
        )

    def possible_transitions(self) -> list[Transition[StateT]]:
        return [transition for transition in self._transitions if transition.from_state == self.state]

    def transition_to(self, target: StateT, context: Any = None) -> StateChangeEvent:
        matched = [
            transition for transition in self._transitions
            if transition.from_state == self.state and transition.to_state == target
        ]
        if not matched:
            available = [transition.to_state.value for transition in self.possible_transitions()]
            raise InvalidTransitionError(self.name, self.state, target, f"available={available}")
        transition = matched[0]
        if not transition.guard(context):
            raise InvalidTransitionError(self.name, self.state, target, "guard failed")
        old_state = self.state
        self.state = target
        return StateChangeEvent(self.name, old_state, target, context)


_PHASE_TO_STATE = {
    "gathering": ItineraryState.INIT,
    "confirming": ItineraryState.PENDING_CONFIRM,
    "planning": ItineraryState.DRAFT,
    "monitoring": ItineraryState.EXECUTING,
    "needs_replan": ItineraryState.NEEDS_REPLAN,
    "completed": ItineraryState.COMPLETED,
    "cancelled": ItineraryState.CANCELLED,
}

_STATE_TO_PHASE = {
    ItineraryState.INIT: "gathering",
    ItineraryState.PENDING_CONFIRM: "confirming",
    ItineraryState.DRAFT: "planning",
    ItineraryState.EXECUTING: "monitoring",
    ItineraryState.NEEDS_REPLAN: "needs_replan",
    ItineraryState.COMPLETED: "completed",
    ItineraryState.CANCELLED: "cancelled",
}

_ALLOWED_PHASE_TRANSITIONS = {
    "gathering": {"gathering", "confirming", "planning", "cancelled"},
    "confirming": {"confirming", "gathering", "planning", "cancelled"},
    "planning": {"planning", "monitoring", "needs_replan", "cancelled"},
    "monitoring": {"monitoring", "planning", "needs_replan", "completed", "cancelled"},
    "needs_replan": {"needs_replan", "planning", "monitoring", "cancelled"},
    "completed": {"completed", "cancelled"},
    "cancelled": {"cancelled"},
}


def create_itinerary_fsm(initial_phase: str = "gathering") -> StateMachine[ItineraryState]:
    initial_state = phase_to_itinerary_state(initial_phase)
    transitions = [
        Transition(ItineraryState.INIT, ItineraryState.DRAFT, label="start planning"),
        Transition(ItineraryState.INIT, ItineraryState.PENDING_CONFIRM, label="clarify"),
        Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.INIT, label="needs more input"),
        Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.DRAFT, label="confirmed"),
        Transition(ItineraryState.DRAFT, ItineraryState.EXECUTING, label="plan ready"),
        Transition(ItineraryState.DRAFT, ItineraryState.NEEDS_REPLAN, label="planning failed"),
        Transition(ItineraryState.EXECUTING, ItineraryState.DRAFT, label="user requested replan"),
        Transition(ItineraryState.EXECUTING, ItineraryState.NEEDS_REPLAN, label="exception requires replan"),
        Transition(ItineraryState.NEEDS_REPLAN, ItineraryState.DRAFT, label="replan started"),
        Transition(ItineraryState.NEEDS_REPLAN, ItineraryState.EXECUTING, label="fallback continue"),
        Transition(ItineraryState.EXECUTING, ItineraryState.COMPLETED, label="all completed"),
        Transition(ItineraryState.INIT, ItineraryState.CANCELLED, label="cancel"),
        Transition(ItineraryState.PENDING_CONFIRM, ItineraryState.CANCELLED, label="cancel"),
        Transition(ItineraryState.DRAFT, ItineraryState.CANCELLED, label="cancel"),
        Transition(ItineraryState.EXECUTING, ItineraryState.CANCELLED, label="cancel"),
        Transition(ItineraryState.NEEDS_REPLAN, ItineraryState.CANCELLED, label="cancel"),
        Transition(ItineraryState.COMPLETED, ItineraryState.CANCELLED, label="archive"),
    ]
    fsm = StateMachine("itinerary", ItineraryState.INIT, transitions)
    fsm.state = initial_state
    return fsm


def create_node_fsm(initial_state: NodeState = NodeState.PLANNED) -> StateMachine[NodeState]:
    transitions = [
        Transition(NodeState.PLANNED, NodeState.PENDING, label="queue fulfillment"),
        Transition(NodeState.PENDING, NodeState.PROCESSING, label="start fulfillment"),
        Transition(NodeState.PROCESSING, NodeState.SUCCESS, label="success"),
        Transition(NodeState.PROCESSING, NodeState.FAILED, label="failed"),
        Transition(NodeState.PROCESSING, NodeState.AWAITING_CONFIRMATION, label="needs confirmation"),
        Transition(NodeState.AWAITING_CONFIRMATION, NodeState.PROCESSING, label="continue"),
        Transition(NodeState.AWAITING_CONFIRMATION, NodeState.FAILED, label="rejected"),
        Transition(NodeState.SUCCESS, NodeState.COMPLETED_LOCK, label="completed"),
        Transition(NodeState.FAILED, NodeState.REPLANNED, label="replanned"),
        Transition(NodeState.FAILED, NodeState.PENDING, label="retry"),
        Transition(NodeState.PLANNED, NodeState.USER_PINNED, label="pin"),
        Transition(NodeState.USER_PINNED, NodeState.PLANNED, label="unpin"),
        Transition(NodeState.PROCESSING, NodeState.SOFT_LOCK, label="resource held"),
        Transition(NodeState.PENDING, NodeState.SOFT_LOCK, label="resource held"),
        Transition(NodeState.PLANNED, NodeState.SOFT_LOCK, label="resource held"),
        Transition(NodeState.SOFT_LOCK, NodeState.COMPLETED_LOCK, label="finish held node"),
        Transition(NodeState.SOFT_LOCK, NodeState.FAILED, label="release held node"),
        Transition(NodeState.PLANNED, NodeState.COMPLETED_LOCK, label="manual checkin"),
        Transition(NodeState.USER_PINNED, NodeState.COMPLETED_LOCK, label="manual checkin"),
    ]
    fsm = StateMachine("node", NodeState.PLANNED, transitions)
    fsm.state = initial_state
    return fsm


def phase_to_itinerary_state(phase: str) -> ItineraryState:
    return _PHASE_TO_STATE.get(phase, ItineraryState.INIT)


def itinerary_state_to_phase(state: ItineraryState | str) -> str:
    if isinstance(state, str):
        state = ItineraryState(state)
    return _STATE_TO_PHASE[state]


def safe_phase_transition(
    current_phase: str,
    target_phase: str,
    context: dict[str, Any] | None = None,
) -> TransitionResult:
    context = context or {}
    current = current_phase or "gathering"
    target = target_phase or current
    allowed = _ALLOWED_PHASE_TRANSITIONS.get(current)

    if target not in _PHASE_TO_STATE:
        return TransitionResult(False, current, target, current, current, f"unknown target phase: {target}")
    if allowed and target in allowed:
        return TransitionResult(True, current, target, target)

    fallback = fallback_phase(current, target, context)
    return TransitionResult(
        False,
        current,
        target,
        fallback,
        fallback,
        f"blocked invalid phase transition: {current} -> {target}",
    )


def fallback_phase(current_phase: str, target_phase: str, context: dict[str, Any]) -> str:
    has_itinerary = bool(context.get("has_itinerary"))
    pending_exception = bool(context.get("pending_exception"))
    all_completed = bool(context.get("all_completed"))

    if current_phase == "completed":
        return "completed"
    if current_phase == "cancelled":
        return "cancelled"
    if all_completed:
        return "completed"
    if pending_exception:
        return "needs_replan" if target_phase == "planning" else current_phase
    if current_phase == "gathering" and target_phase == "monitoring":
        return "planning" if has_itinerary else "confirming"
    if current_phase == "confirming" and target_phase == "monitoring":
        return "planning"
    if current_phase == "planning" and target_phase in {"gathering", "confirming"}:
        return "planning"
    if current_phase == "monitoring" and target_phase == "gathering":
        return "monitoring"
    if target_phase == "monitoring" and not has_itinerary:
        return "planning"
    return current_phase


def node_dict_to_state(node: dict[str, Any]) -> NodeState:
    if node.get("completed_lock") or node.get("_checked"):
        return NodeState.COMPLETED_LOCK
    if node.get("user_pinned") or node.get("pinned"):
        return NodeState.USER_PINNED
    if node.get("soft_lock") or node.get("locked"):
        return NodeState.SOFT_LOCK

    status = str(node.get("node_state") or node.get("status") or "planned")
    aliases = {
        "done": NodeState.SOFT_LOCK,
        "completed": NodeState.COMPLETED_LOCK,
        "optional": NodeState.PLANNED,
    }
    try:
        return aliases.get(status, NodeState(status))
    except ValueError:
        return NodeState.PLANNED


def can_transition_node(node: dict[str, Any], target_state: NodeState) -> bool:
    current = node_dict_to_state(node)
    if current == target_state:
        return True
    fsm = create_node_fsm(current)
    return fsm.can_transition_to(target_state)


def apply_node_state(node: dict[str, Any], target_state: NodeState) -> tuple[dict[str, Any], TransitionResult]:
    current = node_dict_to_state(node)
    if current != target_state:
        fsm = create_node_fsm(current)
        if not fsm.can_transition_to(target_state):
            return dict(node), TransitionResult(
                False,
                current.value,
                target_state.value,
                current.value,
                current.value,
                f"blocked invalid node transition: {current.value} -> {target_state.value}",
            )

    updated = dict(node)
    updated["node_state"] = target_state.value

    if target_state == NodeState.PLANNED:
        updated.update({"user_pinned": False, "pinned": False})
        if updated.get("status") not in ("optional",):
            updated["status"] = "planned"
    elif target_state == NodeState.USER_PINNED:
        updated.update({"user_pinned": True, "pinned": True})
    elif target_state == NodeState.SOFT_LOCK:
        updated.update({"soft_lock": True, "locked": True, "status": "done"})
    elif target_state == NodeState.COMPLETED_LOCK:
        updated.update({"completed_lock": True, "_checked": True, "status": "completed"})
    elif target_state == NodeState.FAILED:
        updated["status"] = "failed"
    elif target_state == NodeState.REPLANNED:
        updated["status"] = "replanned"

    return updated, TransitionResult(True, current.value, target_state.value, target_state.value)
