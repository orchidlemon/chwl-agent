"""User confirmation gateway for costly or irreversible actions."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


ConfirmationCallback = Callable[[bool, dict[str, Any], Any], Awaitable[None] | None]


@dataclass
class ConfirmationRequest:
    request_id: str
    type: str
    context: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    description: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    timeout_s: int = 120
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    resolution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "type": self.type,
            "context": self.context,
            "title": self.title,
            "description": self.description,
            "options": self.options,
            "timeout_s": self.timeout_s,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }


class ConfirmationGateway:
    """Tracks confirmation requests in SessionManager and fails closed on timeout."""

    def __init__(self, manager):
        self.manager = manager
        self._callbacks: dict[str, ConfirmationCallback] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    async def request(
        self,
        session_id: str,
        type: str,
        context: dict[str, Any],
        title: str = "",
        description: str = "",
        options: list[dict[str, Any]] | None = None,
        timeout_s: int = 120,
        callback: ConfirmationCallback | None = None,
    ) -> ConfirmationRequest:
        request_id = f"cfm_{uuid.uuid4().hex[:8]}"
        req = ConfirmationRequest(
            request_id=request_id,
            type=type,
            context=context,
            title=title,
            description=description,
            options=options or [
                {"label": "同意", "value": "approve", "recommended": True},
                {"label": "保留原方案", "value": "reject", "recommended": False},
            ],
            timeout_s=timeout_s,
        )
        session = self.manager.get(session_id)
        if session is None:
            raise ValueError(f"session not found: {session_id}")
        session.setdefault("pending_confirmations", {})[request_id] = req.to_dict()
        session.setdefault("confirmation_history", []).append({
            "request_id": request_id,
            "type": type,
            "status": "pending",
            "time": time.strftime("%H:%M:%S"),
        })
        session["confirmation_history"] = session["confirmation_history"][-50:]
        if callback:
            self._callbacks[request_id] = callback
        self._timeout_tasks[request_id] = asyncio.create_task(
            self._timeout(session_id, request_id, timeout_s),
            name=f"confirmation_timeout_{request_id}",
        )
        self.manager.add_monitor_event(
            session_id,
            "confirmation_gateway",
            f"等待用户确认: {title or type}",
            "confirmation_pending",
        )
        return req

    async def resolve(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        modifications: dict[str, Any] | None = None,
        ctx: Any = None,
        reason: str = "",
    ) -> Optional[ConfirmationRequest]:
        session = self.manager.get(session_id)
        if session is None:
            return None
        pending = session.setdefault("pending_confirmations", {})
        raw = pending.pop(request_id, None)
        if not raw:
            return None

        task = self._timeout_tasks.pop(request_id, None)
        if task and not task.done():
            task.cancel()

        req = ConfirmationRequest(**raw)
        req.status = "confirmed" if approved else "rejected"
        req.resolved_at = time.time()
        req.resolution = {
            "approved": approved,
            "modifications": modifications or {},
            "reason": reason,
        }
        session.setdefault("resolved_confirmations", {})[request_id] = req.to_dict()
        session.setdefault("confirmation_history", []).append({
            "request_id": request_id,
            "type": req.type,
            "status": req.status,
            "time": time.strftime("%H:%M:%S"),
            "reason": reason,
        })
        session["confirmation_history"] = session["confirmation_history"][-50:]

        callback = self._callbacks.pop(request_id, None)
        if callback:
            result = callback(approved, modifications or {}, ctx)
            if hasattr(result, "__await__"):
                await result
        self.manager.add_monitor_event(
            session_id,
            "confirmation_gateway",
            f"用户确认结果: {req.type} -> {req.status}",
            "confirmation_resolved",
        )
        return req

    def get_pending(self, session_id: str, request_id: str) -> Optional[dict[str, Any]]:
        session = self.manager.get(session_id)
        if not session:
            return None
        return session.setdefault("pending_confirmations", {}).get(request_id)

    def find_latest_pending(self, session_id: str, type: str | None = None) -> Optional[dict[str, Any]]:
        session = self.manager.get(session_id)
        if not session:
            return None
        pending = list(session.setdefault("pending_confirmations", {}).values())
        if type:
            pending = [req for req in pending if req.get("type") == type]
        if not pending:
            return None
        return sorted(pending, key=lambda req: req.get("created_at", 0))[-1]

    def list_pending(self, session_id: str) -> list[dict[str, Any]]:
        session = self.manager.get(session_id)
        if not session:
            return []
        return list(session.setdefault("pending_confirmations", {}).values())

    async def cancel_session(self, session_id: str) -> None:
        session = self.manager.get(session_id)
        if session:
            for request_id in list(session.setdefault("pending_confirmations", {}).keys()):
                await self.resolve(session_id, request_id, False, reason="session_cancelled")

    async def _timeout(self, session_id: str, request_id: str, timeout_s: int) -> None:
        try:
            await asyncio.sleep(timeout_s)
            req = self.get_pending(session_id, request_id)
            if not req:
                return
            await self.resolve(session_id, request_id, False, reason="timeout")
            session = self.manager.get(session_id)
            if session is not None:
                session["pending_monitor_msg"] = {
                    "type": "monitor_alert",
                    "severity": "medium",
                    "message": "确认请求已超时，已为你保留原方案。",
                    "request_id": request_id,
                }
        except asyncio.CancelledError:
            return
