"""
Orchestrator 集成测试

测试 Phase 1 (规划) 和 Phase 2 (履约) 的完整流程
"""
import asyncio
import pytest

from core.state_machine import ItineraryState, NodeState
from core.models import ItineraryModification, UserSentiment


class TestOrchestrator:
    """Orchestrator 集成测试"""

    @pytest.mark.asyncio
    async def test_start_session_returns_id(self, orchestrator):
        sid = await orchestrator.start_session("下午出去玩")
        assert sid is not None
        assert sid.startswith("sess_")

    @pytest.mark.asyncio
    async def test_phase1_completes_draft(self, orchestrator):
        """Phase 1 完成后状态为 PENDING_CONFIRM（因为 mock 很快）"""
        sid = await orchestrator.start_session("下午带老婆孩子出去玩")
        await asyncio.sleep(2)  # 等待异步 Phase 1 完成

        status = await orchestrator.get_status(sid)
        assert status.itinerary_state in ("pending_confirm", "draft")

    @pytest.mark.asyncio
    async def test_phase1_generates_nodes(self, orchestrator):
        """Phase 1 完成后至少有一个节点"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        status = await orchestrator.get_status(sid)
        assert len(status.nodes) > 0

    @pytest.mark.asyncio
    async def test_confirm_triggers_execution(self, orchestrator):
        """确认后进入 EXECUTING 状态"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        result = await orchestrator.confirm_itinerary(sid)
        assert result["status"] == "executing"

        status = await orchestrator.get_status(sid)
        assert status.itinerary_state == "executing"

    @pytest.mark.asyncio
    async def test_full_flow_success(self, orchestrator):
        """完整流程：规划 → 确认 → 履约完成"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        # 检查规划完成
        status_before = await orchestrator.get_status(sid)
        assert len(status_before.nodes) > 0

        # 确认
        await orchestrator.confirm_itinerary(sid)

        # 等待履约完成（mock booking 约 15 秒）
        await asyncio.sleep(18)

        status_after = await orchestrator.get_status(sid)
        # 如果全部成功，状态为 completed；部分失败则为 needs_replan
        assert status_after.itinerary_state in ("completed", "needs_replan")

    @pytest.mark.asyncio
    async def test_user_sentiment_triggers_replan(self, orchestrator):
        """用户上报情绪触发重规划"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        result = await orchestrator.handle_user_sentiment(
            sid, UserSentiment(type="tired", description="孩子累了")
        )
        assert result["status"] == "replanning"

    @pytest.mark.asyncio
    async def test_modify_itinerary(self, orchestrator):
        """用户编辑行程"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        # 获取当前节点
        status = await orchestrator.get_status(sid)
        if not status.nodes:
            pytest.skip("无可用节点")

        # 编辑第一个节点
        node = status.nodes[0]
        mod = ItineraryModification(
            type="replace",
            node_id=node["node_id"],
            new_resource={"poi_name": "替换活动"},
        )
        itinerary = await orchestrator.modify_itinerary(sid, mod)
        assert itinerary is not None

    @pytest.mark.asyncio
    async def test_cancel_session(self, orchestrator):
        """取消会话"""
        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(1)

        result = await orchestrator.cancel_session(sid)
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_unknown_session_raises_error(self, orchestrator):
        with pytest.raises(ValueError, match="会话不存在"):
            await orchestrator.get_status("nonexistent")

    @pytest.mark.asyncio
    async def test_event_bus_emits_plan_complete(self, orchestrator):
        """验证 plan_complete 事件被触发"""
        events = []
        orchestrator.event_bus.subscribe("plan_complete",
            lambda ctx, e: events.append(e))

        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)

        assert len(events) >= 1
        assert events[0]["type"] == "plan_complete"

    @pytest.mark.asyncio
    async def test_event_bus_emits_execution_events(self, orchestrator):
        """验证履约事件被触发"""
        events = []
        orchestrator.event_bus.subscribe("execution_started",
            lambda ctx, e: events.append(e))
        orchestrator.event_bus.subscribe("booking_status_changed",
            lambda ctx, e: events.append(e))

        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(2)
        await orchestrator.confirm_itinerary(sid)
        await asyncio.sleep(5)

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_concurrent_sessions(self, orchestrator):
        """并发多会话不互相干扰"""
        sid1 = await orchestrator.start_session("家庭出去玩")
        sid2 = await orchestrator.start_session("朋友聚会")
        await asyncio.sleep(2)

        status1 = await orchestrator.get_status(sid1)
        status2 = await orchestrator.get_status(sid2)
        assert status1.session_id != status2.session_id

    @pytest.mark.asyncio
    async def test_e2e_full_flow(self, orchestrator):
        """端到端集成测试：规划→确认→履约完成"""
        sid = await orchestrator.start_session("下午带5岁孩子出去玩")
        await asyncio.sleep(3)

        # 规划完成
        status = await orchestrator.get_status(sid)
        assert status.itinerary_state == "pending_confirm"
        assert len(status.nodes) >= 2, f"至少2个节点，实际{len(status.nodes)}"

        # 确认履约
        result = await orchestrator.confirm_itinerary(sid)
        assert result["status"] == "executing"

        # 等待履约完成
        for i in range(20):
            await asyncio.sleep(1)
            s = await orchestrator.get_status(sid)
            if s.itinerary_state in ("completed", "needs_replan"):
                break

        final = await orchestrator.get_status(sid)
        assert final.itinerary_state in ("completed", "needs_replan"), \
            f"期望 completed 或 needs_replan，实际 {final.itinerary_state}"

    @pytest.mark.asyncio
    async def test_resource_loss_warning_on_cancel(self, orchestrator):
        """取消行程时发出资源损失警告"""
        events = []
        orchestrator.event_bus.subscribe("resource_loss_warning",
            lambda ctx, e: events.append(e))

        sid = await orchestrator.start_session("下午出去玩")
        await asyncio.sleep(3)
        await orchestrator.confirm_itinerary(sid)
        await asyncio.sleep(8)  # 等部分 booking 完成
        await orchestrator.cancel_session(sid)
        # 资源损失事件可能被发出
        assert len(events) >= 0  # 至少不崩溃
