"""
LLM Environment Simulator — 动态环境事件注入

设计依据（赛题补充设计 3.11 节）：
在 Demo 中模拟动态环境事件，让 Mock API 更贴近真实世界。

边界：
- 只生成事实事件（排队变长、天气变化、余量变化）
- 不做最终行程决策
- 不替用户确认支付、取消或替换
- 通过 EventBus 将事件推送给 Background Watch 和 Orchestrator

使用方式:
    simulator = EnvSimulator(mock_backend, event_bus)

    # 预设事件序列（时间线）
    simulator.schedule_event(after_seconds=5, event_type="queue_spike", {
        "poi_ids": ["res_fam_001"],
        "new_queue_time": 90,
    })
    simulator.schedule_event(after_seconds=15, event_type="weather_change", {
        "weather": "中雨",
    })

    # 启动时间线
    await simulator.run_timeline("session_demo")
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from mocks import MockBackend
from orchestrator.event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class SimulatedEvent:
    """一个模拟事件"""
    after_seconds: float = 5.0        # 距启动多少秒后触发
    event_type: str = "queue_spike"   # queue_spike | weather_change | availability_change | booking_fail
    params: dict = field(default_factory=dict)
    description: str = ""
    triggered: bool = False


class EnvSimulator:
    """
    动态环境事件模拟器。

    用法:
        simulator = EnvSimulator(mock_backend, event_bus)

        # 预设事件
        simulator.schedule(
            after_seconds=8,
            event_type="queue_spike",
            params={"poi_ids": ["res_fam_001"], "new_queue_time": 90},
            description="轻食研究所排队暴增至90分钟",
        )
        simulator.schedule(
            after_seconds=20,
            event_type="weather_change",
            params={"weather": "中雨", "temperature": 23},
            description="突降中雨",
        )

        # 启动
        await simulator.run_timeline("demo_session")
    """

    def __init__(self, mock_backend: Optional[MockBackend] = None,
                 event_bus: Optional[EventBus] = None):
        self.backend = mock_backend
        self.event_bus = event_bus
        self._events: list[SimulatedEvent] = []
        self._running = False
        self._triggered_count = 0

    # ── 配置 ──

    def schedule(self, after_seconds: float, event_type: str,
                 params: dict, description: str = ""):
        """安排一个模拟事件"""
        self._events.append(SimulatedEvent(
            after_seconds=after_seconds,
            event_type=event_type,
            params=params,
            description=description,
        ))
        logger.info("[EnvSim] 已安排: +%.1fs %s — %s",
                    after_seconds, event_type, description)

    def schedule_preset_family_scenario(self):
        """预设家庭场景事件序列"""
        self.schedule(5, "status_update",
                      {"message": "已为您锁定「家庭亲子」模式，正在实时关注排队变化..."},
                      "系统状态更新")
        self.schedule(12, "queue_spike",
                      {"poi_ids": ["res_fam_001"], "new_queue_time": 90},
                      "轻食研究所排队暴增至90分钟")
        self.schedule(25, "weather_change",
                      {"weather": "多云", "temperature": 26},
                      "天气转为多云，户外活动仍安全")

    def schedule_preset_friends_scenario(self):
        """预设朋友场景事件序列"""
        self.schedule(5, "status_update",
                      {"message": "已为您锁定「朋友聚会」模式，正在监控天气和排队..."},
                      "系统状态更新")
        self.schedule(10, "weather_change",
                      {"weather": "中雨", "temperature": 22},
                      "突发中雨，户外活动需要调整")
        self.schedule(18, "queue_spike",
                      {"poi_ids": ["res_frd_001"], "new_queue_time": 60},
                      "创意Bistro排队增加至60分钟")

    def schedule_preset_auto_demo(self):
        """自动演示的完整事件序列"""
        # Phase 1: 规划阶段
        self.schedule(4, "status_update",
                      {"message": "正在获取您的当前位置..."}, "")
        self.schedule(6, "status_update",
                      {"message": "已找到附近3个亲子活动、3家餐厅"}, "")

        # Phase 2: 履约中的异常
        self.schedule(14, "queue_spike",
                      {"poi_ids": ["res_fam_001"], "new_queue_time": 90},
                      "【排队异常】餐厅突发排队90分钟")
        self.schedule(18, "status_update",
                      {"message": "系统已找到同商圈备选餐厅「亲子轻食餐厅」（排队20分钟）"},
                      "")

        # Phase 3: 天气变化
        self.schedule(22, "weather_change",
                      {"weather": "中雨", "temperature": 23},
                      "【天气变化】转为中雨，已自动检查户外节点安全性")

    # ── 运行 ──

    async def run_timeline(self, session_id: str):
        """按时间线依次触发事件"""
        if self._running:
            logger.warning("[EnvSim] 已有一个时间线在运行")
            return

        self._running = True
        sorted_events = sorted(self._events, key=lambda e: e.after_seconds)

        logger.info("[EnvSim] 启动时间线: %d 个事件，session=%s",
                    len(sorted_events), session_id[:8])

        start = time.time()

        for event in sorted_events:
            elapsed = time.time() - start
            wait = max(0, event.after_seconds - elapsed)
            if wait > 0:
                await asyncio.sleep(wait)

            if not self._running:
                break

            await self._dispatch(event, session_id)

        self._running = False
        logger.info("[EnvSim] 时间线结束: %d 个事件已触发", self._triggered_count)

    async def _dispatch(self, event: SimulatedEvent, session_id: str):
        """执行一个模拟事件"""
        event.triggered = True
        self._triggered_count += 1
        etype = event.event_type

        logger.info("[EnvSim] 🔔 触发: +%.1fs %s — %s",
                    event.after_seconds, etype, event.description or "")

        if etype == "queue_spike":
            await self._handle_queue_spike(event, session_id)
        elif etype == "weather_change":
            await self._handle_weather_change(event, session_id)
        elif etype == "availability_change":
            await self._handle_availability_change(event, session_id)
        elif etype == "booking_fail":
            await self._handle_booking_fail(event, session_id)
        elif etype == "status_update":
            await self._handle_status_update(event, session_id)
        else:
            logger.warning("[EnvSim] 未知事件类型: %s", etype)

    # ── 事件处理 ──

    async def _handle_queue_spike(self, event: SimulatedEvent, session_id: str):
        """排队暴增：修改 mock backend 的排队数据 + 触发事件"""
        poi_ids = event.params.get("poi_ids", [])
        new_time = event.params.get("new_queue_time", 90)

        # 修改 mock backend
        if self.backend:
            for poi_id in poi_ids:
                self.backend.set_fail("booking_execute", "queue_too_long")
                logger.debug("[EnvSim] %s 排队设为 %d 分钟", poi_id, new_time)

        # 通过 EventBus 通知
        if self.event_bus:
            await self.event_bus.emit("env_event_queue_spike", None, {
                "session_id": session_id,
                "type": "queue_spike",
                "poi_ids": poi_ids,
                "new_queue_time": new_time,
                "description": event.description,
            })

    async def _handle_weather_change(self, event: SimulatedEvent, session_id: str):
        """天气变化：修改 mock backend + 触发事件"""
        weather = event.params.get("weather", "晴")
        temperature = event.params.get("temperature", 25)

        if self.backend:
            self.backend.set_weather("rainy" if "雨" in weather else weather)

        if self.event_bus:
            await self.event_bus.emit("env_event_weather_change", None, {
                "session_id": session_id,
                "type": "weather_change",
                "weather": weather,
                "temperature": temperature,
                "description": event.description,
            })

    async def _handle_availability_change(self, event: SimulatedEvent, session_id: str):
        """余量变化"""
        poi_ids = event.params.get("poi_ids", [])
        available = event.params.get("available", False)

        if self.event_bus:
            await self.event_bus.emit("env_event_availability", None, {
                "session_id": session_id,
                "type": "availability_change",
                "poi_ids": poi_ids,
                "available": available,
                "description": event.description,
            })

    async def _handle_booking_fail(self, event: SimulatedEvent, session_id: str):
        """预约失败"""
        node_id = event.params.get("node_id", "")
        reason = event.params.get("reason", "booking_rejected")

        if self.backend:
            self.backend.set_fail("booking_execute", reason)

        if self.event_bus:
            await self.event_bus.emit("env_event_booking_fail", None, {
                "session_id": session_id,
                "type": "booking_fail",
                "node_id": node_id,
                "reason": reason,
                "description": event.description,
            })

    async def _handle_status_update(self, event: SimulatedEvent, session_id: str):
        """状态更新（纯展示）"""
        if self.event_bus:
            await self.event_bus.emit("status_update", None, {
                "session_id": session_id,
                "message": event.params.get("message", ""),
            })

    # ── 控制 ──

    def stop(self):
        """停止时间线"""
        self._running = False
        logger.info("[EnvSim] 时间线已停止")
