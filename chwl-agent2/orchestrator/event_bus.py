"""
内部事件总线 — 组件间异步通信

基于 asyncio.Queue + pub/sub 模式。
Orchestrator 内部组件通过事件解耦，外部 (Planner/UI) 通过订阅事件获取状态变化。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """
    轻量级事件总线。

    用法:
        bus = EventBus()

        # 订阅
        def on_plan_complete(ctx, event):
            print("plan done!", event["data"])

        bus.subscribe("plan_complete", on_plan_complete)

        # 发布
        await bus.emit("plan_complete", ctx, {"itinerary": ...})
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._wildcard_handlers: list[Callable] = []  # 订阅 "*" 的 handler
        self._history: list[dict] = []                 # 事件历史（调试用）
        self._max_history = 100

    def subscribe(self, event_type: str, handler: Callable):
        """订阅事件。event_type="*" 表示所有事件。"""
        if event_type == "*":
            self._wildcard_handlers.append(handler)
        else:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        if event_type == "*":
            self._wildcard_handlers = [h for h in self._wildcard_handlers if h != handler]
        else:
            handlers = self._handlers.get(event_type, [])
            self._handlers[event_type] = [h for h in handlers if h != handler]

    async def emit(self, event_type: str, ctx: Any, data: dict):
        """
        发布事件。

        所有 handler 被异步调用（create_task），不会阻塞发布者。
        """
        event = {
            "id": uuid.uuid4().hex[:8],
            "type": event_type,
            "timestamp": time.time(),
            "session_id": getattr(ctx, "session_id", ""),
            "data": data,
        }

        # 记录历史
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        logger.debug("[Event] %s: %s", event_type, str(data)[:80])

        # 通知订阅者（异步，不阻塞）
        handlers = self._handlers.get(event_type, []) + self._wildcard_handlers
        for handler in handlers:
            try:
                result = handler(ctx, event)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("[EventBus] handler 异常: %s", e, exc_info=True)

    def get_history(self, event_type: str | None = None,
                    limit: int = 20) -> list[dict]:
        """获取事件历史（调试用）"""
        events = self._history
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        return events[-limit:]

    def clear_history(self):
        self._history.clear()
