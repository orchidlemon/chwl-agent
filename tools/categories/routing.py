"""Routing tools — transit time and distance estimation."""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def estimate_routes(
    origin: str,
    destinations: list[str],
    mode: str = "taxi",
) -> dict:
    """
    估算多个节点之间的连续路线耗时。
    用于验证通勤时间是否超过目的地停留时长（check_itinerary_structure 的前置工具）。

    Args:
        origin: 起点 POI ID 或 "current_location"
        destinations: 按访问顺序排列的目的地 POI ID 列表
        mode: taxi / walk / transit
    """
    from backend import tools as _tools
    return await _tools.get_routes(origin=origin, destinations=destinations, mode=mode)
