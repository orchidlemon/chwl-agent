"""
Execution Tools — 履约执行
BookTicket: 活动预约
BookRestaurant: 餐厅预约/取号
SharePlan: 行程分享
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def book_ticket(poi_id: str, node: dict | None = None) -> dict:
    """预约活动门票，返回预约编号和状态。poi_id 必须来自 search_activity 真实数据。"""
    from backend import tools as _tools
    return await _tools.booking_execute(node=node or {"poi_id": poi_id})


# Alias
booking_execute = book_ticket


async def book_restaurant(poi_id: str, party_size: int = 2) -> dict:
    """餐厅预约/取号，返回等位编号和预计等待时间。poi_id 必须来自 search_restaurant 真实数据。"""
    from backend import tools as _tools
    return await _tools.booking_execute(node={"poi_id": poi_id, "party_size": party_size, "type": "restaurant"})


async def share_plan(session_id: str, nodes: list[dict]) -> dict:
    """生成行程分享链接，供用户发送给同行人。"""
    return {
        "share_url": f"/plan/share/{session_id}",
        "nodes_count": len(nodes),
        "message": "行程分享链接已生成",
    }


async def dispatch_taxi() -> dict:
    """叫出租车/网约车，返回司机信息和预计到达时间。"""
    from backend import tools as _tools
    return await _tools.dispatch_taxi()


async def trigger_preset_event(event_type: str) -> dict:
    """触发预设沙盒事件（模拟器/测试用）。"""
    from backend import tools as _tools
    return await _tools.trigger_preset_event(event_type=event_type)
