"""Reliable tool invocation layer.

This adapts the teammate ToolRegistry idea for the current backend. It provides
retry, timeout, circuit breaker, cache, and parallel invocation while callers can
keep their existing function names and return shapes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class ToolError(Exception):
    def __init__(self, tool_name: str, message: str, retryable: bool = True):
        self.tool_name = tool_name
        self.retryable = retryable
        super().__init__(f"[{tool_name}] {message}")


class CircuitBreakerOpenError(ToolError):
    def __init__(self, tool_name: str):
        super().__init__(tool_name, "circuit breaker is open", retryable=False)


class ToolNotFoundError(ToolError):
    def __init__(self, tool_name: str):
        super().__init__(tool_name, f"tool not registered: {tool_name}", retryable=False)


@dataclass
class ToolDefinition:
    name: str
    timeout_ms: int = 6000
    max_retries: int = 2
    retry_delay_ms: int = 250
    critical: bool = False
    cache_ttl_ms: Optional[int] = None


Handler = Callable[[dict[str, Any]], Awaitable[Any] | Any]


class ToolRegistry:
    def __init__(self, circuit_threshold: int = 5, circuit_reset_seconds: int = 30):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Handler] = {}
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._circuit_breakers: dict[str, dict[str, float | int]] = {}
        self._circuit_threshold = circuit_threshold
        self._circuit_reset_seconds = circuit_reset_seconds

    def register_tool(self, definition: ToolDefinition, handler: Handler | None = None) -> None:
        self._tools[definition.name] = definition
        if handler is not None:
            self._handlers[definition.name] = handler

    def register_handler(self, name: str, handler: Handler) -> None:
        if name not in self._tools:
            self._tools[name] = ToolDefinition(name=name)
        self._handlers[name] = handler

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def invoke(self, tool_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        tool = self._tools.get(tool_name)
        handler = self._handlers.get(tool_name)
        if tool is None or handler is None:
            return _failure("not_found", f"tool not registered: {tool_name}", retryable=False)

        try:
            self._check_circuit_breaker(tool_name)
        except CircuitBreakerOpenError as exc:
            return _failure("circuit_open", str(exc), retryable=False)

        cache_key = self._cache_key(tool_name, payload)
        if tool.cache_ttl_ms:
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) * 1000 < tool.cache_ttl_ms:
                return cached[1]

        last_error: Exception | None = None
        for attempt in range(tool.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    _call_handler(handler, payload),
                    timeout=tool.timeout_ms / 1000,
                )
                wrapped = self._wrap_result(result)

                if wrapped.get("success") is False:
                    error = wrapped.get("error", {})
                    if error.get("is_retryable") and attempt < tool.max_retries:
                        await self._sleep_retry(tool, attempt)
                        continue
                    self._record_failure(tool_name)
                    return wrapped

                self._record_success(tool_name)
                if tool.cache_ttl_ms:
                    self._cache[cache_key] = (time.time(), wrapped)
                return wrapped
            except asyncio.TimeoutError as exc:
                last_error = exc
                if attempt < tool.max_retries:
                    await self._sleep_retry(tool, attempt)
                    continue
            except ToolError as exc:
                last_error = exc
                if exc.retryable and attempt < tool.max_retries:
                    await self._sleep_retry(tool, attempt)
                    continue
            except Exception as exc:
                last_error = exc
                if attempt < tool.max_retries:
                    await self._sleep_retry(tool, attempt)
                    continue

        self._record_failure(tool_name)
        return _failure("max_retries_exceeded", str(last_error or "unknown error"), retryable=False)

    async def invoke_parallel(self, calls: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        duplicate_names = {name for name in [call[0] for call in calls] if [call[0] for call in calls].count(name) > 1}

        async def _one(index: int, name: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            key = f"{name}#{index}" if name in duplicate_names else name
            return key, await self.invoke(name, payload)

        results = await asyncio.gather(
            *[_one(index, name, payload) for index, (name, payload) in enumerate(calls)],
            return_exceptions=True,
        )
        out: dict[str, dict[str, Any]] = {}
        for item, original in zip(results, calls):
            name = original[0]
            if isinstance(item, Exception):
                out[name] = _failure("invoke_parallel_error", str(item), retryable=False)
            else:
                out[item[0]] = item[1]
        return out

    def _check_circuit_breaker(self, tool_name: str) -> None:
        cb = self._circuit_breakers.get(tool_name)
        if not cb:
            return
        open_until = float(cb.get("open_until", 0))
        if open_until and time.time() < open_until:
            raise CircuitBreakerOpenError(tool_name)
        if open_until:
            cb["failure_count"] = 0
            cb["open_until"] = 0

    def _record_failure(self, tool_name: str) -> None:
        cb = self._circuit_breakers.setdefault(tool_name, {"failure_count": 0, "open_until": 0})
        cb["failure_count"] = int(cb.get("failure_count", 0)) + 1
        if int(cb["failure_count"]) >= self._circuit_threshold:
            cb["open_until"] = time.time() + self._circuit_reset_seconds
            logger.warning("[ToolRegistry] circuit opened for %s", tool_name)

    def _record_success(self, tool_name: str) -> None:
        self._circuit_breakers.pop(tool_name, None)

    async def _sleep_retry(self, tool: ToolDefinition, attempt: int) -> None:
        await asyncio.sleep((tool.retry_delay_ms / 1000) * (2 ** attempt))

    @staticmethod
    def _wrap_result(result: Any) -> dict[str, Any]:
        if isinstance(result, dict) and "success" in result:
            return result
        return {"success": True, "data": result}

    @staticmethod
    def _cache_key(tool_name: str, payload: dict[str, Any]) -> str:
        return f"{tool_name}:{repr(sorted(payload.items()))}"


async def _call_handler(handler: Handler, payload: dict[str, Any]) -> Any:
    result = handler(payload)
    if hasattr(result, "__await__"):
        return await result
    return result


def _failure(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "is_retryable": retryable},
    }
