"""Realtime tools — queue and booking status."""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def get_queue_status(poi_id: str) -> dict:
    """
    查询 POI 的实时排队等待时间（分钟）。
    餐厅排队 > 40 分钟时应考虑换一家或选择 get_alternatives。
    poi_id 必须来自 search_activities 或 search_restaurants 返回的真实数据。
    """
    from backend import tools as _tools
    return await _tools.get_queue_status(poi_id=poi_id)


async def get_booking_status(poi_id: str) -> dict:
    """
    查询活动的预约状态和余量。
    booking_required=true 的活动在规划时必须调用此接口确认有余票。
    poi_id 必须来自 search_activities 返回的真实数据。
    """
    from backend import tools as _tools
    return await _tools.get_booking_status(poi_id=poi_id)
