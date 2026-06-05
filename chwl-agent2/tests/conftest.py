"""Pytest 共享 fixtures"""
import pytest

from core.tool_registry import ToolRegistry
from core.state_machine import (
    ItineraryState, NodeState,
    create_itinerary_fsm, create_node_fsm,
)
from mocks import MockBackend
from orchestrator.orchestrator import Orchestrator


@pytest.fixture
def mock_backend():
    return MockBackend()


@pytest.fixture
def tool_registry(mock_backend):
    registry = ToolRegistry()
    for name in ["location", "user_context", "weather", "activities_search",
                 "restaurants_search", "route_check", "candidates_score",
                 "itinerary_generate", "booking_execute", "booking_status",
                 "itinerary_replan"]:
        handler = getattr(mock_backend, f"handle_{name}")
        registry.register_mock(name, handler)
    return registry


@pytest.fixture
def orchestrator(tool_registry):
    return Orchestrator(tool_registry)


# ─── 状态机 fixture ──────────────────────────────────────────

@pytest.fixture
def itinerary_fsm():
    return create_itinerary_fsm()


@pytest.fixture
def node_fsm():
    return create_node_fsm()


# ─── 通用上下文 fixture ─────────────────────────────────────

@pytest.fixture
def sample_context():
    from core.models import ItineraryData, ItineraryNode
    from orchestrator.orchestrator import ExecutionContext
    ctx = ExecutionContext(session_id="test-session")
    ctx.itinerary = ItineraryData(
        itinerary_id="test-iti",
        session_id="test-session",
    )
    ctx.itinerary.nodes = [
        ItineraryNode(node_id="n1", poi_id="act_001", poi_name="活动1"),
        ItineraryNode(node_id="n2", poi_id="res_001", poi_name="餐厅1"),
    ]
    ctx.node_sms = {n.node_id: create_node_fsm() for n in ctx.itinerary.nodes}
    return ctx
