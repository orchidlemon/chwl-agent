"""
状态机单元测试 — 验证 ItineraryFSM 和 NodeFSM 的所有合法/非法转换
"""
import pytest
from core.state_machine import (
    ItineraryState, NodeState,
    create_itinerary_fsm, create_node_fsm,
    InvalidTransitionError,
)


class TestItineraryFSM:
    """行程级状态机测试"""

    def test_initial_state(self, itinerary_fsm):
        assert itinerary_fsm.state == ItineraryState.INIT

    def test_init_to_draft(self, itinerary_fsm, sample_context):
        """用户输入 → DRAFT"""
        from core.state_machine import StateChangeEvent
        events = []
        itinerary_fsm.on_change(lambda e: events.append(e))

        import asyncio
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        assert itinerary_fsm.state == ItineraryState.DRAFT
        assert len(events) == 1
        assert events[0].from_state == ItineraryState.INIT
        assert events[0].to_state == ItineraryState.DRAFT

    def test_draft_to_pending_confirm(self, itinerary_fsm, sample_context):
        """方案就绪 → PENDING_CONFIRM"""
        import asyncio
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.PENDING_CONFIRM, sample_context))
        assert itinerary_fsm.state == ItineraryState.PENDING_CONFIRM

    def test_pending_confirm_to_executing(self, itinerary_fsm, sample_context):
        """用户确认 → EXECUTING"""
        import asyncio
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.PENDING_CONFIRM, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.EXECUTING, sample_context))
        assert itinerary_fsm.state == ItineraryState.EXECUTING

    def test_pending_confirm_back_to_draft_with_edits(self, itinerary_fsm, sample_context):
        """用户编辑 → DRAFT（需要 has_user_edits=True）"""
        import asyncio
        sample_context.has_user_edits = True
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.PENDING_CONFIRM, sample_context))

        # 有编辑 → 可以回退
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        assert itinerary_fsm.state == ItineraryState.DRAFT

    def test_pending_confirm_no_edit_cannot_go_back(self, itinerary_fsm, sample_context):
        """没有编辑 → 不能回退到 DRAFT"""
        import asyncio
        sample_context.has_user_edits = False
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.PENDING_CONFIRM, sample_context))

        with pytest.raises(InvalidTransitionError):
            asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))

    def test_invalid_transition_raises(self, itinerary_fsm, sample_context):
        """非法转换抛出异常"""
        import asyncio
        # INIT → EXECUTING 是非法的
        with pytest.raises(InvalidTransitionError):
            asyncio.run(itinerary_fsm.transition_to(ItineraryState.EXECUTING, sample_context))

    def test_executing_to_completed(self, itinerary_fsm, sample_context):
        """全部完成 → COMPLETED"""
        import asyncio
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.DRAFT, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.PENDING_CONFIRM, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.EXECUTING, sample_context))
        asyncio.run(itinerary_fsm.transition_to(ItineraryState.COMPLETED, sample_context))
        assert itinerary_fsm.state == ItineraryState.COMPLETED
        assert itinerary_fsm.is_terminal()


class TestNodeFSM:
    """节点级状态机测试"""

    def test_initial_state(self, node_fsm):
        assert node_fsm.state == NodeState.PLANNED

    def test_planned_to_pending_to_processing(self, node_fsm):
        """正常履约流程"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.PENDING, None))
        assert node_fsm.state == NodeState.PENDING
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        assert node_fsm.state == NodeState.PROCESSING

    def test_processing_to_success_to_locked(self, node_fsm):
        """成功 → 锁定"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.PENDING, None))
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        asyncio.run(node_fsm.transition_to(NodeState.SUCCESS, None))
        assert node_fsm.state == NodeState.SUCCESS
        asyncio.run(node_fsm.transition_to(NodeState.COMPLETED_LOCK, None))
        assert node_fsm.state == NodeState.COMPLETED_LOCK
        assert node_fsm.is_terminal()

    def test_processing_to_failed_to_replanned(self, node_fsm):
        """失败 → 已替换"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.PENDING, None))
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        asyncio.run(node_fsm.transition_to(NodeState.FAILED, None))
        assert node_fsm.state == NodeState.FAILED
        asyncio.run(node_fsm.transition_to(NodeState.REPLANNED, None))
        assert node_fsm.state == NodeState.REPLANNED

    def test_user_pin_unpin(self, node_fsm):
        """用户固定/取消固定"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.USER_PINNED, None))
        assert node_fsm.state == NodeState.USER_PINNED
        asyncio.run(node_fsm.transition_to(NodeState.PLANNED, None))
        assert node_fsm.state == NodeState.PLANNED

    def test_soft_lock_lifecycle(self, node_fsm):
        """软锁 → 完成/失败"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.PENDING, None))
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        asyncio.run(node_fsm.transition_to(NodeState.SOFT_LOCK, None))
        assert node_fsm.state == NodeState.SOFT_LOCK
        asyncio.run(node_fsm.transition_to(NodeState.COMPLETED_LOCK, None))
        assert node_fsm.state == NodeState.COMPLETED_LOCK

    def test_user_confirmation_lifecycle(self, node_fsm):
        """需确认 → 继续/失败"""
        import asyncio
        asyncio.run(node_fsm.transition_to(NodeState.PENDING, None))
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        asyncio.run(node_fsm.transition_to(NodeState.AWAITING_CONFIRMATION, None))
        assert node_fsm.state == NodeState.AWAITING_CONFIRMATION

        # 用户确认继续
        asyncio.run(node_fsm.transition_to(NodeState.PROCESSING, None))
        assert node_fsm.state == NodeState.PROCESSING

    def test_invalid_transition(self, node_fsm):
        """非法转换"""
        import asyncio
        # PLANNED → COMPLETED_LOCK 是非法的
        with pytest.raises(InvalidTransitionError):
            asyncio.run(node_fsm.transition_to(NodeState.COMPLETED_LOCK, None))

    def test_possible_transitions(self, node_fsm):
        """查询可用转换"""
        trans = node_fsm.possible_transitions()
        assert len(trans) > 0
        assert any(t.to_state == NodeState.PENDING for t in trans)
        assert any(t.to_state == NodeState.USER_PINNED for t in trans)
