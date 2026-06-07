"""
Tool Fallback Defaults
用于"一键安排"等信息不全时的工具参数补全。

使用方式:
  from tools.defaults import get_default
  scenario = session_facts.get("scenario") or get_default("scenario")

原则:
  - 这里的值只在 session_facts 中对应字段为 None/缺失 时才被工具使用
  - 绝不写回 session_facts，不污染用户画像
  - 每次工具调用现场取用，不做全局修改
"""
from __future__ import annotations
import time as _time

# ── 静态默认值 ─────────────────────────────────────────────────────────

_STATIC_DEFAULTS: dict = {
    "scenario": "family",          # 默认家庭出行
    "duration_hours": 3,           # 默认3小时
    "mode": "taxi",                # 默认出行方式
    "node_count": 4,               # 默认4个节点
    "travel_style": "relaxed",     # 默认轻松风格
    "radius_km": 5.0,              # 默认搜索半径
    "has_children": False,
    "has_elderly": False,
    "skip_restaurant": False,
    "food_preferences": [],
    "venue_preference": None,
}

# ── 动态默认值（每次调用时计算）──────────────────────────────────────

def _dynamic_defaults() -> dict:
    now = _time.localtime()
    hour = now.tm_hour
    # 出发时间默认：当前时间向上取整到半小时，至少距现在30分钟
    total_min = hour * 60 + now.tm_min + 30
    total_min = (total_min // 30 + 1) * 30  # 向上取整到下一个30分钟
    total_min = min(total_min, 20 * 60)       # 不超过20:00
    start_time = f"{total_min // 60:02d}:{total_min % 60:02d}"
    return {
        "start_time": start_time,
    }


def get_default(field: str):
    """
    获取单个字段的 fallback 默认值。
    动态字段（start_time）每次调用时实时计算。
    """
    dynamic = _dynamic_defaults()
    if field in dynamic:
        return dynamic[field]
    return _STATIC_DEFAULTS.get(field)


def fill_missing(facts: dict, fields: list[str]) -> dict:
    """
    对 facts 中缺失的 fields 用默认值填充，返回新 dict，不修改原始 facts。
    用于工具调用时的临时补全，不写回 session。
    """
    dynamic = _dynamic_defaults()
    result = dict(facts)
    for field in fields:
        if result.get(field) is None:
            if field in dynamic:
                result[field] = dynamic[field]
            elif field in _STATIC_DEFAULTS:
                result[field] = _STATIC_DEFAULTS[field]
    return result
