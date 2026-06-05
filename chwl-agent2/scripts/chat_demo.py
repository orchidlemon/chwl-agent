"""
Chat Demo — LLM 驱动的 AI Agent 对话交互

真正的 Agent 循环：
  用户输入 → LLM 理解意图 → 执行操作（规划/履约/重规划/展示）→ 回复用户

用法:
    python -X utf-8 scripts/chat_demo.py

架构:
  handle_message → LLM 意图路由 → action dispatch → Orchestrator API → 回复用户
                   (30行 if/elif 替换为 Agent 循环)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
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


# ── Agent System Prompt ────────────────────────────────────

AGENT_PROMPT = """你是执行型助手「小美」，负责解析用户意图并路由到正确的后端操作。

【操作列表】
- plan: 用户给出出发时间 + 人数/人员构成时，立即触发
- clarify: 缺少出发时间或出行人数时，追问（每次只问一个信息）
- show_plan: 用户想看当前方案时
- replan: 用户对方案不满意，或提出新偏好/约束时
- confirm: 用户明确表示接受方案时
- cancel: 用户要取消或结束时
- chat: 其他情况

【触发规则——按优先级从高到低】

优先级 1（强制 clarify）:
- 用户表达出行意图，但没有出发时间 → clarify，问出发时间
- 用户表达出行意图，但没有人数/人员 → clarify，问几个人

优先级 2（强制 plan）:
- 用户同时给出了时间 + 人员信息 → plan，不要聊天
- 用户说"你安排""随便""都行" → plan（使用已知信息）

优先级 3（强制 replan）:
- 用户说"太早/太晚/太赶/换一个/重新规划/不喜欢" → replan
- 用户提出新偏好（"想吃火锅""不想走路"）→ replan

优先级 4（强制 show_plan）:
- 用户说"行程呢/结果呢/看看/方案在哪/展示一下" → show_plan

优先级 5（强制 confirm）:
- 用户说"好/行/确认/就这样/安排/可以" → confirm

【关键约束】
- plan 不是让你描述方案，是让后端去搜索真实数据
- clarify 每次只问一个问题，不要一次问多个
- replan 前不需要再次 clarify，直接用新偏好触发 replan
- response 最多 1-2 句，不列出行程细节

【当前系统状态】
行程状态: {state}
当前方案摘要: {plan_summary}

【输出格式（严格 JSON）】
{{"action": "plan|clarify|show_plan|replan|confirm|cancel|chat", "response": "对用户说的话，1-2句", "reason": "识别到的用户意图"}}
"""


# ── 安全打印（兼容 GBK） ──────────────────────────────────

def sp(text: str):
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
    """LLM 驱动的 Agent 对话循环"""

    def __init__(self, use_teammate: bool = False):
        self.llm = LLMClient(api_key=API_KEY)
        self.planner = LLMPlanner(self.llm)
        self.session_id = ""
        self.conversation_history: list[dict] = []
        self._plan_id: str | None = None      # plan 完成的唯一标识
        self._planning: bool = False           # single-flight 锁

        self.event_bus = EventBus()
        self.orchestrator = self._build(use_teammate)
        self._subscribe_events()

    def _build(self, use_teammate: bool) -> Orchestrator:
        tools = ToolRegistry()
        tools.register_mock("user_context", self.planner.handle_user_context)
        tools.register_mock("candidates_score", self.planner.handle_candidates_score)
        tools.register_mock("itinerary_generate", self.planner.handle_itinerary_generate)
        tools.register_mock("itinerary_replan", self.planner.handle_itinerary_replan)

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
        self.event_bus.subscribe("status_update", lambda ctx, e: sp(f"  . {e['data'].get('message','')}"))
        self.event_bus.subscribe("tool_warning", lambda ctx, e: sp(f"  [!] {e['data']['tool']}: {e['data'].get('error',{}).get('code','')}"))
        self.event_bus.subscribe("plan_complete", self._on_plan_complete)
        self.event_bus.subscribe("plan_failed", lambda ctx, e: sp(f"  [!] 规划失败"))
        self.event_bus.subscribe("execution_started", lambda ctx, e: sp(f"\n[履约] 开始预约..."))
        self.event_bus.subscribe("booking_status_changed", lambda ctx, e: sp(f"  . {e['data'].get('name','')}: {e['data'].get('status','')}"))
        self.event_bus.subscribe("execution_complete", lambda ctx, e: sp(f"\n[履约完成] 所有预订成功"))
        self.event_bus.subscribe("node_failed", lambda ctx, e: sp(f"  [!] {e['data'].get('name','')} 失败"))
        self.event_bus.subscribe("replan_ready", lambda ctx, e: sp(f"  [重规划] 调整方案已就绪"))
        self.event_bus.subscribe("resource_loss_warning", self._on_resource_loss)

    def _on_plan_complete(self, ctx, event):
        itin = event["data"].get("itinerary", {})
        nodes = itin.get("nodes", [])
        sp(f"\n[规划完成] 共 {len(nodes)} 个活动")
        for n in nodes:
            sp(f"  {n.get('scheduled_start','?')}-{n.get('scheduled_end','?')} {n.get('poi_name','')} ({n.get('duration_min',0)}min)")
        sp(f"  摘要: {itin.get('summary','')}")

    def _on_resource_loss(self, ctx, event):
        losses = event["data"].get("losses", [])
        if losses:
            sp(f"  [!] 取消将释放以下预约/资格：")
            for loss in losses:
                sp(f"      - {loss.get('name','')} ({loss.get('type',loss.get('reason',''))})")

    # ── Agent 循环 ──

    async def start(self):
        self.session_id = ""
        sp("\n[Agent] 你好！今天下午有什么安排？")

    async def _build_context(self) -> str:
        """构建当前状态摘要（异步）"""
        if not self.session_id:
            return json.dumps({"state": "none", "plan": "无"}, ensure_ascii=False)
        try:
            s = await self.orchestrator.get_status(self.session_id)
            nodes_text = "、".join(
                f"{n.get('start_time','')} {n.get('name','')}" for n in s.nodes[:3]
            ) or "无"
            return json.dumps({"state": s.itinerary_state, "plan": nodes_text}, ensure_ascii=False)
        except Exception:
            return json.dumps({"state": "unknown", "plan": "无"}, ensure_ascii=False)

    async def handle_message(self, user_input: str) -> str:
        """LLM 驱动的 Agent 路由 — 替代硬编码状态机"""
        self.conversation_history.append({"role": "user", "content": user_input})

        # 取消直接处理（不经过 LLM，避免漏判）
        if any(k in user_input for k in ("取消", "再见")):
            if self.session_id:
                await self.orchestrator.cancel_session(self.session_id)
                self.session_id = ""
            reply = "好的，已取消。下次需要随时找我。"
            self.conversation_history.append({"role": "assistant", "content": reply})
            return reply

        # 首次输入 → 创建 session（不主动规划，等 LLM 决定 action）
        if not self.session_id:
            self.session_id = await self.orchestrator.start_session(user_input)

        # 发给 LLM 做意图理解
        ctx = await self._build_context()
        ctx_data = json.loads(ctx)
        prompt = AGENT_PROMPT.format(state=ctx_data["state"], plan_summary=ctx_data["plan"])

        try:
            result = await self.llm.chat_json(
                system=prompt,
                messages=self.conversation_history[-8:],
                temperature=0.2,
            )
        except Exception:
            return "我没理解，您能再说一遍吗？"

        action = result.get("action", "chat")
        response = result.get("response", "您说")
        self.conversation_history.append({"role": "assistant", "content": response})

        # 带 guardrail 的 action dispatch
        try:
            status = await self.orchestrator.get_status(self.session_id)
            state = status.itinerary_state
        except Exception:
            state = "none"

        # 状态守卫层：非法 action 降级为 chat
        invalid_map = {
            "confirm": ["init", "draft", "none", "cancelled"],
            "replan": ["init", "none", "cancelled"],
            "cancel": ["cancelled", "none"],
            "plan": ["init", "none", "executing", "completed"],
            "show_plan": ["init", "none"],
        }
        if action in invalid_map and state in invalid_map[action]:
            action = "chat"

        # 执行（所有操作以 _plan_id 为唯一依据）
        if action == "plan":
            return await self._do_plan(user_input)
        elif action == "confirm":
            if not self._plan_id:
                return "方案尚未就绪，请稍后再确认"
            return await self._do_execute()
        elif action == "replan":
            self._plan_id = None
            return await self._do_replan(user_input)
        elif action == "show_plan":
            if not self._plan_id:
                return "正在为您规划行程，请稍等..."
            try:
                s = await self.orchestrator.get_status(self.session_id)
                return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"
            except Exception:
                return "正在为您规划行程，请稍等..."
        elif action == "cancel":
            await self.orchestrator.cancel_session(self.session_id)
            self.session_id = ""
            return "好的，已取消。"
        else:
            return response  # clarify 或 chat

    # ── 操作 ──

    async def _do_plan(self, user_input: str) -> str:
        # single-flight lock：已有规划任务则等待完成
        if self._planning:
            for _ in range(40):
                await asyncio.sleep(1)
                if self._plan_id:
                    break
            else:
                return "规划超时，请重试"
            s = await self.orchestrator.get_status(self.session_id)
            return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"

        self._planning = True
        self._plan_id = None

        if self.session_id:
            try:
                await self.orchestrator.cancel_session(self.session_id)
            except Exception:
                pass
            await asyncio.sleep(0.3)
        self.session_id = await self.orchestrator.start_session(user_input)

        # 等 Phase 1 完成
        for _ in range(40):
            await asyncio.sleep(1)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state in ("pending_confirm", "executing", "completed", "needs_replan"):
                self._plan_id = s.session_id + "_plan"
                self._planning = False
                return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"
        self._planning = False
        return "规划超时，请重试"

    async def _do_replan(self, user_input: str) -> str:
        sp("\n[重新规划] 根据您的反馈调整方案...")
        # 先读取当前方案摘要，拼到用户输入里，避免旧信息丢失
        old_plan = ""
        if self.session_id:
            try:
                s = await self.orchestrator.get_status(self.session_id)
                if s.nodes:
                    old_plan = "之前方案: " + "、".join(
                        f"{n.get('start_time','')} {n.get('name','')}" for n in s.nodes[:3]
                    )
            except Exception:
                pass
        enriched = f"{old_plan}。用户新要求: {user_input}" if old_plan else user_input
        return await self._do_plan(enriched)

    async def _do_execute(self) -> str:
        try:
            await self.orchestrator.confirm_itinerary(self.session_id)
        except Exception as e:
            return f"预约启动失败: {e}"
        sp("  预约进度：")
        for _ in range(20):
            await asyncio.sleep(1.5)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state in ("completed", "needs_replan"):
                break
        return "全部预约搞定！您按计划出发就行。"

    @staticmethod
    def _show_plan_str(nodes) -> str:
        if not nodes:
            return "暂无方案"
        return "\n".join(f"  {n.get('start_time','')} {n.get('name','')}" for n in nodes[:5])


# ── 主循环 ──────────────────────────────────────────────────

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
            sp(f"\n[Agent] {response}\n")
    except (KeyboardInterrupt, EOFError):
        sp("\n[Agent] 再见！")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
