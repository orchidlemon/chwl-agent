"""
Background Watch — 7x24 后台监控引擎

设计依据（赛题补充设计 2.7 节）：
用户确认方案后，Agent 必须创建后台 watch 任务持续监控：
- 餐厅排队状态
- 天气变化
- 预约/核销状态
- 活动余量

体系结构：
每个 watch 是一个 asyncio.Task，周期性轮询并对比阈值。
当触发条件满足时，通过 EventBus 发出 watch_alert 事件。
外部监听者（Orchestrator）收到 alert 后决定是否触发重规划。

使用方式:
    watch = BackgroundWatch(event_bus, tool_registry)

    # 为某个节点添加排队监控
    watch.add_watch(WatchConfig(
        watch_id="w_queue_001",
        type="queue",
        poi_id="res_001",
        node_id="node_002",
        threshold={"estimated_wait_min_gt": 35},
        poll_interval_s=10,
    ))

    # 天气监控（全局，不绑定特定节点）
    watch.add_watch(WatchConfig(
        watch_id="w_weather_001",
        type="weather",
        location="北京朝阳区",
        threshold={"rain_level_in": ["heavy"]},
        poll_interval_s=60,
    ))

    # 启动所有 watch
    await watch.start_all("session_001")
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from core.tool_registry import ToolRegistry
from orchestrator.event_bus import EventBus

logger = logging.getLogger(__name__)


class WatchType(str, Enum):
    """监控类型"""
    QUEUE = "queue"           # 排队时间
    WEATHER = "weather"       # 天气变化
    BOOKING = "booking"       # 预约状态
    AVAILABILITY = "availability"  # 余量


class WatchStatus(str, Enum):
    """监控任务状态"""
    RUNNING = "running"
    TRIGGERED = "triggered"   # 已触发告警
    CANCELLED = "cancelled"
    COMPLETED = "completed"   # 节点已完成，停止监控


@dataclass
class WatchConfig:
    """单个监控任务的配置"""
    watch_id: str = ""
    type: WatchType = WatchType.QUEUE
    poi_id: str = ""
    node_id: str = ""                # 关联的行程节点（可为空，如天气）
    location: str = ""               # 监控位置（天气用）
    threshold: dict = field(default_factory=dict)  # 阈值条件
    poll_interval_s: int = 15        # 轮询间隔
    max_polls: int = 0               # 0 = 无限
    auto_resolve: bool = False       # 超阈值是否自动触发 replan

    def get_event_payload(self, current_value: Any) -> dict:
        """生成告警事件负载"""
        return {
            "watch_id": self.watch_id,
            "type": self.type.value,
            "poi_id": self.poi_id,
            "node_id": self.node_id,
            "threshold": self.threshold,
            "current_value": current_value,
            "timestamp": time.time(),
        }


@dataclass
class WatchAlert:
    """监控告警"""
    watch_id: str
    type: str
    severity: str = "warning"       # info | warning | critical
    message: str = ""
    payload: dict = field(default_factory=dict)
    timestamp: float = 0.0


class BackgroundWatch:
    """
    后台监控引擎。

    用法:
        watch_engine = BackgroundWatch(event_bus, tool_registry)

        # 为一个 session 添加监控组
        configs = [
            WatchConfig(type="queue", poi_id="res_001", node_id="n2",
                        threshold={"estimated_wait_min_gt": 35}),
            WatchConfig(type="weather", location="北京望京",
                        threshold={"rain_level_in": ["heavy"]}),
        ]
        await watch_engine.start_watch_group("session_001", configs)

        # 停止某个 session 的所有监控
        await watch_engine.stop_all("session_001")
    """

    def __init__(self, event_bus: Optional[EventBus] = None,
                 tool_registry: Optional[ToolRegistry] = None):
        self.event_bus = event_bus
        self.tools = tool_registry
        self._watchers: dict[str, dict[str, asyncio.Task]] = {}  # session_id -> {watch_id -> Task}
        self._configs: dict[str, dict[str, WatchConfig]] = {}     # session_id -> {watch_id -> Config}
        self._alert_history: list[WatchAlert] = []
        self._max_history = 100

    # ── 管理 ──

    def add_watch(self, session_id: str, config: WatchConfig):
        """添加一个监控配置（不会自动启动）"""
        if not config.watch_id:
            config.watch_id = f"w_{uuid.uuid4().hex[:8]}"

        self._configs.setdefault(session_id, {})[config.watch_id] = config

        logger.debug("[Watch] 添加 %s/%s: type=%s, poi=%s, threshold=%s",
                     session_id[:8], config.watch_id[:8], config.type.value,
                     config.poi_id, config.threshold)

    def add_watch_group(self, session_id: str, configs: list[WatchConfig]):
        """批量添加监控配置"""
        for cfg in configs:
            self.add_watch(session_id, cfg)

    async def start_all(self, session_id: str):
        """启动某个 session 的所有监控"""
        configs = self._configs.get(session_id, {})
        tasks = {}
        for watch_id, config in configs.items():
            task = asyncio.create_task(
                self._run_watch(session_id, config),
                name=f"watch_{session_id[:8]}_{watch_id[:8]}",
            )
            tasks[watch_id] = task

        self._watchers[session_id] = tasks
        logger.info("[Watch] 启动 %d 个监控: %s", len(tasks), session_id[:8])

    async def start_watch_group(self, session_id: str, configs: list[WatchConfig]):
        """便捷：添加并启动一组监控"""
        self.add_watch_group(session_id, configs)
        await self.start_all(session_id)

    async def stop_all(self, session_id: str):
        """停止某个 session 的所有监控"""
        tasks = self._watchers.pop(session_id, {})
        for watch_id, task in tasks.items():
            task.cancel()
        self._configs.pop(session_id, None)
        logger.info("[Watch] 停止 %d 个监控: %s", len(tasks), session_id[:8])

    async def stop_watch(self, session_id: str, watch_id: str):
        """停止单个监控"""
        task = self._watchers.get(session_id, {}).pop(watch_id, None)
        if task:
            task.cancel()
        self._configs.get(session_id, {}).pop(watch_id, None)

    def is_running(self, session_id: str, watch_id: str) -> bool:
        task = self._watchers.get(session_id, {}).get(watch_id)
        return task is not None and not task.done()

    def get_active_count(self, session_id: str) -> int:
        return len(self._watchers.get(session_id, {}))

    def get_alerts(self, limit: int = 20) -> list[WatchAlert]:
        return self._alert_history[-limit:]

    # ── 监控循环 ──

    async def _run_watch(self, session_id: str, config: WatchConfig):
        """单个监控的主循环"""
        poll_count = 0

        while True:
            try:
                await asyncio.sleep(config.poll_interval_s)
                poll_count += 1

                if config.max_polls > 0 and poll_count > config.max_polls:
                    logger.debug("[Watch] %s 达到最大轮询次数", config.watch_id[:8])
                    break

                # 根据类型执行检查
                if config.type == WatchType.QUEUE:
                    await self._check_queue(session_id, config)
                elif config.type == WatchType.WEATHER:
                    await self._check_weather(session_id, config)
                elif config.type == WatchType.BOOKING:
                    await self._check_booking(session_id, config)
                elif config.type == WatchType.AVAILABILITY:
                    await self._check_availability(session_id, config)

            except asyncio.CancelledError:
                logger.debug("[Watch] %s 被取消", config.watch_id[:8])
                return
            except Exception as e:
                logger.error("[Watch] %s 异常: %s", config.watch_id[:8], e,
                             exc_info=True)
                await asyncio.sleep(config.poll_interval_s * 2)  # 失败后加倍等待

    # ── 检查逻辑 ──

    async def _check_queue(self, session_id: str, config: WatchConfig):
        """检查排队时间是否超阈值"""
        if not self.tools:
            return

        result = await self.tools.invoke("booking_status", {
            "poi_id": config.poi_id,
            "watch_check": True,
        })
        if not result.get("success"):
            return

        data = result.get("data", {})
        wait_min = data.get("estimated_wait_minutes", 0)
        threshold = config.threshold.get("estimated_wait_min_gt", 999)

        if wait_min > threshold:
            await self._trigger_alert(session_id, config, WatchAlert(
                watch_id=config.watch_id,
                type="queue",
                severity="warning",
                message=f"排队时间 {wait_min} 分钟，超过阈值 {threshold} 分钟",
                payload=config.get_event_payload(wait_min),
                timestamp=time.time(),
            ))

    async def _check_weather(self, session_id: str, config: WatchConfig):
        """检查天气变化"""
        if not self.tools:
            return

        result = await self.tools.invoke("weather", {
            "location": config.location or "北京",
            "watch_check": True,
        })
        if not result.get("success"):
            return

        data = result.get("data", {})
        weather = data.get("weather", "")

        rain_levels = config.threshold.get("rain_level_in", [])
        if rain_levels and weather in rain_levels:
            await self._trigger_alert(session_id, config, WatchAlert(
                watch_id=config.watch_id,
                type="weather",
                severity="critical",
                message=f"天气变为 {weather}，户外活动可能受影响",
                payload=config.get_event_payload(weather),
                timestamp=time.time(),
            ))

    async def _check_booking(self, session_id: str, config: WatchConfig):
        """检查预约状态变化"""
        if not self.tools:
            return

        result = await self.tools.invoke("booking_status", {
            "booking_ref": config.poi_id,  # 这里 poi_id 作为 booking_ref
            "watch_check": True,
        })
        if not result.get("success"):
            return

        data = result.get("data", {})
        status = data.get("status", "")

        if status == "failed":
            await self._trigger_alert(session_id, config, WatchAlert(
                watch_id=config.watch_id,
                type="booking",
                severity="critical",
                message=f"预约失败: {data.get('error', '未知错误')}",
                payload=config.get_event_payload(status),
                timestamp=time.time(),
            ))
        elif status == "confirmed":
            # 预约成功，可以停止监控
            logger.debug("[Watch] %s 预约成功，停止监控", config.watch_id[:8])

    async def _check_availability(self, session_id: str, config: WatchConfig):
        """检查活动余量"""
        if not self.tools:
            return

        result = await self.tools.invoke("activities_search", {
            "poi_id": config.poi_id,
            "watch_check": True,
        })
        if not result.get("success"):
            return

        data = result.get("data", {})
        activities = data.get("activities", [])
        for act in activities:
            if act.get("poi_id") == config.poi_id:
                available = act.get("ticket_available", True)
                if not available:
                    await self._trigger_alert(session_id, config, WatchAlert(
                        watch_id=config.watch_id,
                        type="availability",
                        severity="critical",
                        message=f"「{act.get('name', '')}」门票已售罄",
                        payload=config.get_event_payload(False),
                        timestamp=time.time(),
                    ))
                break

    # ── 告警 ──

    async def _trigger_alert(self, session_id: str, config: WatchConfig,
                              alert: WatchAlert):
        """触发告警"""
        self._alert_history.append(alert)
        if len(self._alert_history) > self._max_history:
            self._alert_history.pop(0)

        logger.warning("[Watch] 告警 %s: %s", config.watch_id[:8], alert.message)

        if self.event_bus:
            await self.event_bus.emit("watch_alert", None, {
                "session_id": session_id,
                "alert": {
                    "watch_id": alert.watch_id,
                    "type": alert.type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "payload": alert.payload,
                },
                "node_id": config.node_id,
                "poi_id": config.poi_id,
            })

        # 如果配置了自动处理，从 config 触发回调
        if config.auto_resolve:
            logger.info("[Watch] %s 自动处理已触发", config.watch_id[:8])
