"""LLM 全链路测试"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ["PYTHONIOENCODING"] = "utf-8"

from core.llm_client import LLMClient
from core.llm_planner import LLMPlanner
from core.tool_registry import ToolRegistry
from mocks import MockBackend
from orchestrator.orchestrator import Orchestrator

async def test():
    llm = LLMClient(api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-67937130fddf4be086e73e7b2f6d293c"))
    planner = LLMPlanner(llm)

    # 测试 1: 意图理解
    print("=" * 50)
    print("Test 1: LLM Intent Understanding")
    result = await planner.handle_user_context({
        "input": "下午带5岁孩子和老婆出去玩，老婆在减肥，别太远"
    })
    data = result.get("data", {})
    print(f"  scene={data.get('scene')}, mode={data.get('mode')}")
    print(f"  special={data.get('special_requirements')}")
    assert data.get("scene") == "family", f"Expected family, got {data.get('scene')}"
    print("  PASS")

    # 测试 2: LLM 驱动全流程
    print("\n" + "=" * 50)
    print("Test 2: Full LLM Planning Flow")
    tools = ToolRegistry()
    tools.register_mock("user_context", planner.handle_user_context)
    tools.register_mock("candidates_score", planner.handle_candidates_score)
    tools.register_mock("itinerary_generate", planner.handle_itinerary_generate)
    tools.register_mock("itinerary_replan", planner.handle_itinerary_replan)

    mem = MockBackend()
    for name in ["location", "weather", "activities_search",
                 "restaurants_search", "route_check",
                 "booking_execute", "booking_status"]:
        tools.register_mock(name, getattr(mem, f"handle_{name}"))

    orch = Orchestrator(tools)
    sid = await orch.start_session("下午带老婆孩子出去玩")
    await asyncio.sleep(20)

    status = await orch.get_status(sid)
    print(f"  State: {status.itinerary_state}")
    print(f"  Nodes: {len(status.nodes)}")
    for n in status.nodes:
        print(f"    {n['name']} | {n['start_time']}-{n['end_time']} | [{n['state']}]")

    assert status.itinerary_state == "pending_confirm", f"Expected pending_confirm, got {status.itinerary_state}"
    assert len(status.nodes) >= 2, f"Expected >=2 nodes, got {len(status.nodes)}"
    print("  PASS")

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)

asyncio.run(test())
