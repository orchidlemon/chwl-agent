"""
前端渲染契约 — 6 组 JSON Schema

所有 Schema 遵循以下原则：
1. 前端直接按字段渲染，不依赖自然语言
2. 字段缺失填 null，不省略
3. 枚举值严格限定
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ═══════════════════════════════════════════════════════════════
# 1. Itinerary Card Schema — 行程卡片
# ═══════════════════════════════════════════════════════════════

class NodeAction(str, Enum):
    """节点可执行操作"""
    REPLACE = "replace"
    DELETE = "delete"
    PIN = "pin"
    UNPIN = "unpin"
    DELAY = "delay"
    ADVANCE = "advance"
    SKIP = "skip"
    OPEN_BOOKING = "open_booking"
    OPEN_MAP = "open_map"
    COMPLETE = "complete"
    REPORT_ISSUE = "report_issue"


class CardStatus(str, Enum):
    """卡片状态"""
    DRAFT = "draft"
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNED = "replanned"
    SKIPPED = "skipped"


class ItineraryCardSchema(BaseModel):
    """行程卡片 Schema — 时间轴上的一个节点"""
    node_id: str = Field(..., description="节点唯一 ID")
    type: str = Field(..., description="节点类型: activity | restaurant | transport | rest")
    poi_id: str = Field(..., description="POI 唯一 ID")
    title: str = Field(..., description="地点名称，如'汤姆猫亲子乐园'")
    subtitle: str = Field("", description="副标题，如'室内 · 亲子友好 · 雨天友好'")

    # 时间
    start_time: str = Field(..., description="开始时间 HH:MM")
    end_time: str = Field(..., description="结束时间 HH:MM")
    duration_min: int = Field(0, description="预计时长")

    # 状态
    status: CardStatus = Field(CardStatus.DRAFT, description="卡片状态")
    is_pinned: bool = Field(False, description="用户是否固定此节点")
    is_locked: bool = Field(False, description="是否已完成锁定")

    # 评分与理由
    rating: float = Field(0.0, description="POI 评分 0-5")
    score: float = Field(0.0, description="Agent 评分 0-100")
    planner_reason: str = Field("", description="选择理由，如'低卡匹配度高，排队短'")

    # 标签
    tags: list[str] = Field(default_factory=list, description="标签列表")

    # 距离
    distance_km: float = Field(0.0, description="距上一节点的距离")
    travel_time_min: int = Field(0, description="距上一节点的交通时间")

    # 履约
    needs_booking: bool = Field(True, description="是否需要预约/购票")
    booking_status: Optional[str] = Field(None, description="履约状态: queued | processing | confirmed | failed")
    booking_ref: Optional[str] = Field(None, description="预约编号")

    # 交互
    available_actions: list[NodeAction] = Field(
        default_factory=lambda: [NodeAction.REPLACE, NodeAction.DELETE, NodeAction.PIN],
        description="当前可执行的操作列表"
    )
    conflicts: list[dict] = Field(default_factory=list, description="冲突警告列表")

    model_config = ConfigDict(use_enum_values=True)

class ItineraryPlanSchema(BaseModel):
    """完整行程方案 — 由一组有序卡片组成"""
    plan_id: str = Field(..., description="方案 ID")
    summary: str = Field("", description="一句话摘要，如'下午2点出发，先去室内乐园，再吃清淡晚饭'")
    scene: str = Field("family", description="场景: family | friends | couple | solo")
    mode: str = Field("light_managed", description="模式: light_managed | full_managed")
    total_duration_min: int = Field(0, description="预计总耗时")
    start_time: str = Field("", description="出发时间")
    end_time: str = Field("", description="预计结束时间")

    nodes: list[ItineraryCardSchema] = Field(
        default_factory=list, description="有序卡片列表"
    )

    feasibility: dict = Field(
        default_factory=lambda: {
            "commute_ratio_ok": True,
            "weather_ok": True,
            "queue_ok": True,
            "child_safety_ok": True,
        },
        description="可行性校验摘要"
    )

    weather: Optional[dict] = Field(None, description="天气信息")
    location: Optional[dict] = Field(None, description="位置信息")


# ═══════════════════════════════════════════════════════════════
# 2. Status Stream Schema — 状态流
# ═══════════════════════════════════════════════════════════════

class StatusLevel(str, Enum):
    INFO = "info"
    PROCESSING = "processing"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class StatusStreamItem(BaseModel):
    """单条状态"""
    id: str = Field(..., description="状态 ID")
    level: StatusLevel = Field(StatusLevel.INFO, description="状态级别")
    icon: str = Field("", description="图标名，前端映射: ⏳✅⚠️❌")
    message: str = Field(..., description="状态文本")
    timestamp: str = Field("", description="时间戳 HH:MM:SS")
    progress_pct: Optional[float] = Field(None, description="可选进度 0-100")


class StatusStreamSchema(BaseModel):
    """状态流 Schema — 异步加载和履约时的流水账"""
    session_id: str = Field(..., description="会话 ID")
    items: list[StatusStreamItem] = Field(
        default_factory=list, description="按时间序的状态条目"
    )
    estimated_remaining_seconds: int = Field(0, description="预估剩余秒数")


# ═══════════════════════════════════════════════════════════════
# 3. Risk Modal Schema — 风险弹窗
# ═══════════════════════════════════════════════════════════════

class RiskSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class RiskAction(BaseModel):
    """弹窗中的可操作选项"""
    action_id: str = Field(..., description="操作 ID")
    label: str = Field(..., description="按钮文案，如'同意切换'")
    type: str = Field("confirm", description="操作类型: confirm | reject | view_alternatives")
    recommended: bool = Field(False, description="是否推荐")
    payload: dict = Field(default_factory=dict, description="选中后传给后端的参数")


class RiskModalSchema(BaseModel):
    """风险弹窗 Schema — 排队暴增/天气突变/预约售罄时弹出"""
    modal_id: str = Field(..., description="弹窗 ID")
    severity: RiskSeverity = Field(RiskSeverity.WARNING, description="风险等级")
    title: str = Field(..., description="弹窗标题，如'餐厅排队过长'")
    description: str = Field(..., description="问题描述，详细说明情况和影响")
    affected_node_id: str = Field("", description="受影响的节点 ID")

    # 系统推荐方案
    recommended_action: str = Field("", description="推荐行动摘要")

    # 用户可选择的 action
    actions: list[RiskAction] = Field(
        default_factory=list,
        description="可选操作列表（至少 2 个，最多 4 个）"
    )

    # 资源损失警告（如有）
    resource_loss_warning: Optional[str] = Field(
        None,
        description="取消/替换有成本节点时的损失告知"
    )

    model_config = ConfigDict(use_enum_values=True)


# ═══════════════════════════════════════════════════════════════
# 4. Alternative Nodes Schema — 备选节点列表
# ═══════════════════════════════════════════════════════════════

class AlternativeNode(BaseModel):
    """单个备选节点"""
    poi_id: str = Field(..., description="POI ID")
    title: str = Field(..., description="名称")
    subtitle: str = Field("", description="简短描述")
    rating: float = Field(0.0, description="评分 0-5")
    score: float = Field(0.0, description="Agent 评分 0-100")
    distance_km: float = Field(0.0, description="距当前节点的距离")
    queue_time_min: int = Field(0, description="当前排队时间")
    avg_price: float = Field(0.0, description="人均价格")
    tags: list[str] = Field(default_factory=list, description="标签")
    planner_reason: str = Field("", description="推荐理由")
    recommended: bool = Field(False, description="是否系统推荐")


class AlternativeNodesSchema(BaseModel):
    """备选节点 Schema — 替换操作时展示"""
    target_node_id: str = Field(..., description="被替换的节点 ID")
    target_node_name: str = Field("", description="被替换的节点名称")

    # 系统推荐（最多 2 个）
    recommended: list[AlternativeNode] = Field(
        default_factory=list,
        description="系统推荐替代（2 个）"
    )

    # 更多候选
    more_candidates: list[AlternativeNode] = Field(
        default_factory=list,
        description="更多候选（前端可滚动加载）"
    )

    total_available: int = Field(0, description="可用候选总数")


# ═══════════════════════════════════════════════════════════════
# 5. Fulfillment Status Schema — 履约状态
# ═══════════════════════════════════════════════════════════════

class FulfillmentTask(BaseModel):
    """单个履约任务"""
    task_id: str = Field(..., description="任务 ID")
    node_id: str = Field(..., description="关联节点 ID")
    node_name: str = Field("", description="节点名称")
    action: str = Field(..., description="动作: reserve | queue | pay | open_map")
    status: str = Field("pending", description="状态: pending | processing | success | failed")
    message: str = Field("", description="状态描述")
    booking_ref: Optional[str] = Field(None, description="预约编号/核销码")
    timestamp: str = Field("", description="时间戳")


class FulfillmentStatusSchema(BaseModel):
    """履约状态 Schema — 一键安排后的履约进度看板"""
    session_id: str = Field(..., description="会话 ID")
    overall_status: str = Field(
        "processing",
        description="整体状态: processing | all_success | partial_failure | all_failed"
    )
    progress_pct: float = Field(0.0, description="整体进度 0-100")
    tasks: list[FulfillmentTask] = Field(
        default_factory=list, description="所有履约任务"
    )


# ═══════════════════════════════════════════════════════════════
# 6. Share Message Schema — 分享消息
# ═══════════════════════════════════════════════════════════════

class ShareNode(BaseModel):
    """分享中的单个节点"""
    time: str = Field(..., description="时间 HH:MM")
    title: str = Field(..., description="名称")
    type_icon: str = Field("", description="类型图标")


class ShareMessageSchema(BaseModel):
    """分享消息 Schema — 一键生成分享文本/长图"""
    title: str = Field("下午出行计划", description="分享标题")
    summary: str = Field("", description="一句话总结")
    total_duration: str = Field("", description="总时长")
    nodes: list[ShareNode] = Field(default_factory=list, description="行程节点摘要")
    note: str = Field("", description="附言，如'系统自动生成，仅供参考'")
    share_url: str = Field("", description="分享链接（Mock）")
    generated_at: str = Field("", description="生成时间")

    model_config = ConfigDict(use_enum_values=True)


# ═══════════════════════════════════════════════════════════════
# Schema 注册表 — 方便 OutputValidator 引用
# ═══════════════════════════════════════════════════════════════

ALL_SCHEMAS = {
    "itinerary_card": ItineraryCardSchema,
    "itinerary_plan": ItineraryPlanSchema,
    "status_stream": StatusStreamSchema,
    "risk_modal": RiskModalSchema,
    "alternative_nodes": AlternativeNodesSchema,
    "fulfillment_status": FulfillmentStatusSchema,
    "share_message": ShareMessageSchema,
}

# 默认按 schema_type 映射到 Pydantic model
SCHEMA_TYPE_MAP = {
    "plan_complete": "itinerary_plan",
    "status_update": "status_stream",
    "risk_alert": "risk_modal",
    "alternatives": "alternative_nodes",
    "fulfillment_update": "fulfillment_status",
    "share": "share_message",
    "card_update": "itinerary_card",
}



