"""Fulfillment tools — booking execution, taxi dispatch, simulator."""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def booking_execute(node: dict, on_status=None) -> dict:
    """执行预约流程，返回预约编号和状态。"""
    from backend import tools as _tools
    return await _tools.booking_execute(node=node, on_status=on_status)


async def dispatch_taxi() -> dict:
    """叫出租车/网约车，返回司机信息和预计到达时间。"""
    from backend import tools as _tools
    return await _tools.dispatch_taxi()


async def trigger_preset_event(event_type: str) -> dict:
    """触发预设沙盒事件（用于模拟器/测试）。"""
    from backend import tools as _tools
    return await _tools.trigger_preset_event(event_type=event_type)
