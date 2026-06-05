"""
CLI 交互式演示 — 在终端里跑完整流程

不需要前端，不需要队友 API。用内存 mock 展示全流程。

用法:
    python scripts/cli_demo.py                    # 家庭场景（内存 mock，默认）
    python scripts/cli_demo.py friends            # 朋友场景
    python scripts/cli_demo.py --teammate         # 使用队友 Mock API（需先启动 mock_api/app.py）
    python scripts/cli_demo.py --auto             # 自动演示模式

交互命令:
    status          - 查看当前状态
    confirm         - 确认行程，开始履约
    edit <id> <k=v> - 编辑节点（占位）
    sentiment <msg> - 上报情绪（如 "孩子累了"）
    cancel          - 取消行程
    q/quit          - 退出
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根目录

from core.tool_registry import ToolRegistry
from core.models import ItineraryModification, UserSentiment
from mocks import MockBackend
from mocks.env_simulator import EnvSimulator
from orchestrator.orchestrator import Orchestrator
from orchestrator.event_bus import EventBus

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


# ─── ANSI 颜色（兼容 Windows CMD） ───────────────────────────

import os as _os
_IS_WINDOWS = _os.name == "nt"

class C:
    GREEN = "" if _IS_WINDOWS else "\033[92m"
    YELLOW = "" if _IS_WINDOWS else "\033[93m"
    RED = "" if _IS_WINDOWS else "\033[91m"
    BLUE = "" if _IS_WINDOWS else "\033[94m"
    CYAN = "" if _IS_WINDOWS else "\033[96m"
    BOLD = "" if _IS_WINDOWS else "\033[1m"
    DIM = "" if _IS_WINDOWS else "\033[2m"
    RESET = "" if _IS_WINDOWS else "\033[0m"
    CLEAR = "" if _IS_WINDOWS else "\033[2J\033[H"

# Windows 下启用颜色支持
if _IS_WINDOWS:
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def banner():
    if C.CLEAR:
        print(f"{C.CLEAR}", end="")
    print(f"{C.BOLD}{C.CYAN}")
    print("=" * 52)
    print("  Meituan Agent - Activity Planning Demo")
    print("  Tool Orchestration Layer")
    print("=" * 52)
    print(f"{C.RESET}")
    print("  Say what you need, I'll handle the rest.")
    print()


def agent_say(text):
    """AI 管家说话"""
    print(f"\n{C.CYAN}{C.BOLD}[管家]{C.RESET} {text}")

def user_say(text):
    """用户说话"""
    print(f"\n{C.GREEN}{C.BOLD}[你]{C.RESET} {text}")

def print_plan(nodes, summary, total_min):
    """以卡片形式打印行程方案"""
    print(f"\n{'='*55}")
    print(f"  [下午行程方案]")
    print(f"{'='*55}")
    for i, node in enumerate(nodes, 1):
        cat_icon = {"main_activity": "[A]", "restaurant": "[R]", "optional_activity": "[W]"}
        icon = cat_icon.get(node.get("category", ""), "[*]")
        time_str = f"{node.get('scheduled_start', '')} - {node.get('scheduled_end', '')}"
        print(f"  {i}. {icon} {C.BOLD}{node.get('poi_name', '')}{C.RESET}")
        print(f"     Time: {time_str}  |  {node.get('duration_min', '')}min")
        tags = node.get("tags", [])
        if tags:
            print(f"     Tags: {' '.join(tags[:3])}")
        print()
    print(f"{'='*55}")
    print(f"  Total: {total_min} min")
    if summary:
        print(f"  Note: {summary}")
    print(f"{'='*55}")

def print_fulfillment_progress(name, status):
    """打印履约进度"""
    icons = {"processing": "[.]", "confirmed": "[OK]", "failed": "[X]", "queued": "[*]"}
    icon = icons.get(status, "[.]")
    print(f"  {icon} {name}: {status}")

def print_event(ctx, event):
    """打印事件到终端（对话流格式）"""
    etype = event["type"]
    data = event.get("data", {})
    msg = ""

    if etype == "plan_complete":
        itinerary = data.get("itinerary", {})
        nodes = itinerary.get("nodes", [])
        agent_say(f"方案已经安排好了，给您看看下午的安排：")
        print_plan(nodes, itinerary.get("summary", ""),
                   itinerary.get("total_duration_min", 0))
        print(f"\n  {C.DIM}您可以输入「确认」开始预约，或者告诉我哪里需要调整。{C.RESET}")

    elif etype == "status_update":
        msg = data.get("message", "").strip()
        if msg:
            print(f"  {C.BLUE}[.] {msg}{C.RESET}")

    elif etype == "execution_started":
        agent_say("好的，现在开始帮您预约和取号，请稍等：")

    elif etype == "booking_status_changed":
        print_fulfillment_progress(data.get("name", ""), data.get("status", ""))

    elif etype == "execution_complete":
        agent_say("全部预约成功！所有预订都已锁定！")

    elif etype == "execution_partial_failure":
        failed = data.get("failed_nodes", [])
        agent_say(f"有 {len(failed)} 个节点预约遇到问题，正在帮您找替代方案...")

    elif etype == "node_failed":
        print(f"  {C.RED}  {data.get('name', '')} 遇到问题"
              f" (L{data.get('fallback_level', '?')} 级处理){C.RESET}")

    elif etype == "node_replaced":
        new = data.get("new_node", {}).get("name", "")
        agent_say(f"已替换为「{new}」[OK]")

    elif etype == "replan_ready":
        agent_say("调整方案已准备就绪，您看是否需要切换？")

    elif etype == "replan_applied":
        agent_say("已按调整方案更新行程 [OK]")

    elif etype == "queue_too_long":
        name = data.get("name", "")
        queue = data.get("queue_time", 0)
        print(f"  {C.RED}[!] 警告：{name} 当前排队 {queue} 分钟，正在寻找替代{C.RESET}")

    elif etype == "plan_failed":
        agent_say(f"规划出现问题：{data.get('error', '')}，请重新描述需求 (sorry)")

    elif etype == "user_sentiment":
        agent_say("收到您的反馈，我来看看怎么调整方案。")

    elif etype == "tool_warning":
        tool = data.get("tool", "")
        err = data.get("error", {}).get("code", "unknown")
        print(f"  {C.YELLOW}[!] 数据源 {tool} 返回: {err}{C.RESET}")


def show_status(status):
    """打印状态快照"""
    print(f"\n{C.BOLD}📊 状态快照{C.RESET}")
    print(f"  ID: {status.session_id[:12]}...")
    print(f"  行程状态: {status.itinerary_state}")
    print(f"  模式: {status.mode.value}")
    print(f"  场景: {status.scene.value}")
    print(f"  进度: {status.progress_pct}%")
    if status.summary:
        print(f"  摘要: {status.summary}")
    if status.has_pending_confirmation:
        print(f"  {C.YELLOW}[!] 有待处理的确认请求: {status.pending_confirmation_type}{C.RESET}")
    if status.nodes:
        print(f"\n  {C.BOLD}节点列表:{C.RESET}")
        for n in status.nodes:
            state_icon = {"planned": "[.]", "pending": "[.]", "processing": "[*]",
                          "success": "[OK]", "completed_lock": "[L]", "failed": "[X]",
                          "replanned": "[*]"}
            icon = state_icon.get(n["state"], "[?]")
            print(f"    {icon} {n.get('name', '')} [{n['state']}]"
                  f" {n.get('start_time', '')}-{n.get('end_time', '')}")


def print_help():
    print(f"\n{C.BOLD}可用命令:{C.RESET}")
    print("  status       - 查看状态")
    print("  confirm      - 确认行程，开始履约")
    print("  edit <k=v>   - 模拟编辑（占位）")
    print("  sentiment <m> - 上报情绪，如: sentiment 孩子累了")
    print("  cancel       - 取消")
    print("  q/quit       - 退出")


def print_start_prompt():
    print(f"\n{C.GREEN}{C.BOLD}[*] 系统已就绪！{C.RESET}")
    print(f"  输入命令与系统交互，输入 help 查看可用命令")
    print(f"  {C.DIM}正在规划中，请稍候...{C.RESET}")


# ─── 主循环 ──────────────────────────────────────────────────

async def main():
    scene = "family"
    user_input = "今天下午想和老婆孩子出去玩几个小时，别太远，帮我安排一下"

    if len(sys.argv) > 1:
        if sys.argv[1] == "friends" or sys.argv[1] == "--friends":
            scene = "friends"
            user_input = "今天下午是空的，想和朋友出去玩几个小时，别离家太远，帮我安排一下"
        elif sys.argv[1] == "--auto":
            await run_auto_demo()
            return

    banner()
    print(f"  Input: {C.BOLD}{user_input}{C.RESET}")
    print(f"  Scene: {scene}")

    # ── 初始化 ──

    tools = ToolRegistry()
    use_teammate = "--teammate" in sys.argv

    if use_teammate:
        # ── 队友 Mock API 模式（需先启动 mock_api/app.py）──
        from mocks.teammate_adapter import TeammateAPIAdapter
        adapter = TeammateAPIAdapter("http://127.0.0.1:8000")

        # 队友提供的事实数据 API
        tools.register_mock("activities_search", adapter.activities_search)
        tools.register_mock("restaurants_search", adapter.restaurants_search)
        tools.register_mock("weather", adapter.weather)
        tools.register_mock("route_check", adapter.route_check)
        tools.register_mock("booking_status", adapter.booking_status)

        # Planner 层 API（队友没有）→ 内存 mock
        backend = MockBackend()
        if scene == "friends":
            backend.set_scene("friends")
        tools.register_mock("location", backend.handle_location)
        tools.register_mock("user_context", backend.handle_user_context)
        tools.register_mock("candidates_score", backend.handle_candidates_score)
        tools.register_mock("itinerary_generate", backend.handle_itinerary_generate)
        tools.register_mock("booking_execute", backend.handle_booking_execute)
        tools.register_mock("itinerary_replan", backend.handle_itinerary_replan)

        print(f"  {C.CYAN}🔗 队友 Mock API (http://127.0.0.1:8000){C.RESET}")
    else:
        # ── 纯内存 mock 模式（默认，不需要启动外部服务）──
        backend = MockBackend()
        if scene == "friends":
            backend.set_scene("friends")
        for name in ["location", "user_context", "weather", "activities_search",
                     "restaurants_search", "route_check", "candidates_score",
                     "itinerary_generate", "booking_execute", "booking_status",
                     "itinerary_replan"]:
            handler = getattr(backend, f"handle_{name}")
            tools.register_mock(name, handler)

    event_bus = EventBus()
    orchestrator = Orchestrator(tools, event_bus)

    # 订阅事件
    event_bus.subscribe("*", print_event)

    # ── 初始化环境模拟器（注入动态事件）──
    simulator = EnvSimulator(backend, event_bus)
    if scene == "family":
        simulator.schedule_preset_family_scenario()
    else:
        simulator.schedule_preset_friends_scenario()
    asyncio.create_task(simulator.run_timeline("demo"))

    # ── 启动会话 ──

    sid = await orchestrator.start_session(user_input)
    agent_say("正在为您规划下午的方案，请稍等...")

    # 等待 Phase 1 规划完成
    for _ in range(30):  # 最多等 30 秒
        await asyncio.sleep(1)
        status = await orchestrator.get_status(sid)
        if status.itinerary_state in ("pending_confirm", "executing", "completed"):
            break

    if status.itinerary_state != "pending_confirm":
        agent_say("规划未完成，请稍后输入「看看方案」查看进度")
    # ── 对话交互循环 ──

    def match_intent(text: str):
        """自然语言意图匹配"""
        t = text.lower().strip()
        if any(k in t for k in ("退出", " quit", " exit", "再见", "拜拜")):
            return "quit"
        if any(k in t for k in ("确认", "安排", "开始", "一键", "下单", "预约", "好的")):
            return "confirm"
        if any(k in t for k in ("状态", "查看", "什么方案", "方案是什么", "看看")):
            return "status"
        if any(k in t for k in ("累", "困", "不想", "取消活动", "删掉", "太远", "太长")):
            return "sentiment"
        if any(k in t for k in ("取消行程", "不去了", "全取消", "取消所有")):
            return "cancel"
        return "unknown"

    user_say("帮我安排一下下午出去玩")

    try:
        while True:
            cmd = input(f"\n  {C.CYAN}输入消息{C.RESET} > ").strip()
            if not cmd:
                continue

            intent = match_intent(cmd)

            if intent == "quit":
                user_say("再见！")
                break

            elif intent == "confirm":
                user_say(cmd)
                try:
                    result = await orchestrator.confirm_itinerary(sid)
                    # 履约信息会通过 event_bus 异步打印
                except Exception as e:
                    agent_say(f"确认失败：{e}，请重试")

            elif intent == "status":
                user_say(cmd)
                try:
                    status = await orchestrator.get_status(sid)
                    if status.nodes:
                        print_plan(
                            status.nodes,
                            status.summary,
                            sum(n.get("duration_min", 0) for n in status.nodes),
                        )
                    agent_say(f"当前状态：{status.itinerary_state}")
                except Exception as e:
                    agent_say(f"查询失败：{e}")

            elif intent == "sentiment":
                user_say(cmd)
                # 自动判断类型
                sent_type = "tired" if any(k in cmd for k in ("累", "困")) else "other"
                sentiment = UserSentiment(type=sent_type, description=cmd)
                try:
                    await orchestrator.handle_user_sentiment(sid, sentiment)
                except Exception as e:
                    agent_say(f"处理失败：{e}")

            elif intent == "cancel":
                user_say(cmd)
                try:
                    await orchestrator.cancel_session(sid)
                    agent_say("已取消行程，所有预约已释放。需要重新安排随时找我。")
                except Exception as e:
                    agent_say(f"取消失败：{e}")

            else:
                user_say(cmd)
                agent_say("我没完全理解您的意思，您可以试试：\n"
                          "  - 说「确认」来一键安排\n"
                          "  - 说「看看方案」查看当前行程\n"
                          "  - 说「孩子累了」来调整行程\n"
                          "  - 说「取消行程」取消所有安排")

    except KeyboardInterrupt:
        agent_say("好的，下次需要随时找我。再见！")
    except Exception as e:
        agent_say(f"出了点问题：{e}，请重试")


# ─── 自动演示模式 ────────────────────────────────────────────

async def run_auto_demo():
    """全自动演示 — 不需要用户输入"""
    banner()
    print(f"{C.BOLD}{C.CYAN}🎬 自动演示模式{C.RESET}\n")

    # 家庭场景
    print(f"{C.BOLD}[场景一] 家庭带娃（排队异常 + 孩子累了）{C.RESET}\n")
    await run_single_demo("family", [
        ("plan", None),
        ("wait", 3),
        ("confirm", None),
        ("wait", 18),
        ("sentiment", UserSentiment(type="tired", description="孩子有点累了")),
        ("wait", 3),
    ])

    print(f"\n{C.DIM}{'='*50}{C.RESET}\n")

    # 朋友场景
    print(f"{C.BOLD}[场景二] 朋友聚会（天气异常）{C.RESET}\n")
    await run_single_demo("friends", [
        ("plan", None),
        ("wait", 3),
        ("confirm", None),
        ("wait", 18),
    ])

    print(f"\n{C.GREEN}{C.BOLD}🎉 自动演示完成！{C.RESET}")


async def run_single_demo(scene: str, steps: list):
    backend = MockBackend()
    if scene == "friends":
        backend.set_scene("friends")

    tools = ToolRegistry()
    for name in ["location", "user_context", "weather", "activities_search",
                 "restaurants_search", "route_check", "candidates_score",
                 "itinerary_generate", "booking_execute", "booking_status",
                 "itinerary_replan"]:
        handler = getattr(backend, f"handle_{name}")
        tools.register_mock(name, handler)

    event_bus = EventBus()
    orchestrator = Orchestrator(tools, event_bus)

    # 环境模拟器（自动演示中注入动态事件）
    simulator = EnvSimulator(backend, event_bus)
    if scene == "family":
        simulator.schedule_preset_family_scenario()
    else:
        simulator.schedule_preset_friends_scenario()
    asyncio.create_task(simulator.run_timeline("demo"))

    captured = []
    def collector(ctx, event):
        captured.append(event)
    event_bus.subscribe("*", collector)

    user_input = ("今天下午想和老婆孩子出去玩几个小时，别太远，帮我安排一下"
                  if scene == "family" else
                  "今天下午是空的，想和朋友出去玩几个小时，别离家太远，帮我安排一下")

    sid = await orchestrator.start_session(user_input)

    for step_type, step_data in steps:
        if step_type == "wait":
            await asyncio.sleep(step_data)
            # 打印收集到的事件
            while captured:
                ev = captured.pop(0)
                print_event(None, ev)
                await asyncio.sleep(0.3)

        elif step_type == "plan":
            await asyncio.sleep(2)
            while captured:
                ev = captured.pop(0)
                print_event(None, ev)
                await asyncio.sleep(0.3)

        elif step_type == "confirm":
            await orchestrator.confirm_itinerary(sid)
            print(f"\n{C.GREEN}[OK] 用户点击「一键安排」{C.RESET}")

        elif step_type == "sentiment":
            await orchestrator.handle_user_sentiment(sid, step_data)
            print(f"\n{C.YELLOW}📝 用户上报: {step_data.description}{C.RESET}")

    # 等待剩余事件
    await asyncio.sleep(5)
    while captured:
        ev = captured.pop(0)
        print_event(None, ev)
        await asyncio.sleep(0.3)

    status = await orchestrator.get_status(sid)
    show_status(status)


if __name__ == "__main__":
    asyncio.run(main())
