"""
Monitoring Tools — 运行监控
WatchQueue: 排队变化监控
WatchWeather: 天气变化监控
WatchBooking: 预约状态监控
用于行程执行阶段的实时状态跟踪。
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def watch_queue(poi_id: str, threshold_min: int = 40) -> dict:
    """
    监控 POI 排队变化。排队超过 threshold_min 分钟时触发告警。
    返回当前等待时间和变化趋势。
    """
    from backend import tools as _tools
    result = await _tools.get_queue_status(poi_id=poi_id)
    wait = result.get("estimated_wait_min", 0)
    result["alert"] = wait > threshold_min
    result["threshold_min"] = threshold_min
    return result


async def watch_weather(check_outdoor_poi_ids: list[str] | None = None) -> dict:
    """
    监控天气变化，影响户外活动安全性。
    返回当前天气状态和对已规划户外节点的影响评估。
    """
    from backend import tools as _tools
    weather = await _tools.get_weather()
    outdoor_impact = []
    if check_outdoor_poi_ids:
        condition = weather.get("condition", "")
        if any(w in condition for w in ["雨", "暴", "雷"]):
            outdoor_impact = [
                {"poi_id": pid, "risk": "high", "reason": f"当前天气: {condition}"}
                for pid in check_outdoor_poi_ids
            ]
    weather["outdoor_impact"] = outdoor_impact
    return weather


async def watch_booking(poi_id: str) -> dict:
    """
    监控预约状态变化（余量减少/关闭）。
    返回当前余量和是否需要紧急处理。
    """
    from backend import tools as _tools
    result = await _tools.get_booking_status(poi_id=poi_id)
    availability = result.get("availability", "unknown")
    result["urgent"] = availability in ("last_few", "sold_out", "closing_soon")
    return result


async def poll_events() -> dict:
    """轮询沙盒环境事件（队列激增/天气突变/预约关闭），监控阶段使用。"""
    from backend import tools as _tools
    return await _tools.poll_events()
