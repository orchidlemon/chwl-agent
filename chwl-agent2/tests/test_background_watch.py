"""
Background Watch 测试 — 7x24 后台监控引擎
"""
import asyncio
import pytest
from orchestrator.background_watch import (
    BackgroundWatch, WatchConfig, WatchType, WatchAlert,
)
from orchestrator.event_bus import EventBus
from core.tool_registry import ToolRegistry


@pytest.fixture
def watch_engine():
    return BackgroundWatch()


class TestBackgroundWatch:
    """后台监控引擎测试"""

    @pytest.mark.asyncio
    async def test_add_watch(self, watch_engine):
        """添加监控配置"""
        config = WatchConfig(type=WatchType.QUEUE, poi_id="res_001", node_id="n2",
                             threshold={"estimated_wait_min_gt": 35})
        watch_engine.add_watch("s1", config)
        assert config.watch_id.startswith("w_")

    @pytest.mark.asyncio
    async def test_add_watch_group(self, watch_engine):
        """批量添加"""
        configs = [
            WatchConfig(type=WatchType.QUEUE, poi_id="r1", node_id="n1"),
            WatchConfig(type=WatchType.WEATHER, location="北京"),
        ]
        watch_engine.add_watch_group("s1", configs)
        assert watch_engine.get_active_count("s1") == 0  # 还没启动
        assert len(watch_engine._configs["s1"]) == 2

    @pytest.mark.asyncio
    async def test_start_and_stop_all(self, watch_engine):
        """启动和停止所有监控"""
        configs = [
            WatchConfig(type=WatchType.QUEUE, poi_id="r1", node_id="n1",
                        poll_interval_s=60, max_polls=1),
        ]
        await watch_engine.start_watch_group("s1", configs)
        assert watch_engine.get_active_count("s1") == 1

        await watch_engine.stop_all("s1")
        assert watch_engine.get_active_count("s1") == 0

    @pytest.mark.asyncio
    async def test_stop_watch(self, watch_engine):
        """停止单个监控"""
        config = WatchConfig(type=WatchType.QUEUE, poi_id="r1", node_id="n1",
                            poll_interval_s=60)
        watch_engine.add_watch("s1", config)
        await watch_engine.start_all("s1")
        assert watch_engine.is_running("s1", config.watch_id)

        await watch_engine.stop_watch("s1", config.watch_id)
        assert not watch_engine.is_running("s1", config.watch_id)

    @pytest.mark.asyncio
    async def test_queue_watch_triggers_alert(self):
        """排队监控 — 超阈值触发的告警"""
        # 模拟 tools: 排队超阈值
        tools = ToolRegistry()
        tools.register_mock("booking_status", lambda p: {
            "success": True,
            "data": {"estimated_wait_minutes": 50},
        })
        eb = EventBus()
        engine = BackgroundWatch(eb, tools)

        alerts = []
        eb.subscribe("watch_alert", lambda ctx, e: alerts.append(e))

        config = WatchConfig(
            type=WatchType.QUEUE, poi_id="res_001", node_id="n2",
            threshold={"estimated_wait_min_gt": 30},
            poll_interval_s=1,
        )
        await engine.start_watch_group("s1", [config])
        await asyncio.sleep(2.5)

        await engine.stop_all("s1")
        assert len(alerts) > 0
        alert = alerts[0]["data"]["alert"]
        assert alert["type"] == "queue"
        assert alert["severity"] == "warning"
        assert alert["payload"]["watch_id"] == config.watch_id

    @pytest.mark.asyncio
    async def test_weather_watch_triggers_alert(self):
        """天气监控 — 检测到暴雨触发告警"""
        tools = ToolRegistry()
        tools.register_mock("weather", lambda p: {
            "success": True,
            "data": {"weather": "暴雨", "temperature": 22},
        })
        eb = EventBus()
        engine = BackgroundWatch(eb, tools)

        alerts = []
        eb.subscribe("watch_alert", lambda ctx, e: alerts.append(e))

        config = WatchConfig(
            type=WatchType.WEATHER, location="北京",
            threshold={"rain_level_in": ["暴雨"]},
            poll_interval_s=1,
        )
        await engine.start_watch_group("s1", [config])
        await asyncio.sleep(2.5)

        await engine.stop_all("s1")
        assert len(alerts) > 0
        assert alerts[0]["data"]["alert"]["type"] == "weather"

    @pytest.mark.asyncio
    async def test_booking_watch_detects_failure(self):
        """预约监控 — 检测到失败"""
        tools = ToolRegistry()
        tools.register_mock("booking_status", lambda p: {
            "success": True,
            "data": {"status": "failed", "error": "余票不足"},
        })
        eb = EventBus()
        engine = BackgroundWatch(eb, tools)

        alerts = []
        eb.subscribe("watch_alert", lambda ctx, e: alerts.append(e))

        config = WatchConfig(
            type=WatchType.BOOKING, poi_id="BK-001", node_id="n1",
            poll_interval_s=1,
        )
        await engine.start_watch_group("s1", [config])
        await asyncio.sleep(2.5)

        await engine.stop_all("s1")
        assert len(alerts) > 0
        assert alerts[0]["data"]["alert"]["type"] == "booking"
        assert alerts[0]["data"]["alert"]["severity"] == "critical"

    def test_get_alerts(self, watch_engine):
        """获取告警历史"""
        alert = WatchAlert(watch_id="w1", type="queue", message="test")
        watch_engine._alert_history.append(alert)
        history = watch_engine.get_alerts()
        assert len(history) == 1
        assert history[0].watch_id == "w1"

    @pytest.mark.asyncio
    async def test_watch_auto_stop_on_cancel(self, watch_engine):
        """取消后自动停止"""
        config = WatchConfig(type=WatchType.QUEUE, poi_id="r1", node_id="n1",
                            poll_interval_s=60)
        await watch_engine.start_watch_group("s1", [config])
        assert watch_engine.get_active_count("s1") == 1

        await watch_engine.stop_all("s1")
        assert watch_engine.get_active_count("s1") == 0
        # 再次停止也应安全（幂等）
        await watch_engine.stop_all("s1")
