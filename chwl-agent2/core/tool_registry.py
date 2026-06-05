"""
Tool Registry — 工具注册与调用中心

职责：
1. 注册 11 个工具的定义（endpoint、timeout、retry 策略）
2. 支持内存 mock handler（开发/测试用）和 HTTP 调用（接队友 API）
3. 内置 retry 机制（指数退避）
4. 熔断保护（连续失败超过阈值自动断连）
5. 并行调用编排（invoke_parallel）

关键设计原则：
- 从 Orchestrator 视角看，调一个工具 = 调一个函数
- 不管背后是 mock 还是 HTTP，Orchestrator 不需要知道
- 队友 API 就绪 = 改一行 base_url 的事
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─── 异常 ─────────────────────────────────────────────────────

class ToolError(Exception):
    """工具调用失败"""
    def __init__(self, tool_name: str, message: str, retryable: bool = True):
        self.tool_name = tool_name
        self.retryable = retryable
        super().__init__(f"[{tool_name}] {message}")


class ToolTimeoutError(ToolError):
    def __init__(self, tool_name: str, timeout_ms: int):
        super().__init__(tool_name, f"超时 {timeout_ms}ms", retryable=True)


class ToolInvalidInputError(ToolError):
    def __init__(self, tool_name: str, message: str):
        super().__init__(tool_name, message, retryable=False)


class CircuitBreakerOpenError(ToolError):
    def __init__(self, tool_name: str):
        super().__init__(tool_name, "熔断器已打开", retryable=False)


class ToolNotFoundError(ToolError):
    def __init__(self, tool_name: str):
        super().__init__(tool_name, f"未注册的工具: {tool_name}", retryable=False)


# ─── 工具定义 ─────────────────────────────────────────────────

@dataclass
class ToolDefinition:
    """一个工具的完整定义"""
    name: str                              # 工具名，如 "weather"
    endpoint: str = ""                     # API 路径，如 "/api/weather"
    method: str = "POST"
    description: str = ""
    timeout_ms: int = 5000                 # 单次超时
    max_retries: int = 2                   # 最大重试次数
    retry_delay_ms: int = 1000             # 基础重试延迟
    critical: bool = False                 # 关键路径? (失败触发熔断)
    cache_ttl_ms: Optional[int] = None     # 缓存时间 (None=不缓存)


# ─── 工具注册表 ──────────────────────────────────────────────

class ToolRegistry:
    """
    工具注册表。

    提供：
    - register_mock(name, handler) — 注册内存 handler（开发/测试用）
    - invoke(name, payload) — 调用单个工具（含 retry + 熔断）
    - invoke_parallel(calls) — 并行调用多个工具
    - set_base_url(url) — 切换到 HTTP 模式（接队友 API）

    用法:
        registry = ToolRegistry(base_url="http://mock-api:8000")  # HTTP 模式
        registry.register_mock("weather", my_handler)              # mock 模式
        result = await registry.invoke("weather", {"city": "北京"})
    """

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url
        self._tools: dict[str, ToolDefinition] = {}
        self._mock_handlers: dict[str, Callable] = {}
        self._cache: dict[str, tuple[float, dict]] = {}

        # 熔断器状态: tool_name -> {"failure_count": int, "open_until": float}
        self._circuit_breakers: dict[str, dict] = {}
        self._circuit_threshold = 5         # 连续失败 N 次后熔断
        self._circuit_reset_seconds = 30    # 熔断后 N 秒后自动半开

        self._register_defaults()

    # ── 注册 ──

    def _register_defaults(self):
        """注册 11 个默认工具"""
        defaults = [
            ("location",            "/api/location/current",       False, "获取当前位置"),
            ("user_context",        "/api/user/context",           False, "解析用户输入"),
            ("weather",             "/api/weather",                False, "查询天气"),
            ("activities_search",   "/api/activities/search",      False, "搜索活动"),
            ("restaurants_search",  "/api/restaurants/search",     False, "搜索餐厅"),
            ("route_check",         "/api/route/check",            False, "查询路线"),
            ("candidates_score",    "/api/candidates/score",       True,  "候选评分(LLM)"),
            ("itinerary_generate",  "/api/itinerary/generate",     True,  "生成行程"),
            ("booking_execute",     "/api/booking/execute",        False, "执行履约"),
            ("booking_status",      "/api/booking/status",         False, "查询履约状态"),
            ("itinerary_replan",    "/api/itinerary/replan",       False, "动态重规划"),
        ]
        for name, endpoint, critical, desc in defaults:
            self._tools[name] = ToolDefinition(
                name=name, endpoint=endpoint, critical=critical,
                description=desc,
            )

    def register_mock(self, name: str, handler: Callable):
        """注册内存 mock handler"""
        if name not in self._tools:
            self._tools[name] = ToolDefinition(name=name, endpoint=f"/mock/{name}")
        self._mock_handlers[name] = handler
        logger.debug("[ToolRegistry] 注册 mock: %s", name)

    def register_tool(self, definition: ToolDefinition):
        """注册/覆盖工具定义"""
        self._tools[definition.name] = definition

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def set_base_url(self, url: str):
        self._base_url = url

    def clear_cache(self):
        self._cache.clear()

    # ── 熔断 ──

    def _check_circuit_breaker(self, tool_name: str):
        cb = self._circuit_breakers.get(tool_name)
        if not cb:
            return
        open_until = cb.get("open_until", 0)
        if open_until == 0:
            return  # 从未熔断过
        if time.time() < open_until:
            raise CircuitBreakerOpenError(tool_name)
        # 半开：重置失败计数
        cb["failure_count"] = 0
        cb["open_until"] = 0

    def _record_failure(self, tool_name: str):
        cb = self._circuit_breakers.setdefault(tool_name, {
            "failure_count": 0, "open_until": 0,
        })
        cb["failure_count"] += 1
        if cb["failure_count"] >= self._circuit_threshold:
            cb["open_until"] = time.time() + self._circuit_reset_seconds
            logger.warning("[CircuitBreaker] %s 熔断开启 %ds", tool_name, self._circuit_reset_seconds)

    def _record_success(self, tool_name: str):
        self._circuit_breakers.pop(tool_name, None)

    # ── 调用 ──

    async def invoke(self, tool_name: str, payload: dict) -> dict:
        """
        调用单个工具。

        返回格式: {"success": True, "data": {...}}
                  或 {"success": False, "error": {"code": "...", "message": "..."}}
        """
        if tool_name not in self._tools:
            return {"success": False, "error": {"code": "not_found",
                    "message": f"工具 '{tool_name}' 未注册"}}

        tool = self._tools[tool_name]

        # 1. 检查熔断
        try:
            self._check_circuit_breaker(tool_name)
        except CircuitBreakerOpenError as e:
            return {"success": False, "error": {"code": "circuit_open", "message": str(e)}}

        # 2. 检查缓存
        if tool.cache_ttl_ms:
            cached = self._cache.get(tool_name)
            if cached and (time.time() - cached[0]) * 1000 < tool.cache_ttl_ms:
                return cached[1]

        # 3. 执行调用（含 retry）
        last_error = None
        for attempt in range(tool.max_retries + 1):
            try:
                result = await self._do_invoke(tool, payload)

                # 检查业务错误
                if isinstance(result, dict) and result.get("success") is False:
                    error = result.get("error", {})
                    is_retryable = error.get("is_retryable", False)
                    if is_retryable and attempt < tool.max_retries:
                        delay = tool.retry_delay_ms / 1000 * (2 ** attempt)
                        logger.debug("[%s] 重试 %d/%d (等待 %.1fs): %s",
                                     tool_name, attempt + 1, tool.max_retries, delay, error.get("code"))
                        await asyncio.sleep(delay)
                        continue
                    self._record_failure(tool_name)
                    return result

                # 成功
                self._record_success(tool_name)
                if tool.cache_ttl_ms:
                    self._cache[tool_name] = (time.time(), result)
                return result

            except ToolError as e:
                last_error = e
                if e.retryable and attempt < tool.max_retries:
                    delay = tool.retry_delay_ms / 1000 * (2 ** attempt)
                    logger.debug("[%s] 重试 %d/%d: %s", tool_name, attempt + 1, tool.max_retries, e)
                    await asyncio.sleep(delay)
                    continue
                self._record_failure(tool_name)
                return {"success": False, "error": {
                    "code": "tool_error", "message": str(e), "is_retryable": e.retryable,
                }}

            except Exception as e:
                last_error = e
                if attempt < tool.max_retries:
                    delay = tool.retry_delay_ms / 1000 * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue

        self._record_failure(tool_name)
        return {"success": False, "error": {
            "code": "max_retries_exceeded",
            "message": f"重试 {tool.max_retries} 次均失败: {last_error}",
        }}

    async def _do_invoke(self, tool: ToolDefinition, payload: dict) -> dict:
        """实际执行调用（mock handler or HTTP）"""

        # mock 模式
        if tool.name in self._mock_handlers:
            handler = self._mock_handlers[tool.name]
            result = handler(payload)
            if hasattr(result, "__await__"):
                result = await result
            # 如果 handler 返回了标准格式，直接使用
            if isinstance(result, dict) and "success" in result:
                return result
            # 否则包装成标准格式
            return {"success": True, "data": result}

        # HTTP 模式（接队友 API）
        if self._base_url:
            import httpx
            url = f"{self._base_url.rstrip('/')}{tool.endpoint}"
            async with httpx.AsyncClient(timeout=tool.timeout_ms / 1000) as client:
                if tool.method == "GET":
                    resp = await client.get(url, params=payload)
                else:
                    resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()

        # 既没有 mock handler 也没有 base_url
        raise ToolError(tool.name, "既未注册 mock handler 也未配置 base_url")

    async def invoke_parallel(self, calls: list[tuple[str, dict]]) -> dict[str, dict]:
        """
        并行调用多个工具。

        参数: [(tool_name, payload), ...]
        返回: {tool_name: result_dict, ...}

        即使有调用失败，也不会影响其他并行调用。
        每个结果中通过 success 字段判断是否成功。
        兼容 Python 3.9（不使用 TaskGroup）。
        """
        async def _safe_invoke(name: str, pl: dict) -> tuple[str, dict]:
            result = await self.invoke(name, pl)
            return name, result

        tasks = [asyncio.create_task(_safe_invoke(name, pl))
                 for name, pl in calls]
        done, _ = await asyncio.wait(tasks)

        results = {}
        for task in done:
            name, result = task.result()
            results[name] = result
        return results
