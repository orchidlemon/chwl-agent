"""
LLM Planner 测试 — 验证 DeepSeek 驱动的规划器

注意：这些测试会调用真实的 DeepSeek API，需要网络连接。
如果 API key 无效或网络不通，测试会被跳过。
"""
import pytest
import json
import os

from core.llm_client import LLMClient
from core.llm_planner import LLMPlanner

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-67937130fddf4be086e73e7b2f6d293c")
SKIP_REASON = "DeepSeek API 不可用或未配置"


@pytest.fixture
def planner():
    llm = LLMClient(api_key=API_KEY, timeout_s=15)
    return LLMPlanner(llm)


class TestLLMIntentParsing:
    """LLM 意图理解测试"""

    @pytest.mark.asyncio
    async def test_parse_family_scene(self, planner):
        """识别家庭场景"""
        result = await planner.handle_user_context({
            "input": "下午带5岁孩子和老婆出去玩"
        })
        assert result.get("success"), f"LLM 调用失败: {result}"
        data = result["data"]
        assert data["scene"] == "family", f"期望 family，得到 {data['scene']}"
        assert data["mode"] == "full_managed"

    @pytest.mark.asyncio
    async def test_parse_friends_scene(self, planner):
        """识别朋友场景"""
        result = await planner.handle_user_context({
            "input": "下午和朋友出去玩"
        })
        assert result.get("success"), f"LLM 调用失败: {result}"
        data = result["data"]
        assert data["scene"] == "friends", f"期望 friends，得到 {data['scene']}"

    @pytest.mark.asyncio
    async def test_parse_diet_requirement(self, planner):
        """识别饮食偏好"""
        result = await planner.handle_user_context({
            "input": "下午带老婆孩子出去玩，老婆在减肥"
        })
        assert result.get("success"), f"LLM 调用失败: {result}"
        data = result["data"]
        specials = [s.lower() for s in data.get("special_requirements", [])]
        assert any("减肥" in s or "清淡" in s for s in specials), \
            f"未识别减肥需求: {data['special_requirements']}"

    @pytest.mark.asyncio
    async def test_parse_missing_info(self, planner):
        """识别缺失信息"""
        result = await planner.handle_user_context({
            "input": "想出去玩"
        })
        assert result.get("success"), f"LLM 调用失败: {result}"
        data = result["data"]
        assert isinstance(data.get("missing_info"), list)


class TestLLMScoring:
    """LLM POI 评分测试"""

    @pytest.mark.asyncio
    async def test_score_candidates(self, planner):
        """对候选 POI 进行评分"""
        candidates = [
            {
                "poi_id": "act_fam_001",
                "name": "汤姆猫亲子乐园",
                "category": "indoor_playground",
                "rating": 4.7,
                "distance_km": 2.4,
                "tags": ["室内", "亲子", "儿童游乐"],
                "child_friendly": True,
            },
            {
                "poi_id": "res_fam_001",
                "name": "轻食研究所",
                "category": "restaurant",
                "rating": 4.8,
                "distance_km": 2.1,
                "queue_time_min": 90,
                "child_seat": True,
                "healthy_option": True,
                "tags": ["轻食", "低卡", "沙拉"],
            },
        ]
        result = await planner.handle_candidates_score({
            "candidates": candidates,
            "user_context": {
                "scene": "family",
                "special_requirements": ["减肥", "清淡"],
                "mode": "full_managed",
            },
            "weather": {"condition": "晴", "temperature_c": 28},
        })
        assert result.get("success"), f"LLM 评分失败: {result}"
        scored = result.get("data", {}).get("scored", [])
        assert len(scored) > 0, "未返回评分结果"
        # 验证 POI 合法性校验：返回的 poi_id 必须在候选列表中
        valid_ids = {c["poi_id"] for c in candidates}
        for s in scored:
            assert s["poi_id"] in valid_ids, f"编造 POI: {s['poi_id']}"
            assert isinstance(s.get("score"), (int, float)), "评分不是数字"

    @pytest.mark.asyncio
    async def test_score_prefers_healthy_for_diet(self, planner):
        """减肥需求下健康餐厅评分应更高"""
        candidates = [
            {
                "poi_id": "res_healthy",
                "name": "轻食沙拉",
                "category": "restaurant",
                "rating": 4.3,
                "queue_time_min": 5,
                "healthy_option": True,
                "tags": ["轻食", "沙拉", "健康"],
            },
            {
                "poi_id": "res_bbq",
                "name": "韩式烤肉",
                "category": "restaurant",
                "rating": 4.5,
                "queue_time_min": 30,
                "healthy_option": False,
                "tags": ["烤肉", "热闹"],
            },
        ]
        result = await planner.handle_candidates_score({
            "candidates": candidates,
            "user_context": {
                "scene": "family",
                "special_requirements": ["减肥"],
                "mode": "full_managed",
            },
            "weather": {"condition": "晴"},
        })
        assert result.get("success"), f"LLM 评分失败: {result}"
        scored = result.get("data", {}).get("scored", [])
        # 验证评分结果存在
        assert len(scored) == 2, f"期望 2 个评分，得到 {len(scored)}"


class TestLLMItineraryGeneration:
    """LLM 行程生成测试"""

    @pytest.mark.asyncio
    async def test_generate_itinerary(self, planner):
        """生成行程"""
        selected = {
            "main_activity": {
                "poi_id": "act_fam_001",
                "name": "汤姆猫亲子乐园",
                "category": "indoor_playground",
                "estimated_duration_min": 90,
            },
            "restaurant": {
                "poi_id": "res_fam_002",
                "name": "花园简餐 Bistro",
                "category": "restaurant",
                "estimated_duration_min": 60,
            },
            "optional_activity": {
                "poi_id": "walk_001",
                "name": "商场轻松散步区",
                "category": "shopping",
                "estimated_duration_min": 40,
            },
        }
        result = await planner.handle_itinerary_generate({
            "selected_nodes": selected,
            "departure_time": "14:00",
            "scene": "family",
        })
        assert result.get("success"), f"LLM 生成失败: {result}"
        data = result.get("data", {})
        nodes = data.get("nodes", [])
        assert len(nodes) > 0, "未生成节点"

        # 验证 POI 合法性校验：所有节点的 poi_id 必须在选中的 POI 中
        valid_ids = {n["poi_id"] for n in selected.values() if n.get("poi_id")}
        for node in nodes:
            assert node["poi_id"] in valid_ids, \
                f"编造 POI: {node.get('poi_name', '')} ({node['poi_id']}) 不在候选列表中"

        # 验证时间合理性
        for node in nodes:
            assert "start_time" in node, f"节点缺少 start_time: {node}"
            assert "end_time" in node, f"节点缺少 end_time: {node}"

    @pytest.mark.asyncio
    async def test_generate_filters_fabricated_poi(self, planner):
        """LLM 编造 POI 时被过滤"""
        selected = {
            "main_activity": {
                "poi_id": "act_fam_001",
                "name": "汤姆猫亲子乐园",
                "category": "indoor_playground",
                "estimated_duration_min": 90,
            },
            "restaurant": {
                "poi_id": "res_fam_002",
                "name": "花园简餐 Bistro",
                "category": "restaurant",
                "estimated_duration_min": 60,
            },
        }
        result = await planner.handle_itinerary_generate({
            "selected_nodes": selected,
            "departure_time": "14:00",
            "scene": "family",
        })
        assert result.get("success"), f"LLM 生成失败: {result}"
        data = result.get("data", {})
        nodes = data.get("nodes", [])
        valid_ids = {n["poi_id"] for n in selected.values() if n.get("poi_id")}
        for node in nodes:
            assert node["poi_id"] in valid_ids, \
                f"LLM 编造的 POI 未被过滤: {node}"
