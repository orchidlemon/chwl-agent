"""
Replanning 增强测试 — 多候选替换 + 用户选择
"""
import asyncio
import pytest
import pytest_asyncio
from core.state_machine import ItineraryState, NodeState, create_node_fsm
from core.models import NodeCategory
from core.tool_registry import ToolRegistry
from mocks import MockBackend
from orchestrator.orchestrator import Orchestrator


async def make_failed_node(ctx, node_id, poi_id, poi_name, category=NodeCategory.RESTAURANT):
    """创建节点并通过状态机切换到 FAILED 状态"""
    from core.models import ItineraryNode, ItineraryData

    node = ItineraryNode(
        node_id=node_id, poi_id=poi_id, poi_name=poi_name,
        category=category, needs_booking=True, status="failed",
    )
    if not ctx.itinerary:
        ctx.itinerary = ItineraryData(itinerary_id="test_iti", session_id=ctx.session_id)
    ctx.itinerary.nodes.append(node)
    sm = create_node_fsm()
    ctx.node_sms[node.node_id] = sm
    # 走合法路径: planned -> pending -> processing -> failed
    await sm.transition_to(NodeState.PENDING, ctx)
    await sm.transition_to(NodeState.PROCESSING, ctx)
    await sm.transition_to(NodeState.FAILED, ctx)
    node.status = "failed"
    return node


class TestReplanEnhanced:
    """Replanning 增强测试"""

    @pytest_asyncio.fixture
    async def setup(self):
        backend = MockBackend()
        tools = ToolRegistry()
        for name in ["location", "user_context", "weather", "activities_search",
                     "restaurants_search", "route_check", "candidates_score",
                     "itinerary_generate", "booking_execute", "booking_status",
                     "itinerary_replan"]:
            handler = getattr(backend, f"handle_{name}")
            tools.register_mock(name, handler)

        orch = Orchestrator(tools)
        sid = await orch.start_session("下午带老婆孩子出去玩")
        await asyncio.sleep(2)
        return orch, sid, backend

    @pytest.mark.asyncio
    async def test_find_alternatives_returns_multiple(self, setup):
        """_find_alternatives 返回多个候选"""
        orch, sid, _ = setup
        ctx = orch._get_ctx(sid)
        ctx.raw_candidates = [
            {"poi_id": "r1", "name": "餐厅A", "category": "restaurant",
             "rating": 4.5, "queue_time_min": 10, "distance_km": 1.0},
            {"poi_id": "r2", "name": "餐厅B", "category": "restaurant",
             "rating": 4.0, "queue_time_min": 20, "distance_km": 2.0},
            {"poi_id": "r3", "name": "餐厅C", "category": "restaurant",
             "rating": 4.8, "queue_time_min": 45, "distance_km": 0.5},
        ]

        from core.models import ItineraryNode
        node = ItineraryNode(node_id="n1", poi_id="r_old", poi_name="旧餐厅",
                             category=NodeCategory.RESTAURANT)

        alternatives = await orch._find_alternatives(ctx, node)
        assert len(alternatives) >= 2

    @pytest.mark.asyncio
    async def test_alternatives_have_reasons(self, setup):
        """每个替代候选包含推荐理由"""
        orch, sid, _ = setup
        ctx = orch._get_ctx(sid)
        from core.models import ItineraryNode

        ctx.raw_candidates = [
            {"poi_id": "r1", "name": "轻食餐厅", "category": "restaurant",
             "rating": 4.5, "queue_time_min": 5, "distance_km": 0.8,
             "tags": ["亲子", "轻食"]},
        ]
        node = ItineraryNode(node_id="n1", poi_id="r_old", poi_name="旧餐厅",
                             category=NodeCategory.RESTAURANT)

        alternatives = await orch._find_alternatives(ctx, node)
        assert len(alternatives) > 0
        assert alternatives[0].get("planner_reason", "") != ""

    @pytest.mark.asyncio
    async def test_on_alternative_response_applies_replacement(self, setup):
        """用户选择替代后应用替换"""
        orch, sid, _ = setup
        ctx = orch._get_ctx(sid)

        node = await make_failed_node(ctx, "n_test", "r_old", "旧餐厅")

        alternatives = [
            {"poi_id": "r_new", "name": "新餐厅", "category": "restaurant",
             "planner_reason": "排队短，距离近", "queue_time_min": 10},
        ]

        await orch._on_alternative_response(ctx, node, True,
                                            {"value": "r_new"}, alternatives)

        assert node.poi_id == "r_new"
        assert node.poi_name == "新餐厅"
        assert node.status == "planned"

    @pytest.mark.asyncio
    async def test_on_alternative_response_reject(self, setup):
        """用户拒绝后标记 fallback_failed"""
        orch, sid, _ = setup
        ctx = orch._get_ctx(sid)

        node = await make_failed_node(ctx, "n_test", "r_old", "旧餐厅")

        # 用户拒绝
        await orch._on_alternative_response(ctx, node, False, {}, [])

        assert node.fallback_failed is True

    @pytest.mark.asyncio
    async def test_level2_fallback_shows_alternatives_to_user(self, setup):
        """L2 fallback 显示替代给用户选择"""
        orch, sid, _ = setup
        ctx = orch._get_ctx(sid)

        node = await make_failed_node(ctx, "n_l2", "res_fam_001", "轻食研究所")
        ctx.raw_candidates = [
            {"poi_id": "r1", "name": "替代餐厅A", "category": "restaurant",
             "rating": 4.5, "queue_time_min": 10},
            {"poi_id": "r2", "name": "替代餐厅B", "category": "restaurant",
             "rating": 4.2, "queue_time_min": 5},
        ]

        events = []
        ctx.event_bus.subscribe("alternatives_ready", lambda ctx, e: events.append(e))

        await orch._handle_node_failure(ctx, node, {"code": "queue_too_long"})
        await asyncio.sleep(0.5)

        pending = orch.confirmation.list_pending()
        assert len(pending) > 0
        assert pending[0].type == "alternative_choice"
