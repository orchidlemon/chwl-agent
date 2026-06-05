"""
Agent Tool Definitions — LLM Function Calling Layer

Defines the tools available to the planning agent and dispatches
tool calls to the actual Mock API functions in tools.py.

Design:
- PLANNING_TOOLS: JSON Schema list passed to LLM (DeepSeek/Anthropic)
- execute_tool(): maps LLM tool call name → tools.py function
- finish_planning: special pseudo-tool that signals end of data collection
  and submits the final itinerary (LLM provides nodes as tool arguments)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


# ── Tool Definitions (sent to LLM) ───────────────────────────────────

PLANNING_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取当前天气状况（晴/多云/小雨/大雨）和温度。规划前必须调用，天气影响户外活动选择。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_activities",
            "description": "搜索附近活动 POI（景点/文化/儿童娱乐/户外等），返回真实 poi_id、名称、票价、开放时间、是否需要预约。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["family", "friends", "couple", "elderly", "solo"],
                        "description": "出行场景",
                    },
                    "categories": {
                        "type": "string",
                        "description": "筛选类别，如 'park,museum'；不填返回全部",
                    },
                },
                "required": ["scenario"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": "搜索附近餐厅，返回真实 poi_id、菜系、人均价格、营业时间、队列数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["family", "friends", "couple", "elderly", "solo"],
                    },
                    "preferences": {
                        "type": "string",
                        "description": "饮食偏好，如 '川菜' 或 '无辣，儿童友好'；不填不限",
                    },
                },
                "required": ["scenario"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_queue_status",
            "description": "查询特定 POI 的实时排队等待时间（分钟）。餐厅排队 > 40 分钟时应换一家。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {
                        "type": "string",
                        "description": "必须来自 search_activities 或 search_restaurants 返回的真实 poi_id",
                    },
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_booking_status",
            "description": "查询活动的预约状态、余量和时段。booking_required=true 的活动必须调用此工具确认有票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {
                        "type": "string",
                        "description": "必须来自 search_activities 返回的真实 poi_id",
                    },
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_routes",
            "description": (
                "估算多个行程节点之间的连续路线耗时。只用于检查通勤时间和路线可行性，"
                "不要因此固定用户行程路线；最终节点仍应根据用户偏好和 POI 选择生成。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "起点 POI ID，如 home/current_location 或上一节点 poi_id",
                    },
                    "destinations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按计划访问顺序排列的目的地 POI ID 列表",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["taxi", "walk", "transit"],
                        "description": "交通方式，默认 taxi",
                    },
                },
                "required": ["origin", "destinations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_planning",
            "description": (
                "收集足够数据后调用此工具提交最终行程方案。"
                "【前提】必须已调用 search_activities 和 search_restaurants，且天气已知。"
                "【约束】nodes 中每个 poiId 必须来自工具返回的真实数据，严禁编造。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "一句话概括今天行程的亮点，≤30字",
                    },
                    "cot": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "规划思路步骤，3-5条，每条 ≤ 20 字",
                    },
                    "nodes": {
                        "type": "array",
                        "description": "行程节点列表，4-6个",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "节点唯一ID，如 n1/n2/n3",
                                },
                                "poiId": {
                                    "type": "string",
                                    "description": "工具返回的真实 poi_id，严禁编造",
                                },
                                "name": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["activity", "restaurant", "light"],
                                },
                                "icon": {
                                    "type": "string",
                                    "description": "emoji，如 🎡🍜🚶",
                                },
                                "startTime": {
                                    "type": "string",
                                    "description": "格式 HH:MM",
                                },
                                "endTime": {
                                    "type": "string",
                                    "description": "格式 HH:MM",
                                },
                                "duration": {
                                    "type": "integer",
                                    "description": "停留时长（分钟）",
                                },
                                "address": {"type": "string"},
                                "price_per_person": {
                                    "type": "number",
                                    "description": "人均费用（元）",
                                },
                                "booking_required": {"type": "boolean"},
                                "booking_urgent": {"type": "boolean"},
                                "notes": {
                                    "type": "string",
                                    "description": "给用户的温馨提示",
                                },
                            },
                            "required": ["id", "poiId", "name", "type",
                                         "startTime", "endTime", "duration"],
                        },
                    },
                },
                "required": ["summary", "nodes"],
            },
        },
    },
]

# ── Anthropic format conversion ───────────────────────────────────────

def to_anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style tool list to Anthropic tool_use format."""
    result = []
    for t in tools:
        fn = t["function"]
        result.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": fn["parameters"],
        })
    return result


# ── Tool Executor ─────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict,
                       seen_poi_ids: set | None = None) -> dict:
    """
    Execute a tool call and return the result dict.
    seen_poi_ids: tracks all POI IDs returned so far (for anti-hallucination).
    """
    from . import tools

    logger.info(f"[Tool] {name}({json.dumps(args, ensure_ascii=False)[:120]})")

    try:
        if name == "get_weather":
            return await tools.get_weather()

        elif name == "search_activities":
            items = await tools.get_activities(
                scenario=args.get("scenario", "family"),
                categories=args.get("categories", "").split(",") if args.get("categories") else None,
            )
            if seen_poi_ids is not None:
                for item in items:
                    seen_poi_ids.add(item.get("poi_id", ""))
            return {"items": items, "count": len(items)}

        elif name == "search_restaurants":
            prefs_raw = args.get("preferences", "")
            prefs = [p.strip() for p in prefs_raw.split(",") if p.strip()] if prefs_raw else None
            items = await tools.get_restaurants(
                scenario=args.get("scenario", "family"),
                preferences=prefs,
            )
            if seen_poi_ids is not None:
                for item in items:
                    seen_poi_ids.add(item.get("poi_id", ""))
            return {"items": items, "count": len(items)}

        elif name == "get_queue_status":
            return await tools.get_queue_status(poi_id=args["poi_id"])

        elif name == "get_booking_status":
            return await tools.get_booking_status(poi_id=args["poi_id"])

        elif name in ("estimate_routes", "route_check"):
            destinations = args.get("destinations") or []
            if isinstance(destinations, str):
                destinations = [d.strip() for d in destinations.split(",") if d.strip()]
            return await tools.get_routes(
                origin=args.get("origin", args.get("from_poi", "current_location")),
                destinations=destinations,
                mode=args.get("mode", args.get("transport_mode", "taxi")),
            )

        elif name == "estimate_route":
            return await tools.get_route(
                from_id=args.get("from_poi", args.get("origin", "current_location")),
                to_id=args.get("to_poi", args.get("destination", "")),
                mode=args.get("mode", "taxi"),
            )

        else:
            logger.warning(f"[Tool] Unknown tool: {name}")
            return {"error": f"未知工具: {name}"}

    except Exception as e:
        logger.error(f"[Tool] {name} failed: {e}")
        return {"error": str(e)}


# ── Result Summarizers (for CoT display) ─────────────────────────────

def summarize_tool_result(name: str, result: dict) -> str:
    """One-line human-readable summary of a tool result."""
    if "error" in result:
        return f"失败: {result['error']}"

    if name == "get_weather":
        return f"天气: {result.get('condition', '未知')}，{result.get('temperature', '?')}°C"

    if name == "search_activities":
        count = result.get("count", 0)
        top = result.get("items", [])[:1]
        top_name = top[0].get("name", "") if top else ""
        return f"找到 {count} 个活动" + (f"，首选：{top_name}" if top_name else "")

    if name == "search_restaurants":
        count = result.get("count", 0)
        top = result.get("items", [])[:1]
        top_name = top[0].get("name", "") if top else ""
        return f"找到 {count} 家餐厅" + (f"，首选：{top_name}" if top_name else "")

    if name == "get_queue_status":
        wait = result.get("estimated_wait_min", result.get("wait_minutes", 0))
        return f"当前排队约 {wait} 分钟"

    if name == "get_booking_status":
        avail = result.get("availability", result.get("status", "unknown"))
        return f"预约状态: {avail}"

    if name == "estimate_route":
        dur = result.get("duration_min", result.get("estimated_time_min", "?"))
        dist = result.get("distance_km", "?")
        return f"路程约 {dist} km，{dur} 分钟"

    if name in ("estimate_routes", "route_check"):
        total = result.get("total_travel_time_min", "?")
        segments = result.get("segments", [])
        return f"路线共约 {total} 分钟，{len(segments)} 段"

    return str(result)[:80]
