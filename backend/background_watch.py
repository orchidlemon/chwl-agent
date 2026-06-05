"""Background watch engine adapted to the current session/SSE architecture."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from . import tools


class WatchType(str, Enum):
    QUEUE = "queue"
    WEATHER = "weather"
    BOOKING = "booking"
    AVAILABILITY = "availability"


@dataclass
class WatchConfig:
    type: WatchType
    poi_id: str = ""
    node_id: str = ""
    watch_id: str = ""
    location: str = ""
    threshold: dict[str, Any] = field(default_factory=dict)
    poll_interval_s: int = 15
    cooldown_s: int = 120
    max_polls: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = WatchType(self.type)
        if not self.watch_id:
            suffix = self.node_id or self.poi_id or uuid.uuid4().hex[:8]
            self.watch_id = f"w_{self.type.value}_{suffix}"


@dataclass
class WatchAlert:
    watch_id: str
    type: WatchType
    event_type: str
    message: str
    severity: str = "medium"
    poi_id: str = ""
    node_id: str = ""
    current_value: Any = None
    created_at: float = field(default_factory=time.time)

    def to_event(self) -> dict[str, Any]:
        return {
            "event_id": f"evt_watch_{uuid.uuid4().hex[:8]}",
            "watch_id": self.watch_id,
            "type": self.event_type,
            "severity": self.severity,
            "poi_id": self.poi_id,
            "node_id": self.node_id,
            "message": self.message,
            "current_value": self.current_value,
            "requires_user_confirmation": self.severity in {"high", "critical"},
            "source": "background_watch",
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


class BackgroundWatch:
    """Session-scoped background monitoring without introducing EventBus."""

    def __init__(self, manager, confirmation_gateway=None):
        self.manager = manager
        self.confirmation_gateway = confirmation_gateway
        self._configs: dict[str, dict[str, WatchConfig]] = {}
        self._tasks: dict[str, dict[str, asyncio.Task]] = {}
        self._last_alert_at: dict[tuple[str, str], float] = {}

    def add_watch(self, session_id: str, config: WatchConfig) -> WatchConfig:
        self._configs.setdefault(session_id, {})[config.watch_id] = config
        self._sync_session_watch_ids(session_id)
        return config

    def add_watch_group(self, session_id: str, configs: list[WatchConfig]) -> list[WatchConfig]:
        return [self.add_watch(session_id, cfg) for cfg in configs]

    async def start_watch_group(self, session_id: str, configs: list[WatchConfig]) -> None:
        self.add_watch_group(session_id, configs)
        await self.start_all(session_id)

    async def start_all(self, session_id: str) -> None:
        self._tasks.setdefault(session_id, {})
        for watch_id, config in self._configs.get(session_id, {}).items():
            task = self._tasks[session_id].get(watch_id)
            if task and not task.done():
                continue
            self._tasks[session_id][watch_id] = asyncio.create_task(
                self._run_watch(session_id, config),
                name=f"watch_{session_id[:8]}_{watch_id[:24]}",
            )
        self._sync_session_watch_ids(session_id)

    async def stop_watch(self, session_id: str, watch_id: str) -> None:
        task = self._tasks.get(session_id, {}).pop(watch_id, None)
        if task and not task.done():
            task.cancel()
        self._configs.get(session_id, {}).pop(watch_id, None)
        self._sync_session_watch_ids(session_id)

    async def stop_all(self, session_id: str) -> None:
        tasks = self._tasks.pop(session_id, {})
        for task in tasks.values():
            if not task.done():
                task.cancel()
        self._configs.pop(session_id, None)
        for key in [k for k in self._last_alert_at if k[0] == session_id]:
            self._last_alert_at.pop(key, None)
        self._sync_session_watch_ids(session_id)

    def stop_all_sync(self, session_id: str) -> None:
        tasks = self._tasks.pop(session_id, {})
        for task in tasks.values():
            if not task.done():
                task.cancel()
        self._configs.pop(session_id, None)
        for key in [k for k in self._last_alert_at if k[0] == session_id]:
            self._last_alert_at.pop(key, None)
        self._sync_session_watch_ids(session_id)

    def get_active_count(self, session_id: str) -> int:
        return sum(1 for task in self._tasks.get(session_id, {}).values() if not task.done())

    def get_configs(self, session_id: str) -> list[WatchConfig]:
        return list(self._configs.get(session_id, {}).values())

    async def check_once(self, session_id: str) -> list[WatchAlert]:
        alerts = []
        for config in list(self._configs.get(session_id, {}).values()):
            alert = await self._check(session_id, config)
            if alert:
                alerts.append(alert)
        return alerts

    async def _run_watch(self, session_id: str, config: WatchConfig) -> None:
        polls = 0
        while True:
            try:
                await asyncio.sleep(config.poll_interval_s)
                polls += 1
                if config.max_polls and polls > config.max_polls:
                    return
                await self._check(session_id, config)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.manager.add_monitor_event(
                    session_id,
                    "background_watch",
                    f"后台监控异常: {config.type.value} {exc}",
                    "watch_error",
                    config.poi_id,
                )
                await asyncio.sleep(max(config.poll_interval_s, 5))

    async def _check(self, session_id: str, config: WatchConfig) -> Optional[WatchAlert]:
        if not self.manager.get(session_id):
            await self.stop_all(session_id)
            return None
        if config.type == WatchType.QUEUE:
            alert = await self._check_queue(config)
        elif config.type == WatchType.WEATHER:
            alert = await self._check_weather(config)
        elif config.type == WatchType.BOOKING:
            alert = await self._check_booking(config)
        elif config.type == WatchType.AVAILABILITY:
            alert = await self._check_availability(config)
        else:
            alert = None
        if alert:
            return await self._record_alert(session_id, config, alert)
        return None

    async def _check_queue(self, config: WatchConfig) -> Optional[WatchAlert]:
        data = await tools.get_queue_status(config.poi_id)
        wait = int(data.get("estimated_wait_min", data.get("estimated_wait_minutes", 0)) or 0)
        threshold = int(config.threshold.get("estimated_wait_min_gt", 45))
        if wait > threshold:
            return WatchAlert(
                watch_id=config.watch_id,
                type=WatchType.QUEUE,
                event_type="queue_spike",
                severity="high",
                poi_id=config.poi_id,
                node_id=config.node_id,
                current_value=wait,
                message=f"餐厅排队已升至 {wait} 分钟，超过阈值 {threshold} 分钟",
            )
        return None

    async def _check_weather(self, config: WatchConfig) -> Optional[WatchAlert]:
        data = await tools.get_weather()
        rain = data.get("rain_level")
        condition = data.get("condition", "")
        risk = data.get("risk_level", "")
        rain_levels = set(config.threshold.get("rain_level_in", ["heavy"]))
        risk_levels = set(config.threshold.get("risk_level_in", ["high"]))
        if rain in rain_levels or risk in risk_levels or condition in {"heavy_rain", "storm"}:
            value = rain or condition or risk
            return WatchAlert(
                watch_id=config.watch_id,
                type=WatchType.WEATHER,
                event_type="weather_heavy_rain",
                severity="high",
                poi_id=config.poi_id,
                node_id=config.node_id,
                current_value=value,
                message="天气恶化，户外活动体验风险升高",
            )
        return None

    async def _check_booking(self, config: WatchConfig) -> Optional[WatchAlert]:
        data = await tools.get_booking_status(config.poi_id)
        status = str(data.get("status", data.get("availability", ""))).lower()
        if status in {"failed", "cancelled", "booking_failed"}:
            return WatchAlert(
                watch_id=config.watch_id,
                type=WatchType.BOOKING,
                event_type="booking_failed",
                severity="critical",
                poi_id=config.poi_id,
                node_id=config.node_id,
                current_value=status,
                message="预约状态异常，需要重新确认后续安排",
            )
        return None

    async def _check_availability(self, config: WatchConfig) -> Optional[WatchAlert]:
        data = await tools.get_booking_status(config.poi_id)
        status = str(data.get("status", data.get("availability", ""))).lower()
        available = data.get("available", data.get("ticket_available", True))
        if available is False or status in {"full", "sold_out", "unavailable", "booking_full"}:
            return WatchAlert(
                watch_id=config.watch_id,
                type=WatchType.AVAILABILITY,
                event_type="activity_capacity_low",
                severity="critical",
                poi_id=config.poi_id,
                node_id=config.node_id,
                current_value=status or available,
                message="活动名额或预约余量不足，需要准备备选方案",
            )
        return None

    async def _record_alert(self, session_id: str, config: WatchConfig, alert: WatchAlert) -> Optional[WatchAlert]:
        key = (session_id, config.watch_id)
        now = time.time()
        last = self._last_alert_at.get(key, 0)
        if now - last < config.cooldown_s:
            return None
        self._last_alert_at[key] = now

        event = alert.to_event()
        session = self.manager.get(session_id)
        if session is not None:
            if self.confirmation_gateway and alert.severity in {"high", "critical"}:
                req = await self.confirmation_gateway.request(
                    session_id,
                    "replan",
                    {
                        "exception_type": alert.event_type,
                        "poi_id": alert.poi_id,
                        "node_id": alert.node_id,
                        "watch_id": alert.watch_id,
                    },
                    title="是否切换方案",
                    description=alert.message,
                    timeout_s=120,
                )
                event["request_id"] = req.request_id
            session["pending_exception"] = event
            session["pending_monitor_msg"] = event
            session.setdefault("watch_alerts", []).append(event)
            session["watch_alerts"] = session["watch_alerts"][-50:]
        self.manager.add_monitor_event(
            session_id,
            "background_watch",
            alert.message,
            alert.event_type,
            alert.poi_id,
        )
        return alert

    def _sync_session_watch_ids(self, session_id: str) -> None:
        session = self.manager.get(session_id)
        if session is not None:
            session["watch_ids"] = list(self._configs.get(session_id, {}).keys())


def build_watch_configs_for_itinerary(itinerary: list[dict]) -> list[WatchConfig]:
    configs: list[WatchConfig] = []
    has_weather_sensitive_node = False
    for node in itinerary:
        poi_id = node.get("poiId", "")
        node_id = node.get("id", "")
        node_type = node.get("type", "")
        tags = {str(tag) for tag in node.get("tags", [])}

        if node_type == "restaurant" and poi_id:
            configs.append(WatchConfig(
                type=WatchType.QUEUE,
                poi_id=poi_id,
                node_id=node_id,
                threshold={"estimated_wait_min_gt": max(int(node.get("queueMin", 0) or 0) + 25, 45)},
                poll_interval_s=15,
                cooldown_s=120,
            ))

        if poi_id and (node.get("booking_required") or node.get("booking_urgent")):
            configs.append(WatchConfig(
                type=WatchType.BOOKING,
                poi_id=poi_id,
                node_id=node_id,
                poll_interval_s=20,
                cooldown_s=120,
            ))
            configs.append(WatchConfig(
                type=WatchType.AVAILABILITY,
                poi_id=poi_id,
                node_id=node_id,
                poll_interval_s=30,
                cooldown_s=180,
            ))

        if node_type == "activity" and ("户外" in tags or "公园" in tags or "outdoor" in tags):
            has_weather_sensitive_node = True

    if has_weather_sensitive_node:
        configs.append(WatchConfig(
            type=WatchType.WEATHER,
            watch_id="w_weather_global",
            threshold={"rain_level_in": ["heavy"], "risk_level_in": ["high"]},
            poll_interval_s=30,
            cooldown_s=180,
        ))
    return configs
