"""
LLM Orchestrator — 用 DeepSeek 驱动的完整 Agent 循环

将 LLM Planner 注入到 Orchestrator 的 ToolRegistry 中，
替换所有 mock handler，实现真正的 LLM 推理规划。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from core.llm_client import LLMClient
from core.llm_planner import LLMPlanner
from core.tool_registry import ToolRegistry
from mocks.teammate_adapter import TeammateAPIAdapter
from mocks import MockBackend
from orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

DEFAULT_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "") or ""


def create_llm_orchestrator(
    api_key: str = DEFAULT_API_KEY,
    use_teammate_api: bool = False,
) -> Orchestrator:
    """
    创建 LLM 驱动的 Orchestrator。

    架构:
        LLM Planner (DeepSeek)  ← 替换 candidates_score / itinerary_generate / user_context / replan
              ↓
        Tool Registry            ← 事实数据通过队友 API 或内存 mock
              ↓
        Orchestrator             ← 状态机 / 事件总线 / 确认网关 / 后台监控
              ↓
        CLI / FastAPI            ← 交互层

    参数:
        api_key: DeepSeek API Key
        use_teammate_api: 是否使用队友 HTTP API（需要先启动 mock_api/app.py）
    """
    # 1. LLM
    llm = LLMClient(api_key=api_key)
    planner = LLMPlanner(llm)

    # 2. Tool Registry
    tools = ToolRegistry()

    # 注册 LLM 推理 handler
    tools.register_mock("user_context", planner.handle_user_context)
    tools.register_mock("candidates_score", planner.handle_candidates_score)
    tools.register_mock("itinerary_generate", planner.handle_itinerary_generate)
    tools.register_mock("itinerary_replan", planner.handle_itinerary_replan)

    # 事实数据来源
    if use_teammate_api:
        adapter = TeammateAPIAdapter("http://127.0.0.1:8000")
        tools.register_mock("activities_search", adapter.activities_search)
        tools.register_mock("restaurants_search", adapter.restaurants_search)
        tools.register_mock("weather", adapter.weather)
        tools.register_mock("route_check", adapter.route_check)
        tools.register_mock("booking_status", adapter.booking_status)
        tools.register_mock("booking_execute", adapter.open_fulfillment_link)
        logger.info("[LLM] 事实数据源: 队友 API (http://127.0.0.1:8000)")
    else:
        mem = MockBackend()
        for name in ["location", "weather", "activities_search",
                     "restaurants_search", "route_check",
                     "booking_execute", "booking_status"]:
            handler = getattr(mem, f"handle_{name}")
            tools.register_mock(name, handler)
        logger.info("[LLM] 事实数据源: 内存 Mock")

    # 3. Orchestrator
    orch = Orchestrator(tools)
    logger.info("[LLM] Orchestrator 创建完成（LLM 模式）")
    return orch, planner
