"""
LLM Environment Simulator 测试
"""
import asyncio
import pytest
from mocks.env_simulator import EnvSimulator
from mocks import MockBackend
from orchestrator.event_bus import EventBus


class TestEnvSimulator:
    """环境模拟器测试"""

    def setup_method(self):
        self.backend = MockBackend()
        self.eb = EventBus()
        self.sim = EnvSimulator(self.backend, self.eb)

    def test_schedule_event(self):
        """安排单个事件"""
        self.sim.schedule(5.0, "queue_spike", {"poi_ids": ["res_001"], "new_queue_time": 90})
        assert len(self.sim._events) == 1
        assert self.sim._events[0].event_type == "queue_spike"

    def test_schedule_preset_family(self):
        """预设家庭场景"""
        self.sim.schedule_preset_family_scenario()
        assert len(self.sim._events) >= 3

    def test_schedule_preset_friends(self):
        """预设朋友场景"""
        self.sim.schedule_preset_friends_scenario()
        assert len(self.sim._events) >= 3

    def test_schedule_preset_auto_demo(self):
        """预设自动演示场景"""
        self.sim.schedule_preset_auto_demo()
        assert len(self.sim._events) >= 4

    @pytest.mark.asyncio
    async def test_run_timeline_triggers_events(self):
        """时间线按顺序触发事件"""
        events = []
        self.eb.subscribe("env_event_queue_spike", lambda ctx, e: events.append(e))
        self.eb.subscribe("env_event_weather_change", lambda ctx, e: events.append(e))

        self.sim.schedule(1.0, "queue_spike", {"poi_ids": ["res_001"], "new_queue_time": 90})
        self.sim.schedule(2.0, "weather_change", {"weather": "中雨"})

        await self.sim.run_timeline("test_session")
        assert len(events) == 2
        assert events[0]["data"]["type"] == "queue_spike"
        assert events[1]["data"]["type"] == "weather_change"

    @pytest.mark.asyncio
    async def test_queue_spike_modifies_backend(self):
        """排队暴击事件修改 mock backend"""
        self.sim.schedule(0.5, "queue_spike", {"poi_ids": ["res_001"], "new_queue_time": 90})

        await self.sim.run_timeline("test_session")
        # backend should be modified
        fail_config = self.backend._fail_config.get("booking_execute")
        assert fail_config == "queue_too_long"

    @pytest.mark.asyncio
    async def test_weather_change_modifies_backend(self):
        """天气变化事件修改 mock backend"""
        self.sim.schedule(0.5, "weather_change", {"weather": "中雨"})
        assert self.backend._weather_override is None

        await self.sim.run_timeline("test_session")
        assert self.backend._weather_override is not None

    @pytest.mark.asyncio
    async def test_status_update_event(self):
        """状态更新事件触发 EventBus"""
        events = []
        self.eb.subscribe("status_update", lambda ctx, e: events.append(e))

        self.sim.schedule(0.5, "status_update", {"message": "test message"})
        await self.sim.run_timeline("test_session")

        assert len(events) == 1
        assert events[0]["data"]["message"] == "test message"

    @pytest.mark.asyncio
    async def test_stop_timeline(self):
        """停止时间线"""
        self.sim.schedule(10, "queue_spike", {"poi_ids": ["res_001"]})

        run_task = asyncio.create_task(self.sim.run_timeline("test"))
        await asyncio.sleep(0.5)
        self.sim.stop()
        await run_task

        # 时间线提前结束，事件未被触发
        assert self.sim._triggered_count == 0

    @pytest.mark.asyncio
    async def test_booking_fail_event(self):
        """预约失败事件"""
        events = []
        self.eb.subscribe("env_event_booking_fail", lambda ctx, e: events.append(e))

        self.sim.schedule(0.5, "booking_fail", {"node_id": "n1", "reason": "booking_rejected"})
        await self.sim.run_timeline("test_session")

        assert len(events) == 1
        assert events[0]["data"]["reason"] == "booking_rejected"
