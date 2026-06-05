"""
Pydantic 数据模型

涵盖：Itinerary（行程）、Node（节点）、Session（会话）、
       Tool 调用、错误、事件等全部数据结构。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


# ─── 枚举 ─────────────────────────────────────────────────────

class SceneType(str, Enum):
    """场景类型"""
    FAMILY = "family"
    FRIENDS = "friends"
    COUPLE = "couple"
    SOLO = "solo"


class CompanionType(str, Enum):
    """同行人类型"""
    ADULT_MALE = "adult_male"
    ADULT_FEMALE = "adult_female"
    CHILD_3_6 = "child_3_6"      # 3-6 岁
    CHILD_7_12 = "child_7_12"    # 7-12 岁
    ELDERLY = "elderly"


class NodeCategory(str, Enum):
    """节点类别"""
    MAIN_ACTIVITY = "main_activity"
    RESTAURANT = "restaurant"
    OPTIONAL_ACTIVITY = "optional_activity"
    TRANSPORT = "transport"
    REST = "rest"


class ResourceType(str, Enum):
    """资源类型（影响软锁策略）"""
    ATTRACTION = "attraction"        # 景点/演出（稀缺，早锁）
    RESTAURANT = "restaurant"        # 餐厅（动态时间，出发前 90min 锁）
    PARKING = "parking"              # 停车/充电（实时可用，出发前 15min）
    TRANSPORT = "transport"          # 交通（不锁）


class ModeType(str, Enum):
    """托管模式"""
    LIGHT = "light_managed"
    FULL = "full_managed"


# ─── 评分模型 ─────────────────────────────────────────────────

class ScoreDetail(BaseModel):
    """评分明细"""
    distance_score: float = 0
    queue_penalty: float = 0
    preference_match: float = 0
    comfort_score: float = 0
    mode_adjustment: float = 0
    popularity_price: float = 0


class FeasibilityCheck(BaseModel):
    """合理性校验"""
    commute_ratio: str = ""          # e.g. "17%"
    activity_time_ok: bool = True
    transport_match: bool = True
    passed: bool = True


class ConflictInfo(BaseModel):
    """节点冲突信息"""
    type: str = ""                   # queue_time | time_overlap | weather | distance
    severity: str = "warning"        # warning | error
    message: str = ""


# ─── 节点 ─────────────────────────────────────────────────────

class ItineraryNode(BaseModel):
    """行程节点"""
    node_id: str
    poi_id: str
    poi_name: str
    category: NodeCategory = NodeCategory.MAIN_ACTIVITY
    resource_type: ResourceType = ResourceType.ATTRACTION
    address: str = ""

    # 时间
    scheduled_start: str = ""        # "14:20"
    scheduled_end: str = ""          # "16:20"
    duration_min: int = 60

    # 状态
    status: str = "planned"          # 对应 NodeState 的值
    completed_lock: bool = False
    user_pinned: bool = False
    soft_lock: bool = False
    soft_lock_reason: Optional[str] = None
    fallback_failed: bool = False
    conflict_acknowledged: bool = False

    # 履约
    needs_booking: bool = True
    booking_ref: Optional[str] = None
    booking_status: Optional[str] = None  # queued | processing | confirmed | failed

    # 冲突
    conflicts: list[ConflictInfo] = Field(default_factory=list)

    # 评分
    score: float = 0
    score_detail: ScoreDetail = Field(default_factory=ScoreDetail)
    planner_reason: str = ""

    # 标签
    tags: list[str] = Field(default_factory=list)


# ─── 行程 ─────────────────────────────────────────────────────

class ItineraryData(BaseModel):
    """行程顶层结构"""
    itinerary_id: str
    session_id: str = ""
    mode: ModeType = ModeType.LIGHT
    scene: SceneType = SceneType.FAMILY
    companions: list[CompanionType] = Field(default_factory=list)

    start_time: str = "14:00"
    status: str = "draft"            # 对应 ItineraryState

    nodes: list[ItineraryNode] = Field(default_factory=list)

    feasibility_check: FeasibilityCheck = Field(default_factory=FeasibilityCheck)
    summary: str = ""
    total_duration_min: int = 0

    weather: Optional[dict] = None
    location: Optional[dict] = None

    # ── 便捷方法 ──

    def get_node(self, node_id: str) -> Optional[ItineraryNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def replace_node(self, node_id: str, new_node: ItineraryNode) -> ItineraryNode:
        for i, n in enumerate(self.nodes):
            if n.node_id == node_id:
                self.nodes[i] = new_node
                return new_node
        raise ValueError(f"Node {node_id} not found")

    def remove_node(self, node_id: str):
        self.nodes = [n for n in self.nodes if n.node_id != node_id]

    def insert_node_after(self, target_id: str, new_node: ItineraryNode):
        for i, n in enumerate(self.nodes):
            if n.node_id == target_id:
                self.nodes.insert(i + 1, new_node)
                return
        self.nodes.append(new_node)

    def get_pending_nodes(self) -> list[ItineraryNode]:
        return [n for n in self.nodes
                if n.status in ("planned", "pending")
                and not n.completed_lock
                and not n.user_pinned]

    def get_completed_nodes(self) -> list[ItineraryNode]:
        return [n for n in self.nodes if n.completed_lock or n.status == "completed_lock"]

    def get_failed_nodes(self) -> list[ItineraryNode]:
        return [n for n in self.nodes if n.status == "failed"]


# ─── 会话 ─────────────────────────────────────────────────────

class UserContext(BaseModel):
    """用户输入解析结果"""
    scene: SceneType = SceneType.FAMILY
    time_range: str = "afternoon"
    distance_constraint: str = "nearby"
    companions_text: str = ""
    special_requirements: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    intent_conflict: bool = False
    mode: ModeType = ModeType.LIGHT
    start_time: Optional[str] = None


class UserSentiment(BaseModel):
    """用户情绪/状态报告"""
    type: str = ""             # tired | hungry | bored | weather_uncomfortable | other
    description: str = ""
    node_id: Optional[str] = None


class ItineraryModification(BaseModel):
    """用户对行程的编辑操作"""
    type: str = ""             # replace | delete | insert | reorder
    node_id: Optional[str] = None
    target_node_id: Optional[str] = None
    new_resource: Optional[dict] = None


# ─── 会话状态快照 ────────────────────────────────────────────

class SessionStatus(BaseModel):
    """返回给 Planner/UI 的会话状态"""
    session_id: str
    itinerary_state: str = "init"
    mode: ModeType = ModeType.LIGHT
    scene: SceneType = SceneType.FAMILY
    nodes: list[dict] = Field(default_factory=list)   # 精简版节点信息
    progress_pct: float = 0.0
    has_pending_confirmation: bool = False
    pending_confirmation_type: Optional[str] = None
    summary: str = ""


class NodeStatus(BaseModel):
    """节点状态快照"""
    node_id: str
    name: str = ""
    state: str = "planned"
    booking_status: Optional[str] = None
    error: Optional[dict] = None
    start_time: str = ""
    end_time: str = ""


# ─── 履约 ─────────────────────────────────────────────────────

class BookingAction(BaseModel):
    """一个履约动作"""
    node_id: str
    action: str = ""             # reserve | queue | pay | cancel
    resource_id: str = ""


class ConfirmationRequest(BaseModel):
    """用户确认请求"""
    request_id: str
    type: str = ""               # replan | skip_node | cancel_resource | initial_plan
    title: str = ""
    description: str = ""
    options: list[dict] = Field(default_factory=list)  # [{label, value, recommended}]
    context: dict = Field(default_factory=dict)
    timeout_s: int = 120
    created_at: str = ""


# ─── 工具调用 ─────────────────────────────────────────────────

class ToolCall(BaseModel):
    """一次工具调用记录"""
    tool_name: str
    payload: dict = Field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retry_count: int = 0
    success: bool = False
