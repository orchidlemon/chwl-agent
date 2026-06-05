"""Shared backend data models with frontend compatibility adapters.

These models are adapted from the teammate backend's core/models.py, but keep
conversion helpers for the current React contract. The public API still returns
frontend-shaped dicts such as id, poiId, timeStart, and timeEnd.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SceneType(str, Enum):
    FAMILY = "family"
    FRIENDS = "friends"
    COUPLE = "couple"
    SOLO = "solo"


class CompanionType(str, Enum):
    ADULT_MALE = "adult_male"
    ADULT_FEMALE = "adult_female"
    CHILD_3_6 = "child_3_6"
    CHILD_7_12 = "child_7_12"
    ELDERLY = "elderly"


class NodeCategory(str, Enum):
    MAIN_ACTIVITY = "main_activity"
    RESTAURANT = "restaurant"
    OPTIONAL_ACTIVITY = "optional_activity"
    TRANSPORT = "transport"
    REST = "rest"
    LIGHT = "light"


class ResourceType(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    PARKING = "parking"
    TRANSPORT = "transport"
    LIGHT = "light"


class ModeType(str, Enum):
    LIGHT = "light_managed"
    FULL = "full_managed"


class ScoreDetail(BaseModel):
    distance_score: float = 0
    queue_penalty: float = 0
    preference_match: float = 0
    comfort_score: float = 0
    mode_adjustment: float = 0
    popularity_price: float = 0


class FeasibilityCheck(BaseModel):
    commute_ratio: str = ""
    activity_time_ok: bool = True
    transport_match: bool = True
    passed: bool = True
    warnings: list[str] = Field(default_factory=list)


class ConflictInfo(BaseModel):
    type: str = ""
    severity: str = "warning"
    message: str = ""


_CATEGORY_BY_FRONTEND_TYPE = {
    "activity": NodeCategory.MAIN_ACTIVITY,
    "restaurant": NodeCategory.RESTAURANT,
    "light": NodeCategory.LIGHT,
    "transport": NodeCategory.TRANSPORT,
    "rest": NodeCategory.REST,
    "optional": NodeCategory.OPTIONAL_ACTIVITY,
}

_RESOURCE_BY_FRONTEND_TYPE = {
    "activity": ResourceType.ATTRACTION,
    "restaurant": ResourceType.RESTAURANT,
    "light": ResourceType.LIGHT,
    "transport": ResourceType.TRANSPORT,
    "rest": ResourceType.LIGHT,
    "optional": ResourceType.ATTRACTION,
}

_FRONTEND_TYPE_BY_CATEGORY = {
    NodeCategory.MAIN_ACTIVITY: "activity",
    NodeCategory.RESTAURANT: "restaurant",
    NodeCategory.OPTIONAL_ACTIVITY: "activity",
    NodeCategory.TRANSPORT: "transport",
    NodeCategory.REST: "light",
    NodeCategory.LIGHT: "light",
}


class ItineraryNode(BaseModel):
    """Canonical itinerary node plus adapters for the current frontend shape."""

    node_id: str
    poi_id: str = ""
    poi_name: str
    category: NodeCategory = NodeCategory.MAIN_ACTIVITY
    resource_type: ResourceType = ResourceType.ATTRACTION
    address: str = ""

    scheduled_start: str = ""
    scheduled_end: str = ""
    duration_min: int = 60

    status: str = "planned"
    completed_lock: bool = False
    user_pinned: bool = False
    soft_lock: bool = False
    locked: bool = False
    soft_lock_reason: Optional[str] = None
    fallback_failed: bool = False
    conflict_acknowledged: bool = False

    needs_booking: bool = False
    booking_required: bool = False
    booking_urgent: bool = False
    booking_ref: Optional[str] = None
    booking_status: Optional[str] = None

    conflicts: list[ConflictInfo] = Field(default_factory=list)
    score: float = 0
    score_detail: ScoreDetail = Field(default_factory=ScoreDetail)
    planner_reason: str = ""

    icon: str = ""
    sub: str = ""
    distance: str = ""
    queue_min: int = 0
    queue_text: str = ""
    price: str = ""
    rating: Optional[float] = None
    tags: list[str] = Field(default_factory=list)
    risk_facts: list[str] = Field(default_factory=list)
    transit: Optional[dict[str, Any]] = None
    frontend_extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_frontend_dict(cls, data: dict[str, Any]) -> "ItineraryNode":
        node_type = data.get("type") or data.get("category") or "activity"
        category = _CATEGORY_BY_FRONTEND_TYPE.get(str(node_type), NodeCategory.MAIN_ACTIVITY)
        resource_type = _RESOURCE_BY_FRONTEND_TYPE.get(str(node_type), ResourceType.ATTRACTION)
        queue_min = data.get("queueMin", data.get("queue_min", 0)) or 0

        known_keys = {
            "id", "node_id", "poiId", "poi_id", "name", "poi_name", "type", "category",
            "resource_type", "address", "timeStart", "startTime", "scheduled_start",
            "timeEnd", "endTime", "scheduled_end", "duration", "duration_min", "status",
            "completed_lock", "user_pinned", "pinned", "soft_lock", "locked",
            "soft_lock_reason", "fallback_failed", "conflict_acknowledged", "needs_booking",
            "booking_required", "booking_urgent", "booking_ref", "booking_status",
            "conflicts", "score", "score_detail", "reason", "planner_reason", "icon", "sub",
            "distance", "queueMin", "queue_min", "queueText", "queue_text", "price",
            "rating", "tags", "risk_facts", "transit",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        conflicts = []
        for item in data.get("conflicts", []) or []:
            if isinstance(item, ConflictInfo):
                conflicts.append(item)
            elif isinstance(item, dict):
                conflicts.append(ConflictInfo(**item))
            else:
                conflicts.append(ConflictInfo(message=str(item)))

        score_detail_raw = data.get("score_detail") or {}
        score_detail = score_detail_raw if isinstance(score_detail_raw, ScoreDetail) else ScoreDetail(**score_detail_raw)

        return cls(
            node_id=data.get("node_id") or data.get("id") or "",
            poi_id=data.get("poi_id") or data.get("poiId") or "",
            poi_name=data.get("poi_name") or data.get("name") or "",
            category=category,
            resource_type=resource_type,
            address=data.get("address", ""),
            scheduled_start=data.get("scheduled_start") or data.get("timeStart") or data.get("startTime") or "",
            scheduled_end=data.get("scheduled_end") or data.get("timeEnd") or data.get("endTime") or "",
            duration_min=int(data.get("duration_min") or _parse_duration_min(data.get("duration")) or 60),
            status=data.get("status", "planned"),
            completed_lock=bool(data.get("completed_lock", False)),
            user_pinned=bool(data.get("user_pinned", data.get("pinned", False))),
            soft_lock=bool(data.get("soft_lock", False)),
            locked=bool(data.get("locked", False)),
            soft_lock_reason=data.get("soft_lock_reason"),
            fallback_failed=bool(data.get("fallback_failed", False)),
            conflict_acknowledged=bool(data.get("conflict_acknowledged", False)),
            needs_booking=bool(data.get("needs_booking", False)),
            booking_required=bool(data.get("booking_required", False)),
            booking_urgent=bool(data.get("booking_urgent", False)),
            booking_ref=data.get("booking_ref"),
            booking_status=data.get("booking_status"),
            conflicts=conflicts,
            score=float(data.get("score", 0) or 0),
            score_detail=score_detail,
            planner_reason=data.get("planner_reason") or data.get("reason") or "",
            icon=data.get("icon", ""),
            sub=data.get("sub", ""),
            distance=data.get("distance", ""),
            queue_min=int(queue_min),
            queue_text=data.get("queue_text") or data.get("queueText") or "",
            price=str(data.get("price", "")),
            rating=data.get("rating"),
            tags=list(data.get("tags", []) or []),
            risk_facts=list(data.get("risk_facts", []) or []),
            transit=data.get("transit"),
            frontend_extra=extra,
        )

    def to_frontend_dict(self) -> dict[str, Any]:
        node_type = _FRONTEND_TYPE_BY_CATEGORY.get(self.category, "activity")
        payload: dict[str, Any] = {
            **self.frontend_extra,
            "id": self.node_id,
            "poiId": self.poi_id,
            "name": self.poi_name,
            "type": node_type,
            "icon": self.icon,
            "sub": self.sub,
            "timeStart": self.scheduled_start,
            "timeEnd": self.scheduled_end,
            "duration": f"{self.duration_min}分钟" if self.duration_min else "",
            "status": self.status,
            "completed_lock": self.completed_lock,
            "user_pinned": self.user_pinned,
            "pinned": self.user_pinned,
            "soft_lock": self.soft_lock,
            "locked": self.locked or self.soft_lock,
            "booking_required": self.booking_required,
            "booking_urgent": self.booking_urgent,
            "booking_status": self.booking_status,
            "distance": self.distance,
            "queueMin": self.queue_min,
            "queueText": self.queue_text,
            "price": self.price,
            "rating": self.rating,
            "tags": self.tags,
            "risk_facts": self.risk_facts,
            "reason": self.planner_reason,
        }
        if self.transit is not None:
            payload["transit"] = self.transit
        if self.address:
            payload["address"] = self.address
        if self.conflicts:
            payload["conflicts"] = [_model_to_dict(c) for c in self.conflicts]
        if self.score:
            payload["score"] = self.score
        return payload


class ItineraryData(BaseModel):
    itinerary_id: str
    session_id: str = ""
    mode: ModeType = ModeType.LIGHT
    scene: SceneType = SceneType.FAMILY
    companions: list[CompanionType] = Field(default_factory=list)

    start_time: str = "14:00"
    status: str = "draft"
    nodes: list[ItineraryNode] = Field(default_factory=list)

    feasibility_check: FeasibilityCheck = Field(default_factory=FeasibilityCheck)
    summary: str = ""
    total_duration_min: int = 0
    weather: Optional[dict[str, Any]] = None
    location: Optional[dict[str, Any]] = None

    @classmethod
    def from_frontend_plan(cls, plan: dict[str, Any], session_id: str = "") -> "ItineraryData":
        return cls(
            itinerary_id=plan.get("itinerary_id") or plan.get("id") or f"itin_{session_id[:8] or 'draft'}",
            session_id=session_id or plan.get("session_id", ""),
            mode=plan.get("mode", ModeType.LIGHT),
            scene=plan.get("scene") or plan.get("scenario") or SceneType.FAMILY,
            start_time=plan.get("start_time", "14:00"),
            status=plan.get("status", "draft"),
            nodes=[ItineraryNode.from_frontend_dict(n) for n in plan.get("nodes", [])],
            summary=plan.get("summary", ""),
            total_duration_min=int(plan.get("total_duration_min", 0) or 0),
            weather=plan.get("weather"),
            location=plan.get("location"),
        )

    def to_frontend_plan(self) -> dict[str, Any]:
        return {
            "itinerary_id": self.itinerary_id,
            "session_id": self.session_id,
            "mode": self.mode.value if isinstance(self.mode, Enum) else self.mode,
            "scene": self.scene.value if isinstance(self.scene, Enum) else self.scene,
            "start_time": self.start_time,
            "status": self.status,
            "nodes": [n.to_frontend_dict() for n in self.nodes],
            "summary": self.summary,
            "total_duration_min": self.total_duration_min,
            "weather": self.weather,
            "location": self.location,
            "feasibility_check": _model_to_dict(self.feasibility_check),
        }

    def get_node(self, node_id: str) -> Optional[ItineraryNode]:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def replace_node(self, node_id: str, new_node: ItineraryNode) -> ItineraryNode:
        for index, node in enumerate(self.nodes):
            if node.node_id == node_id:
                self.nodes[index] = new_node
                return new_node
        raise ValueError(f"Node {node_id} not found")

    def remove_node(self, node_id: str) -> None:
        self.nodes = [n for n in self.nodes if n.node_id != node_id]

    def insert_node_after(self, target_id: str, new_node: ItineraryNode) -> None:
        for index, node in enumerate(self.nodes):
            if node.node_id == target_id:
                self.nodes.insert(index + 1, new_node)
                return
        self.nodes.append(new_node)

    def get_pending_nodes(self) -> list[ItineraryNode]:
        return [
            n for n in self.nodes
            if n.status in ("planned", "pending")
            and not n.completed_lock
            and not n.user_pinned
        ]

    def get_completed_nodes(self) -> list[ItineraryNode]:
        return [n for n in self.nodes if n.completed_lock or n.status == "completed"]

    def get_failed_nodes(self) -> list[ItineraryNode]:
        return [n for n in self.nodes if n.status == "failed"]


class UserContext(BaseModel):
    scene: SceneType = SceneType.FAMILY
    time_range: str = "afternoon"
    distance_constraint: str = "nearby"
    companions_text: str = ""
    special_requirements: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    intent_conflict: bool = False
    mode: ModeType = ModeType.LIGHT
    start_time: Optional[str] = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class UserSentiment(BaseModel):
    type: str = ""
    description: str = ""
    node_id: Optional[str] = None


class ItineraryModification(BaseModel):
    type: str = ""
    node_id: Optional[str] = None
    target_node_id: Optional[str] = None
    new_resource: Optional[dict[str, Any]] = None


class SessionStatus(BaseModel):
    session_id: str
    itinerary_state: str = "init"
    mode: ModeType = ModeType.LIGHT
    scene: SceneType = SceneType.FAMILY
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    progress_pct: float = 0.0
    has_pending_confirmation: bool = False
    pending_confirmation_type: Optional[str] = None
    summary: str = ""


class NodeStatus(BaseModel):
    node_id: str
    name: str = ""
    state: str = "planned"
    booking_status: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    start_time: str = ""
    end_time: str = ""


class BookingAction(BaseModel):
    node_id: str
    action: str = ""
    resource_id: str = ""


class ConfirmationRequest(BaseModel):
    request_id: str
    type: str = ""
    title: str = ""
    description: str = ""
    options: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = 120
    created_at: str = ""


class ToolCall(BaseModel):
    tool_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retry_count: int = 0
    success: bool = False


def _parse_duration_min(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            return int(digits)
    return 0


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
