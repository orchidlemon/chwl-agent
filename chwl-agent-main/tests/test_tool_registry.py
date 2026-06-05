"""
Tool Registry 测试 — 调用、重试、熔断、并行
"""
import pytest
from core.tool_registry import ToolRegistry, ToolDefinition


class TestToolRegistry:
    """工具注册与调用"""

    @pytest.mark.asyncio
    async def test_invoke_mock_handler(self):
        registry = ToolRegistry()
        registry.register_mock("ping", lambda p: {"pong": True, "data": p})

        result = await registry.invoke("ping", {"msg": "hello"})
        assert result["success"] is True
        assert result["data"]["pong"] is True

    @pytest.mark.asyncio
    async def test_invoke_parallel(self):
        registry = ToolRegistry()
        registry.register_mock("a", lambda p: {"result": "A"})
        registry.register_mock("b", lambda p: {"result": "B"})

        results = await registry.invoke_parallel([
            ("a", {"x": 1}),
            ("b", {"y": 2}),
        ])
        assert results["a"]["data"]["result"] == "A"
        assert results["b"]["data"]["result"] == "B"

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        """超时重试"""
        call_count = [0]

        def flaky_handler(p):
            call_count[0] += 1
            if call_count[0] < 3:
                return {"success": False, "error": {
                    "code": "timeout", "message": "timeout", "is_retryable": True}}
            return {"success": True, "data": {"ok": True}}

        registry = ToolRegistry()
        registry.register_mock("flaky", flaky_handler)
        tool = registry.get_tool("flaky")
        tool.max_retries = 3
        tool.retry_delay_ms = 10

        result = await registry.invoke("flaky", {})
        assert result["success"] is True
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """熔断器 — 连续失败 N 次后打开"""
        call_count = [0]

        def always_fail(p):
            call_count[0] += 1
            return {"success": False, "error": {
                "code": "internal_error", "message": "fail", "is_retryable": False}}

        registry = ToolRegistry()
        registry._circuit_threshold = 3
        registry._circuit_reset_seconds = 60
        registry.register_mock("bad", always_fail)

        # 前 3 次都返回失败（但不会熔断因为计数未到阈值后记录）
        for i in range(3):
            result = await registry.invoke("bad", {})
            assert result["success"] is False, f"Call {i+1} should fail"

        # 第 4 次应该熔断
        result = await registry.invoke("bad", {})
        assert result["success"] is False
        assert result["error"]["code"] == "circuit_open", (
            f"Expected circuit_open, got: {result.get('error', {})}")
        # 熔断后 handler 不会被调用
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        registry = ToolRegistry()
        result = await registry.invoke("nonexistent", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """缓存命中"""
        call_count = [0]

        def handler(p):
            call_count[0] += 1
            return {"value": call_count[0]}

        registry = ToolRegistry()
        registry.register_mock("cached", handler)
        registry.get_tool("cached").cache_ttl_ms = 60000

        r1 = await registry.invoke("cached", {})
        r2 = await registry.invoke("cached", {})
        assert r1["data"]["value"] == 1
        assert r2["data"]["value"] == 1  # 缓存命中，仍然是 1
        assert call_count[0] == 1
