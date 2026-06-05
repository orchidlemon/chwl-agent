"""
Chat Demo — LLM 驱动的 AI Agent 对话交互

真正的 Agent 循环：
  用户输入 → 展示工具调用过程 → LLM 规划 → 展示方案 → 等待下一步

用法:
    python -X utf-8 scripts/chat_demo.py

支持的自然语言交互:
    "下午带老婆孩子出去玩"     → 规划行程
    "老婆在减肥"              → 更新偏好，重规划
    "确认" / "行"            → 开始履约
    "孩子累了"               → 触发异常重规划
    "取消"                  → 取消行程
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_client import LLMClient
from core.llm_planner import LLMPlanner
from core.tool_registry import ToolRegistry
from core.models import UserSentiment
from mocks import MockBackend
from mocks.teammate_adapter import TeammateAPIAdapter
from orchestrator.orchestrator import Orchestrator
from orchestrator.event_bus import EventBus

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-67937130fddf4be086e73e7b2f6d293c")

# ── 安全打印（兼容 GBK 终端） ──────────────────────────────

def sp(text: str):
    """safe print"""
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        try:
            print(safe)
        except Exception:
            pass


# ── Agent ──────────────────────────────────────────────────

class ChatAgent:
    """
    真正 AI Agent 对话循环。

    改进：
    1. 展示工具调用过程（EventBus 事件实时输出）
    2. 修复 cancelled 状态竞态条件
    3. 偏好更新触发重新规划
    """

    def __init__(self, use_teammate: bool = False):
        self.llm = LLMClient(api_key=API_KEY)
        self.planner = LLMPlanner(self.llm)
        self.session_id = ""

        self.event_bus = EventBus()
        self.orchestrator = self._build(use_teammate)
        self._subscribe_events()

    def _build(self, use_teammate: bool) -> Orchestrator:
        tools = ToolRegistry()

        # LLM 推理 handler
        tools.register_mock("user_context", self.planner.handle_user_context)
        tools.register_mock("candidates_score", self.planner.handle_candidates_score)
        tools.register_mock("itinerary_generate", self.planner.handle_itinerary_generate)
        tools.register_mock("itinerary_replan", self.planner.handle_itinerary_replan)

        # 事实数据源
        if use_teammate:
            adapter = TeammateAPIAdapter("http://127.0.0.1:8000")
            for name in ["activities_search", "restaurants_search", "weather",
                         "route_check", "booking_status", "booking_execute"]:
                tools.register_mock(name, getattr(adapter, name))
        else:
            mem = MockBackend()
            for name in ["location", "weather", "activities_search",
                         "restaurants_search", "route_check",
                         "booking_execute", "booking_status"]:
                tools.register_mock(name, getattr(mem, f"handle_{name}"))

        return Orchestrator(tools, event_bus=self.event_bus)

    def _subscribe_events(self):
        """订阅 EventBus 事件 → 实时展示工具调用过程"""
        self.event_bus.subscribe("status_update", self._on_status)
        self.event_bus.subscribe("tool_warning", self._on_tool_warning)
        self.event_bus.subscribe("plan_complete", self._on_plan_complete)
        self.event_bus.subscribe("plan_failed", self._on_plan_failed)
        self.event_bus.subscribe("execution_started", self._on_exec_start)
        self.event_bus.subscribe("booking_status_changed", self._on_booking)
        self.event_bus.subscribe("execution_complete", self._on_exec_done)
        self.event_bus.subscribe("node_failed", self._on_node_fail)
        self.event_bus.subscribe("replan_ready", self._on_replan_ready)

    # ── 事件处理 ──

    def _on_status(self, ctx, event):
        msg = event["data"].get("message", "")
        if msg:
            sp(f"  . {msg}")

    def _on_tool_warning(self, ctx, event):
        err = event["data"].get("error", {})
        sp(f"  [!] {event['data']['tool']}: {err.get('code', '')}")

    def _on_plan_complete(self, ctx, event):
        itin = event["data"].get("itinerary", {})
        nodes = itin.get("nodes", [])
        sp(f"\n[规划完成] 找到 {len(nodes)} 个活动")
        for n in nodes:
            sp(f"  - {n.get('scheduled_start','')} {n.get('poi_name','')} ({n.get('duration_min','')}min)")

    def _on_plan_failed(self, ctx, event):
        sp(f"  [!] 规划失败: {event['data'].get('error','')}")

    def _on_exec_start(self, ctx, event):
        sp(f"\n[履约] 开始预约...")

    def _on_booking(self, ctx, event):
        name = event["data"].get("name", "")
        st = event["data"].get("status", "")
        sp(f"  . {name}: {st}")

    def _on_exec_done(self, ctx, event):
        sp(f"\n[履约完成] 所有预订成功")

    def _on_node_fail(self, ctx, event):
        name = event["data"].get("name", "")
        level = event["data"].get("fallback_level", "?")
        sp(f"  [!] {name} 失败 (L{level})")

    def _on_replan_ready(self, ctx, event):
        sp(f"  [重规划] 调整方案已就绪")

    # ── 核心入口 ──

    async def start(self):
        """启动 Agent"""
        self.session_id = await self.orchestrator.start_session("")
        sp("\n[Agent] 你好！今天下午有什么安排？")
        sp("        比如：带我老婆孩子出去玩一下午\n")

    async def handle_message(self, user_input: str) -> str:
        """处理用户消息"""
        if not self.session_id:
            return "系统还没准备好"

        # 保存用户输入到对话历史
        self.conversation_history.append({"role": "user", "content": user_input})

        # 取消
        if any(k in user_input for k in ("取消", "不去了", "算了", "再见")):
            await self.orchestrator.cancel_session(self.session_id)
            reply = "好的，已取消。下次需要随时找我。"
            self.conversation_history.append({"role": "assistant", "content": reply})
            return reply

        # 获取状态
        status = await self.orchestrator.get_status(self.session_id)
        state = status.itinerary_state

        # ── DRAFT / INIT：启动新规划 ──
        if state in ("init", "draft"):
            return await self._do_plan(user_input)

        # ── PENDING_CONFIRM：已有方案，判断下一步 ──
        elif state == "pending_confirm":
            # 用户确认 → 履约
            if any(k in user_input for k in ("确认", "好", "行", "安排", "可以", "就这样")):
                return await self._do_execute()

            # 用户提供新约束 → 重新规划
            if any(k in user_input for k in ("减肥", "清淡", "换", "改", "不要", "不行", "累", "远")):
                return await self._do_replan(user_input)

            # 默认：展示当前方案
            return self._show_plan(status.nodes, status.summary)

        # ── EXECUTING：履约中 ──
        elif state == "executing":
            if any(k in user_input for k in ("累", "困", "不想")):
                sentiment = UserSentiment(type="tired", description=user_input)
                await self.orchestrator.handle_user_sentiment(self.session_id, sentiment)
                return "收到，我调整一下后续安排。"
            return "正在预约中，稍等一会就好。"

        # ── COMPLETED：履约完成 ──
        elif state == "completed":
            # 取消
            if any(k in user_input for k in ("取消", "不去了", "算了")):
                await self.orchestrator.cancel_session(self.session_id)
                return "好的，已取消"
            # 新偏好 → 重新规划
            if any(k in user_input for k in ("减肥", "清淡", "换", "改", "累", "困")):
                sentiment = UserSentiment(type="tired", description=user_input)
                await self.orchestrator.handle_user_sentiment(self.session_id, sentiment)
                return "好的，我调整一下。"
            # 查看方案
            if any(k in user_input for k in ("看看", "方案", "什么")):
                return self._show_plan(status.nodes, status.summary)
            # 默认走 LLM 回复
            s = await self.orchestrator.get_status(self.session_id)
            return await self.planner.generate_response(
                {"phase": "completed", "nodes": s.nodes, "summary": s.summary}, user_input
            )

        # ── NEEDS_REPLAN ──
        elif state == "needs_replan":
            if any(k in user_input for k in ("好", "确认", "行", "换", "看看")):
                # 回到 pending_confirm 让用户重新确认
                s = await self.orchestrator.get_status(self.session_id)
                return self._show_plan(s.nodes, s.summary) + "\n您看这样可以吗？输入「确认」继续。"
            return "行程需要调整。输入「看看」查看调整方案，或输入新的需求重新规划。"

        # ── 兜底 ──
        return await self.planner.generate_response(
            {"phase": "unknown", "state": state, "nodes": status.nodes}, user_input
        )

    # ── 核心操作 ──

    async def _do_plan(self, user_input: str) -> str:
        """启动新规划，等待完成"""
        # 取消旧任务 + 旧会话
        if self.session_id:
            try:
                await self.orchestrator.cancel_session(self.session_id)
            except Exception:
                pass
            await asyncio.sleep(0.3)

        # 创建新会话（同步启动 Phase 1）
        self.session_id = await self.orchestrator.start_session(user_input)

        # 等待 Phase 1 完成
        for i in range(40):
            await asyncio.sleep(1)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state == "pending_confirm":
                status = s
                break
            elif s.itinerary_state in ("executing", "completed", "needs_replan"):
                status = s
                break
        else:
            return "规划超时，请重试"

        if status.itinerary_state == "pending_confirm" and status.nodes:
            # LLM 生成回复（携带对话历史）
            context = {"phase": "plan_complete", "nodes": status.nodes, "summary": status.summary}
            if self.conversation_history:
                context["conversation_history"] = self.conversation_history[-6:]  # 最近 3 轮
            reply = await self.planner.generate_response(context, user_input)
            return reply
        return "我看看周边有什么合适的..."

    async def _do_replan(self, user_input: str) -> str:
        """用户更新偏好 → 重新规划"""
        sp(f"\n[重新规划] 根据您的反馈调整方案...")
        try:
            await self.orchestrator.cancel_session(self.session_id)
        except Exception:
            pass
        await asyncio.sleep(0.3)
        return await self._do_plan(user_input)

    async def _do_execute(self) -> str:
        """履约"""
        try:
            await self.orchestrator.confirm_itinerary(self.session_id)
        except Exception as e:
            return f"预约启动失败: {e}"
        # 等履约完成
        for i in range(20):
            await asyncio.sleep(1.5)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state in ("completed", "needs_replan"):
                break
        return "全部预约搞定！您按计划出发就行。"

    # ── 辅助 ──

    def _show_plan(self, nodes, summary) -> str:
        lines = ["目前方案是："]
        for n in nodes[:5]:
            lines.append(f"  - {n.get('start_time', '')} {n.get('name', '')}")
        lines.append("您看可以吗？")
        return "\n".join(lines)

def main():
    use_teammate = "--teammate" in sys.argv

    sp("=" * 50)
    sp("  Meituan AI Agent - LLM Chat Mode")
    sp("=" * 50)

    agent = ChatAgent(use_teammate=use_teammate)
    asyncio.run(agent.start())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while True:
            user_input = input("> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                sp("\n[Agent] 再见！")
                break
            sp("")
            response = loop.run_until_complete(agent.handle_message(user_input))
            agent.conversation_history.append({"role": "assistant", "content": response})
            sp(f"\n[Agent] {response}\n")
    except (KeyboardInterrupt, EOFError):
        sp("\n[Agent] 再见！")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
