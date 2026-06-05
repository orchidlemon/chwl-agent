"""
用户确认网关 — 管理"暂停 → 通知 → 等待 → 继续/回滚"生命周期

任何有真实世界成本的操作必须经过确认网关：
1. 暂停执行，节点进入 AWAITING_CONFIRMATION 状态
2. 通过 EventBus 发出 confirmation_needed 事件
3. 等待用户响应（设置超时）
4. 按用户决策继续或回滚

超时策略：自动拒绝（安全默认），保持原方案不变。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationRequest:
    """一个确认请求"""
    request_id: str
    type: str                      # replan | skip_node | cancel_resource | initial_plan
    context: dict = field(default_factory=dict)
    title: str = ""
    description: str = ""
    options: list[dict] = field(default_factory=list)  # [{label, value, recommended}]
    callback: Optional[Callable] = None   # 用户响应后的回调
    timeout_s: int = 120
    status: str = "pending"        # pending | confirmed | rejected | timeout
    created_at: float = 0.0


class ConfirmationGateway:
    """
    确认网关。

    用法:
        gateway = ConfirmationGateway(event_bus)

        # 发起确认请求
        req_id = await gateway.request(
            type="replan",
            context={"old_node": "A", "new_node": "B"},
            callback=my_callback,
        )

        # 用户响应
        await gateway.resolve(req_id, approved=True)
    """

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._pending: dict[str, ConfirmationRequest] = {}
        self._resolved: dict[str, ConfirmationRequest] = {}

    async def request(
        self,
        type: str,
        context: dict,
        title: str = "",
        description: str = "",
        options: list[dict] | None = None,
        callback: Callable | None = None,
        timeout_s: int = 120,
    ) -> str:
        """
        请求用户确认。

        返回 request_id。需配合 resolve() 使用。

        callback 签名: async def callback(approved: bool, modifications: dict, ctx: Any)
        第三个参数 ctx 是发布事件时的 ExecutionContext
        """
        req_id = f"cfm_{uuid.uuid4().hex[:8]}"
        req = ConfirmationRequest(
            request_id=req_id,
            type=type,
            context=context,
            title=title,
            description=description,
            options=options or [],
            callback=callback,
            timeout_s=timeout_s,
            status="pending",
            created_at=__import__("time").time(),
        )
        self._pending[req_id] = req

        # 通知外部
        if self._event_bus:
            # 这里不传 ctx，由调用方在 emit 时提供
            pass

        # 后台超时 watcher
        asyncio.create_task(self._timeout_watcher(req_id))

        logger.info("[Confirmation] 请求 %s: %s (%s)", req_id[:12], type, title)
        return req_id

    async def resolve(self, req_id: str, approved: bool,
                      modifications: dict | None = None,
                      ctx: Any = None) -> ConfirmationRequest | None:
        """用户对确认请求作出响应"""
        req = self._pending.get(req_id)
        if not req:
            logger.warning("[Confirmation] 请求 %s 不存在或已处理", req_id[:12])
            return None

        req.status = "confirmed" if approved else "rejected"
        self._pending.pop(req_id, None)
        self._resolved[req_id] = req

        logger.info("[Confirmation] %s: %s", req_id[:12], req.status)

        # 执行回调
        if req.callback:
            try:
                result = req.callback(approved, modifications or {}, ctx)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("[Confirmation] 回调异常: %s", e, exc_info=True)

        return req

    def get_pending(self, req_id: str) -> ConfirmationRequest | None:
        return self._pending.get(req_id)

    def has_pending(self) -> bool:
        return len(self._pending) > 0

    def list_pending(self) -> list[ConfirmationRequest]:
        return list(self._pending.values())

    # ── 超时 ──

    async def _timeout_watcher(self, req_id: str):
        """超时自动拒绝"""
        await asyncio.sleep(self._pending.get(req_id, ConfirmationRequest(
            request_id=req_id, type="", timeout_s=120
        )).timeout_s)

        if req_id in self._pending:
            logger.info("[Confirmation] %s 超时 %ds，自动拒绝", req_id[:12],
                        self._pending[req_id].timeout_s)
            await self.resolve(req_id, False, {"reason": "timeout"})
