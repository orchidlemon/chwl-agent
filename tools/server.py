"""
MCP Tool Server
暴露规则工具和数据工具给 LLM。

架构说明:
  [规则工具] check_age_policy / check_group_policy / check_time_policy /
             check_itinerary_structure / check_food_policy
             → 规划前由 LLM 主动调用（规则预规划阶段），纯逻辑，返回约束+追问清单

  [数据工具] 按设计文档四类:
    Discovery   — search_activity / search_restaurant / search_alternative
    Validation  — check_availability / check_queue / check_weather / estimate_route
    Execution   — book_ticket / book_restaurant / share_plan
    Monitoring  — watch_queue / watch_weather / watch_booking

  规划阶段 LLM 只拿到 数据工具（Discovery+Validation+finish_planning），
  规则约束作为 context 注入 user_msg，不再作为工具暴露给规划 LLM。

Schema 获取:
  get_rule_tool_schemas()     → 5 个规则工具（规则预规划阶段用）
  get_planning_tool_schemas() → Discovery + Validation + finish_planning（规划阶段用）
  get_data_tool_schemas()     → 全部数据工具（不含规则工具）
  get_openai_tool_schemas()   → 全部工具（含规则工具）
"""
from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)

# ── 规则工具 Schema ────────────────────────────────────────────────────

RULE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_age_policy",
            "description": (
                "【规则工具】检查年龄相关限制。"
                "触发时机: 有孩子/未成年人，或提到酒吧/夜店/密室时调用。"
                "返回 hard_blocks（绝对禁止）、search_filter_hints（exclude_tags/require_tags）、"
                "questions_to_ask_user（需向用户确认的字段）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "child_age": {"type": "integer", "description": "孩子年龄（岁），未知传 null"},
                    "has_minor": {"type": "boolean", "description": "是否确认有未成年人"},
                    "venue_types_requested": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "用户提到想去的场所类型，如 ['bar','escape_room']",
                    },
                    "all_adults_confirmed": {
                        "type": "boolean",
                        "description": "用户是否已确认所有人成年",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_group_policy",
            "description": (
                "【规则工具】检查同行人群约束。"
                "触发时机: 有老人/儿童/朋友群/性别构成信息时调用。"
                "返回活动约束和 search_params_hint（指导 search_activity 的参数）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string", "enum": ["family", "friends", "couple", "solo"]},
                    "has_elderly": {"type": "boolean"},
                    "elderly_no_walking": {"type": "boolean"},
                    "has_children": {"type": "boolean"},
                    "child_age": {"type": "integer"},
                    "child_purpose": {"type": "string", "enum": ["education", "fun"]},
                    "male_count": {"type": "integer"},
                    "female_count": {"type": "integer"},
                    "friends_activity_type": {
                        "type": "string",
                        "enum": ["social", "exhibition", "mall", "photo_spot", "mixed"],
                    },
                    "female_weight_loss": {"type": "boolean"},
                    "female_prefer_low_intensity": {"type": "boolean"},
                    "female_prefer_indoor": {"type": "boolean"},
                    "male_prefer_high_intensity": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_time_policy",
            "description": (
                "【规则工具】根据出发时间和时长推算时间边界约束。"
                "触发时机: 已知出发时间或时长时调用。"
                "返回 search_params_hint.planned_time/planned_end_time，"
                "直接用于 search_activity/search_restaurant 的时间参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string", "description": "出发时间 HH:MM"},
                    "duration_hours": {"type": "number"},
                    "scenario": {"type": "string"},
                    "has_children": {"type": "boolean"},
                    "has_elderly": {"type": "boolean"},
                    "node_count_planned": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_itinerary_structure",
            "description": (
                "【规则工具】在 finish_planning 前验证节点列表结构，并检测用户偏好是否被满足。"
                "violations 非空时必须修正后再提交。"
                "preference_violations 非空时，在 finish_planning.unmet_preferences 中如实列出。"
                        "LLM \u5fc5\u987b\u628a\u672a\u6ee1\u8db3\u504f\u597d\u5408\u6210\u4e3a\u81ea\u7136\u8bed\u8a00 message\uff0c\u4e0d\u8981\u8f93\u51fa\u5217\u8868/JSON/\u65b9\u62ec\u53f7\u3002"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "items": {"type": "object"}},
                    "skip_restaurant": {"type": "boolean"},
                    "has_children": {"type": "boolean"},
                    "start_time": {"type": "string"},
                    "duration_hours": {"type": "number"},
                    "user_prefs": {
                        "type": "object",
                        "description": (
                            "用户原始偏好，用于偏好满足度检测。"
                            "格式: {food: ['日料','川菜'], activity: '博物馆', venue: 'outdoor', skip_restaurant: false}"
                        ),
                    },
                },
                "required": ["nodes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_food_policy",
            "description": (
                "【规则工具】检查饮食约束，生成 search_restaurant 的过滤参数。"
                "触发时机: 有饮食偏好/饮食限制/skip_restaurant=true 时调用。"
                "返回 search_params_hint.preferences/require_tags/skip。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dietary_prefs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "饮食偏好列表，如 ['川菜', '日料']",
                    },
                    "skip_restaurant": {"type": "boolean"},
                    "has_children": {"type": "boolean"},
                    "child_age": {"type": "integer"},
                    "female_weight_loss": {"type": "boolean"},
                    "participant_preferences": {"type": "object"},
                },
                "required": [],
            },
        },
    },
]

# ── Discovery Tools Schema ─────────────────────────────────────────────

_DISCOVERY_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_activity",
            "description": (
                "【Discovery】搜索活动 POI。"
                "exclude_tags/require_tags/planned_time 直接使用规则阶段提供的参数（见 user_msg 规则验证结果）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["family", "friends", "couple", "solo"],
                    },
                    "categories": {"type": "string", "description": "逗号分隔类别"},
                    "planned_time": {"type": "string", "description": "HH:MM，来自规则验证结果"},
                    "planned_end_time": {"type": "string"},
                    "exclude_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "来自 user_msg 规则验证结果的排除标签",
                    },
                    "require_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "来自规则验证结果的必须标签",
                    },
                    "preferred_categories": {"type": "array", "items": {"type": "string"}},
                    "exclude_poi_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "重新规划时必须排除旧路线已出现过的 poi_id",
                    },
                    "duration_min": {
                        "type": "integer",
                        "description": "本节点计划停留分钟数，用于判断候选时长是否合适",
                    },
                },
                "required": ["scenario"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurant",
            "description": (
                "【Discovery】搜索餐厅。"
                "preferences/require_tags 直接使用规则阶段提供的参数。"
                "skip_restaurant=true 时不调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string", "enum": ["family", "friends", "couple", "solo"]},
                    "preferences": {"type": "string", "description": "逗号分隔饮食偏好，来自规则验证结果"},
                    "planned_time": {"type": "string"},
                    "planned_end_time": {"type": "string"},
                    "require_tags": {"type": "array", "items": {"type": "string"}},
                    "exclude_tags": {"type": "array", "items": {"type": "string"}},
                    "exclude_poi_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "重新规划时必须排除旧路线已出现过的 poi_id",
                    },
                    "duration_min": {
                        "type": "integer",
                        "description": "本节点计划停留分钟数，用于判断候选时长是否合适",
                    },
                },
                "required": ["scenario"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_alternative",
            "description": "【Discovery】查找替代 POI，节点队列超长/预约满/天气不合适时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string"},
                    "reason": {"type": "string", "description": "queue_spike/weather/booking_full"},
                    "affected_node_id": {"type": "string"},
                },
                "required": ["scenario", "reason", "affected_node_id"],
            },
        },
    },
]

# ── Validation Tools Schema ────────────────────────────────────────────

_VALIDATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_weather",
            "description": "【Validation】获取当前天气，影响户外/室内活动选择。规划第一步必须调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "【Validation】查询活动预约状态和余量。"
                "booking_required=true 的活动规划时必须确认有票。"
                "poi_id 必须来自 search_activity 返回的真实数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"poi_id": {"type": "string"}},
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_queue",
            "description": (
                "【Validation】查询 POI 实时排队等待时间。"
                "排队 > 40 分钟时调用 search_alternative 换一家。"
                "poi_id 必须来自 search_* 返回的真实数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"poi_id": {"type": "string"}},
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_route",
            "description": "【Validation】估算节点间路线耗时，验证通勤时间是否合理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "起点 POI ID 或 current_location"},
                    "destinations": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string", "enum": ["taxi", "walk", "transit"]},
                },
                "required": ["origin", "destinations"],
            },
        },
    },
]

# ── Execution Tools Schema ─────────────────────────────────────────────

_EXECUTION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "book_ticket",
            "description": "【Execution】预约活动门票，返回预约编号和状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {"type": "string"},
                    "node": {"type": "object", "description": "节点完整信息"},
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_restaurant",
            "description": "【Execution】餐厅预约/取号，返回等位编号和预计等待时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {"type": "string"},
                    "party_size": {"type": "integer", "description": "就餐人数"},
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "share_plan",
            "description": "【Execution】生成行程分享链接，供用户发送给同行人。",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "nodes": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["session_id", "nodes"],
            },
        },
    },
]

# ── Monitoring Tools Schema ────────────────────────────────────────────

_MONITORING_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "watch_queue",
            "description": "【Monitoring】监控 POI 排队变化，超阈值时触发告警。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {"type": "string"},
                    "threshold_min": {"type": "integer", "description": "告警阈值（分钟），默认 40"},
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_weather",
            "description": "【Monitoring】监控天气变化，评估对已规划户外节点的影响。",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_outdoor_poi_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要监控的户外 POI ID 列表",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_booking",
            "description": "【Monitoring】监控预约状态变化（余量减少/关闭），紧急时触发告警。",
            "parameters": {
                "type": "object",
                "properties": {"poi_id": {"type": "string"}},
                "required": ["poi_id"],
            },
        },
    },
]

# ── finish_planning（规划退出工具）─────────────────────────────────────

_FINISH_PLANNING_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "finish_planning",
        "description": (
            "提交最终行程方案。"
            "【前置条件】必须已调用: check_weather + search_activity + search_restaurant "
            "（或规则结果中 skip_restaurant=true）。"
            "nodes 中每个 poiId 必须来自工具返回的真实数据，严禁编造。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "一句话亮点概括，≤30字"},
                "cot": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "规划思路，3-5条，每条≤20字",
                },
                "applied_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "规则阶段调用过的 rule 工具列表",
                },
                "unmet_preferences": {
                    "type": "array",
                    "description": (
                        "用户明确提出但本次未能满足的偏好列表。"
                        "来源: search_activity/search_restaurant 的 preference_gaps，"
                        "以及 check_itinerary_structure 的 preference_violations。"
                        "无未满足项时传空数组 []。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {
                                "type": "string",
                                "description": "未满足的偏好项，如「日料」「博物馆」",
                            },
                            "reason": {
                                "type": "string",
                                "enum": [
                                    "no_results",       # 搜索无结果
                                    "time_constraint",  # 时间段不匹配/已关门
                                    "rule_blocked",     # 规则限制（如儿童禁入）
                                    "queue_replaced",   # 排队太长已换替代
                                    "not_available",    # 无余票/无库存
                                    "no_match",         # 有结果但不含该类型
                                ],
                            },
                            "message": {
                                "type": "string",
                                "description": "\u7ed9\u7528\u6237\u770b\u7684\u5b8c\u6574\u81ea\u7136\u8bed\u8a00\u53e5\u5b50\uff0c\u226450\u5b57\uff1b\u4e0d\u8981\u8f93\u51fa\u5217\u8868\u3001JSON\u3001\u65b9\u62ec\u53f7\u6216\u5b57\u6bb5\u540d",
                            },
                        },
                        "required": ["item", "reason", "message"],
                    },
                },
                "nodes": {
                    "type": "array",
                    "description": "行程节点列表，4-6个",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "poiId": {"type": "string", "description": "必须来自工具返回的真实 poi_id"},
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["activity", "restaurant", "light"]},
                            "icon": {"type": "string"},
                            "timeStart": {"type": "string", "description": "HH:MM"},
                            "timeEnd": {"type": "string", "description": "HH:MM"},
                            "duration": {"type": "integer", "description": "停留分钟数"},
                            "address": {"type": "string"},
                            "price_per_person": {"type": "number"},
                            "booking_required": {"type": "boolean"},
                            "booking_urgent": {"type": "boolean"},
                            "notes": {"type": "string"},
                        },
                        "required": ["id", "poiId", "name", "type", "timeStart", "timeEnd", "duration"],
                    },
                },
            },
            "required": ["summary", "nodes"],
        },
    },
}

# ── 组合列表 ───────────────────────────────────────────────────────────

DATA_TOOLS: list[dict] = (
    _DISCOVERY_TOOLS + _VALIDATION_TOOLS + _EXECUTION_TOOLS + _MONITORING_TOOLS
)

ALL_TOOLS: list[dict] = RULE_TOOLS + DATA_TOOLS + [_FINISH_PLANNING_TOOL]

# 规划阶段用：Discovery + Validation + check_itinerary_structure + finish_planning
_STRUCT_CHECK_TOOL: dict = next(
    t for t in RULE_TOOLS if t["function"]["name"] == "check_itinerary_structure"
)
_PLANNING_DATA_TOOLS: list[dict] = (
    _DISCOVERY_TOOLS + _VALIDATION_TOOLS + [_STRUCT_CHECK_TOOL, _FINISH_PLANNING_TOOL]
)


# ── Schema 获取接口 ────────────────────────────────────────────────────

def get_rule_tool_schemas() -> list[dict]:
    """返回规则工具 schema（规则预规划阶段，LLM 主动判断并调用）。"""
    return RULE_TOOLS


def get_planning_tool_schemas(exclude_names: set[str] | None = None) -> list[dict]:
    """
    返回规划阶段数据工具 schema（Discovery + Validation + finish_planning）。
    规则工具不在此列表中 — 规则已在预规划阶段处理，结果注入 user_msg。
    """
    tools = _PLANNING_DATA_TOOLS
    if exclude_names:
        tools = [t for t in tools if t["function"]["name"] not in exclude_names]
    return tools


def get_data_tool_schemas(exclude_names: set[str] | None = None) -> list[dict]:
    """返回全部数据工具 schema（含 Execution/Monitoring，不含规则工具）。"""
    tools = DATA_TOOLS + [_FINISH_PLANNING_TOOL]
    if exclude_names:
        tools = [t for t in tools if t["function"]["name"] not in exclude_names]
    return tools


def get_openai_tool_schemas() -> list[dict]:
    """返回所有工具 schema（含规则工具），调试用。"""
    return ALL_TOOLS


def to_anthropic_tools(tools: list[dict] | None = None) -> list[dict]:
    """Convert OpenAI-style list to Anthropic tool_use format."""
    result = []
    for t in (tools or ALL_TOOLS):
        fn = t["function"]
        result.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": fn["parameters"],
        })
    return result


# ── Tool Executor ──────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict, seen_poi_ids: set | None = None) -> dict:
    """统一工具调度入口。新旧工具名均支持。"""
    logger.info(f"[MCP] {name}({json.dumps(args, ensure_ascii=False, default=str)[:150]})")
    try:
        # ── 规则工具 ────────────────────────────────────────────────
        if name == "check_age_policy":
            from rules.age_policy import check_age_policy
            return check_age_policy(**args)

        if name == "check_group_policy":
            from rules.group_policy import check_group_policy
            return check_group_policy(**args)

        if name == "check_time_policy":
            from rules.time_policy import check_time_policy
            return check_time_policy(**args)

        if name == "check_itinerary_structure":
            from rules.itinerary_structure import check_itinerary_structure
            return check_itinerary_structure(**args)

        if name == "check_food_policy":
            from rules.food_policy import check_food_policy
            return check_food_policy(**args)

        # ── Validation: 天气 ─────────────────────────────────────────
        if name in ("check_weather", "get_weather"):
            from tools.categories.validation import check_weather
            return await check_weather()

        # ── Discovery: 活动搜索 ──────────────────────────────────────
        if name in ("search_activity", "search_activities"):
            from tools.categories.discovery import search_activity
            result = await search_activity(**args)
            if seen_poi_ids is not None:
                for item in result.get("items", []):
                    seen_poi_ids.add(item.get("poi_id", ""))
            return result

        # ── Discovery: 餐厅搜索 ──────────────────────────────────────
        if name in ("search_restaurant", "search_restaurants"):
            from tools.categories.discovery import search_restaurant
            result = await search_restaurant(**args)
            if seen_poi_ids is not None:
                for item in result.get("items", []):
                    seen_poi_ids.add(item.get("poi_id", ""))
            return result

        # ── Validation: 排队查询 ─────────────────────────────────────
        if name in ("check_queue", "get_queue_status"):
            from tools.categories.validation import check_queue
            return await check_queue(poi_id=args["poi_id"])

        # ── Validation: 预约状态 ─────────────────────────────────────
        if name in ("check_availability", "get_booking_status"):
            from tools.categories.validation import check_availability
            return await check_availability(poi_id=args["poi_id"])

        # ── Validation: 路线估算 ─────────────────────────────────────
        if name in ("estimate_route", "estimate_routes", "route_check"):
            from tools.categories.validation import estimate_route
            destinations = args.get("destinations") or []
            if isinstance(destinations, str):
                destinations = [d.strip() for d in destinations.split(",") if d.strip()]
            return await estimate_route(
                origin=args.get("origin", "current_location"),
                destinations=destinations,
                mode=args.get("mode", "taxi"),
            )

        # ── Discovery: 替代 POI ──────────────────────────────────────
        if name in ("search_alternative", "get_alternatives"):
            from tools.categories.discovery import search_alternative
            return await search_alternative(**args)

        # ── Execution 工具 ───────────────────────────────────────────
        if name in ("book_ticket", "booking_execute"):
            from tools.categories.execution import book_ticket
            return await book_ticket(**args)

        if name == "book_restaurant":
            from tools.categories.execution import book_restaurant
            return await book_restaurant(**args)

        if name == "share_plan":
            from tools.categories.execution import share_plan
            return await share_plan(**args)

        if name == "dispatch_taxi":
            from tools.categories.execution import dispatch_taxi
            return await dispatch_taxi()

        if name == "trigger_preset_event":
            from tools.categories.execution import trigger_preset_event
            return await trigger_preset_event(event_type=args.get("event_type", ""))

        # ── Monitoring 工具 ──────────────────────────────────────────
        if name == "watch_queue":
            from tools.categories.monitoring import watch_queue
            return await watch_queue(**args)

        if name == "watch_weather":
            from tools.categories.monitoring import watch_weather
            return await watch_weather(**args)

        if name == "watch_booking":
            from tools.categories.monitoring import watch_booking
            return await watch_booking(**args)

        if name == "poll_events":
            from tools.categories.monitoring import poll_events
            return await poll_events()

        # finish_planning 由调用方（skills.py）处理
        if name == "finish_planning":
            return {"_passthrough": True, "args": args}

        logger.warning(f"[MCP] Unknown tool: {name}")
        return {"error": f"未知工具: {name}"}

    except Exception as e:
        logger.error(f"[MCP] {name} failed: {e}", exc_info=True)
        return {"error": str(e)}


def summarize_tool_result(name: str, result: dict) -> str:
    """工具结果的单行摘要，用于 CoT 展示。"""
    if "error" in result:
        return f"失败: {result['error']}"

    if name in ("check_age_policy", "check_group_policy", "check_time_policy",
                "check_itinerary_structure", "check_food_policy"):
        blocks = result.get("hard_blocks", [])
        questions = result.get("questions_to_ask_user", [])
        violations = result.get("violations", [])
        parts = []
        if blocks:
            parts.append(f"禁止: {blocks[0]}")
        if questions:
            parts.append(f"需确认: {len(questions)}项")
        if violations:
            parts.append(f"违规: {len(violations)}项")
        return " | ".join(parts) if parts else "规则检查通过"

    if name in ("check_weather", "get_weather"):
        return f"天气: {result.get('condition', '未知')} {result.get('temperature', '?')}°C"

    if name in ("search_activity", "search_activities"):
        return f"找到 {result.get('count', 0)} 个活动（过滤 {result.get('excluded_count', 0)} 个）"

    if name in ("search_restaurant", "search_restaurants"):
        return f"找到 {result.get('count', 0)} 家餐厅"

    if name in ("check_queue", "get_queue_status"):
        return f"排队约 {result.get('estimated_wait_min', '?')} 分钟"

    if name in ("check_availability", "get_booking_status"):
        return f"预约状态: {result.get('availability', '?')}"

    if name in ("estimate_route", "estimate_routes", "route_check"):
        return f"路线共约 {result.get('total_travel_time_min', '?')} 分钟"

    if name in ("search_alternative", "get_alternatives"):
        items = result.get("items", [])
        return f"找到 {len(items)} 个替代 POI"

    if name in ("watch_queue", "watch_weather", "watch_booking"):
        alert = result.get("alert") or result.get("urgent")
        return f"监控: {'⚠️ 需注意' if alert else '正常'}"

    return str(result)[:80]
