"""
Output Validator + Fallback Repair 测试
"""
import pytest
from core.output_validator import OutputValidator, SchemaValidationResult
from core.fallback_repair import FallbackRepair
from schemas import ItineraryPlanSchema, RiskModalSchema


class TestFallbackRepair:
    """JSON 修复器测试"""

    def setup_method(self):
        self.repairer = FallbackRepair()

    def test_valid_json_passes(self):
        """合法 JSON 直接通过"""
        result = self.repairer.repair('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_extract_code_block(self):
        """提取 ```json ``` 块"""
        text = '一些前言\n```json\n{"name": "test"}\n```\n一些后文'
        result = self.repairer.repair(text)
        assert result == {"name": "test"}

    def test_extract_code_block_no_lang(self):
        """提取 ``` ``` 块（无语言标记）"""
        text = '```\n{"valid": true}\n```'
        result = self.repairer.repair(text)
        assert result == {"valid": True}

    def test_extract_outer_braces(self):
        """提取最外层 {...}"""
        text = 'prefix text {"found": true} suffix text'
        result = self.repairer.repair(text)
        assert result == {"found": True}

    def test_fix_trailing_comma(self):
        """修复尾逗号"""
        text = '{"items": [1, 2, 3,], "end": true,}'
        result = self.repairer.repair(text)
        assert result == {"items": [1, 2, 3], "end": True}

    def test_fix_single_quotes(self):
        """单引号替换为双引号"""
        text = "{'key': 'value', 'num': 42}"
        result = self.repairer.repair(text)
        assert result == {"key": "value", "num": 42}

    def test_fix_unquoted_keys(self):
        """修复未引用的 key"""
        text = '{name: "test", value: 42}'
        result = self.repairer.repair(text)
        assert result == {"name": "test", "value": 42}

    def test_fix_python_bools(self):
        """修复 Python 风格的布尔值"""
        text = '{"active": True, "disabled": False, "data": None}'
        result = self.repairer.repair(text)
        assert result == {"active": True, "disabled": False, "data": None}

    def test_complete_truncated(self):
        """补齐截断的 JSON"""
        text = '{"name": "test", "items": [1, 2, 3'
        result = self.repairer.repair(text)
        assert result is not None
        assert result["name"] == "test"
        assert result["items"] == [1, 2, 3]

    def test_strip_explanatory_prefix(self):
        """去掉解释性前缀"""
        text = 'Here is the result:\n{"key": "value"}'
        result = self.repairer.repair(text)
        assert result == {"key": "value"}

    def test_empty_input(self):
        """空输入返回 None"""
        assert self.repairer.repair("") is None
        assert self.repairer.repair(None) is None

    def test_gibberish_input(self):
        """乱码输入返回 None"""
        assert self.repairer.repair("asdf qwer zxcv") is None

    def test_mixed_content(self):
        """混合内容能正确提取 JSON"""
        text = """
        好的，这是为您生成的方案：
        {
            "plan_id": "plan_001",
            "summary": "下午出行方案",
            "nodes": [
                {"node_id": "n1", "title": "活动1"}
            ]
        }
        如有需要可以调整。
        """
        result = self.repairer.repair(text)
        assert result is not None
        assert result["plan_id"] == "plan_001"


class TestOutputValidator:
    """Output Validator 测试"""

    def setup_method(self):
        self.validator = OutputValidator()

    def test_validate_valid_itinerary_plan(self):
        """合法行程方案通过校验"""
        text = '''
        {
            "plan_id": "plan_001",
            "summary": "下午2点出发",
            "scene": "family",
            "mode": "light_managed",
            "total_duration_min": 210,
            "start_time": "14:00",
            "end_time": "18:00",
            "nodes": [
                {
                    "node_id": "n1",
                    "type": "activity",
                    "poi_id": "act_001",
                    "title": "亲子乐园",
                    "start_time": "14:20",
                    "end_time": "16:20",
                    "duration_min": 120,
                    "status": "draft",
                    "tags": ["室内"]
                }
            ],
            "feasibility": {
                "commute_ratio_ok": true,
                "weather_ok": true,
                "queue_ok": true,
                "child_safety_ok": true
            }
        }
        '''
        result = self.validator.validate("plan_complete", text)
        assert result.passed, f"校验失败: {result.errors}"
        assert result.data["plan_id"] == "plan_001"

    def test_validate_invalid_missing_field(self):
        """缺少必填字段 → 校验失败"""
        text = '{"nodes": [{"node_id": "n1"}]}'  # missing many required fields
        result = self.validator.validate("plan_complete", text)
        assert not result.passed
        assert len(result.errors) > 0

    def test_validate_risk_modal(self):
        """风险弹窗 Schema 校验"""
        text = '''
        {
            "modal_id": "m1",
            "severity": "warning",
            "title": "排队过长",
            "description": "当前排队约90分钟",
            "affected_node_id": "n2",
            "actions": [
                {
                    "action_id": "a1",
                    "label": "切换餐厅",
                    "type": "confirm",
                    "recommended": true,
                    "payload": {}
                }
            ]
        }
        '''
        result = self.validator.validate("risk_alert", text)
        assert result.passed, f"校验失败: {result.errors}"
        assert result.data["modal_id"] == "m1"

    def test_json_in_code_block(self):
        """```json``` 块被正确解析并校验"""
        text = ('```json\n{"plan_id": "p1", "summary": "test", "scene": "family",'
                ' "mode": "light_managed", "total_duration_min": 60,'
                ' "start_time": "14:00", "end_time": "15:00",'
                ' "nodes": [], "feasibility": {}}\n```')
        result = self.validator.validate("plan_complete", text)
        assert result.passed, f"校验失败: {result.errors}"
        assert result.data["plan_id"] == "p1"

    def test_track_retry(self):
        """重试计数"""
        assert self.validator.track_retry("test_type") is True
        assert self.validator.track_retry("test_type") is True
        assert self.validator.track_retry("test_type") is True
        # 第 4 次应返回 False（已超过 max_retries=3）
        assert self.validator.track_retry("test_type") is False

    def test_reset_retries(self):
        """重试计数重置"""
        self.validator.track_retry("test_type")
        self.validator.track_retry("test_type")
        self.validator.reset_retries("test_type")
        assert self.validator.track_retry("test_type") is True  # 重置后第 1 次

    def test_get_fallback_returns_structured(self):
        """降级输出包含 status=fallback"""
        fallback = self.validator.get_fallback("itinerary_plan")
        assert fallback["status"] == "fallback"
        assert "nodes" in fallback

    def test_build_error_feedback(self):
        """错误反馈包含字段名和重试次数"""
        result = SchemaValidationResult(False, errors=[
            "nodes.0.title: 字段必填",
        ])
        feedback = self.validator.build_error_feedback(result, 1)
        assert "第 1 次" in feedback
        assert "title" in feedback

    def test_validate_dict_direct(self):
        """直接校验 dict（不走 JSON 解析）"""
        data = {
            "plan_id": "p1",
            "summary": "test",
            "scene": "family",
            "mode": "light_managed",
            "total_duration_min": 60,
            "start_time": "14:00",
            "end_time": "15:00",
            "nodes": [],
            "feasibility": {},
        }
        result = self.validator.validate_dict("plan_complete", data)
        assert result.passed

    def test_unknown_schema_type(self):
        """未知 schema_type 返回失败"""
        result = self.validator.validate("unknown_type", "{}")
        assert not result.passed

    def test_validate_empty_string(self):
        """空字符串 → 降级"""
        result = self.validator.validate("plan_complete", "")
        assert not result.passed
