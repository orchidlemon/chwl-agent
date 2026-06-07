"""
Validation Tools — 事实校验
CheckAvailability: 库存与营业状态查询
CheckQueue: 排队情况查询
CheckWeather: 天气查询
EstimateRoute: 路线与时间估算
所有数据来自 Mock API，禁止编造状态或时间。
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def check_weather() -> dict:
    """获取当前天气（晴/多云/小雨/大雨）和温度。规划前必须调用，影响户外活动选择。"""
    from backend import tools as _tools
    return await _tools.get_weather()


# Alias for backward compatibility
get_weather = check_weather


async def check_availability(poi_id: str) -> dict:
    """
    查询活动预约状态和余量。
    booking_required=true 的活动规划时必须调用确认有票。
    poi_id 必须来自 search_activity 返回的真实数据。
    """
    from backend import tools as _tools
    return await _tools.get_booking_status(poi_id=poi_id)


# Alias for backward compatibility
get_booking_status = check_availability


async def check_queue(poi_id: str) -> dict:
    """
    查询 POI 实时排队等待时间（分钟）。
    餐厅/热门活动排队 > 40 分钟时应考虑 search_alternative。
    poi_id 必须来自 search_* 返回的真实数据。
    """
    from backend import tools as _tools
    return await _tools.get_queue_status(poi_id=poi_id)


# Alias for backward compatibility
get_queue_status = check_queue


async def estimate_route(
    origin: str,
    destinations: list[str],
    mode: str = "taxi",
) -> dict:
    """
    估算节点间路线耗时，验证通勤时间是否合理。

    Args:
        origin: 起点 POI ID 或 "current_location"
        destinations: 按访问顺序排列的目的地 POI ID 列表
        mode: taxi / walk / transit
    """
    from backend import tools as _tools
    return await _tools.get_routes(origin=origin, destinations=destinations, mode=mode)


# Aliases for backward compatibility
estimate_routes = estimate_route
route_check = estimate_route
