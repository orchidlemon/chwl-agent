"""Environment tools — weather, location, events."""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def get_weather() -> dict:
    """获取当前天气（晴/多云/小雨/大雨）和温度。规划前必须调用。"""
    from backend import tools as _tools
    return await _tools.get_weather()


async def get_user_location() -> dict:
    """获取用户当前位置，用于计算起点距离。"""
    from backend import tools as _tools
    return await _tools.get_user_location()


async def poll_events() -> dict:
    """轮询沙盒环境事件（队列激增/天气突变/预约关闭），用于监控阶段。"""
    from backend import tools as _tools
    return await _tools.poll_events()
