"""
Output Validator — 结构化输出强制

设计依据（赛题补充设计 4.1-4.4 节）：
1. Prompt 强约束：只输出 JSON，禁止 Markdown，禁止解释性前缀
2. JSON Schema 校验：字段完整性 + 类型 + 枚举合法性 + 无多余字段
3. 失败 3 次重试：第 1 次告知错误要求修复 → 第 2 次强制恢复结构 → 第 3 次模板降级
4. 降级输出：最小可用结果，不崩溃

核心流程：
```
LLM 输出
  → parse_json()          尝试解析
  → validate_schema()     校验字段/类型/枚举
  → 通过 → 返回结构化数据
  → 失败 → retry_count < 3 → 告知错误 → 要求 LLM 修复
  → 失败 → retry_count >= 3 → fallback_repair.repair() → 仍失败 → 模板降级
```
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from schemas import ALL_SCHEMAS, SCHEMA_TYPE_MAP
from core.fallback_repair import FallbackRepair

logger = logging.getLogger(__name__)


class SchemaValidationResult:
    """校验结果"""
    def __init__(self, passed: bool, data: Any = None,
                 errors: list[str] = None):
        self.passed = passed
        self.data = data
        self.errors = errors or []

    @classmethod
    def success(cls, data):
        return cls(passed=True, data=data)

    @classmethod
    def failure(cls, errors: list[str]):
        return cls(passed=False, errors=errors)


class OutputValidator:
    """
    结构化输出校验器。

    用法:
        validator = OutputValidator()

        # 校验 LLM 输出是否符合某个 Schema
        result = validator.validate(
            schema_type="itinerary_plan",
            raw_output=llm_response_text,
        )
        if result.passed:
            plan = result.data
        else:
            # 重试或降级
            fallback = validator.get_fallback("itinerary_plan")
    """

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.repairer = FallbackRepair()
        self._retry_counts: dict[str, int] = {}  # schema_type -> count

    # ── 主入口 ──

    def validate(self, schema_type: str, raw_output: str,
                 model: Optional[Type[BaseModel]] = None) -> SchemaValidationResult:
        """
        校验输出。

        参数:
            schema_type: 模式名（用于查 SCHEMA_TYPE_MAP）
            raw_output: LLM 输出的纯文本
            model: 可选，直接指定 Pydantic model

        返回: SchemaValidationResult
        """
        # 获取对应的 Pydantic model
        if model is None:
            schema_key = SCHEMA_TYPE_MAP.get(schema_type, schema_type)
            model = ALL_SCHEMAS.get(schema_key)
            if model is None:
                return SchemaValidationResult.failure(
                    [f"未知 schema_type: {schema_type} (key={schema_key})"]
                )

        # Step 1: 解析 JSON
        parsed = self._parse_json(raw_output)
        if parsed is None:
            # 尝试修复
            repaired = self.repairer.repair(raw_output)
            if repaired is None:
                return SchemaValidationResult.failure(["JSON 解析失败且修复无效"])
            parsed = repaired

        # Step 2: Schema 校验
        return self._validate_against_model(parsed, model)

    def validate_dict(self, schema_type: str, data: dict,
                      model: Optional[Type[BaseModel]] = None
                      ) -> SchemaValidationResult:
        """直接校验 dict（不需要经过 JSON 解析）"""
        if model is None:
            schema_key = SCHEMA_TYPE_MAP.get(schema_type, schema_type)
            model = ALL_SCHEMAS.get(schema_key)
            if model is None:
                return SchemaValidationResult.failure(
                    [f"未知 schema_type: {schema_type}"]
                )
        return self._validate_against_model(data, model)

    # ── 内部 ──

    def _parse_json(self, raw: str) -> Optional[dict]:
        """尝试从文本中解析 JSON"""
        if not raw or not raw.strip():
            return None

        text = raw.strip()

        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 尝试提取 ```json ... ``` 块
        json_match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL
        )
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 3. 尝试提取最外层 {...}
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _validate_against_model(self, data: dict, model: Type[BaseModel]
                                 ) -> SchemaValidationResult:
        """使用 Pydantic model 校验 dict"""
        try:
            instance = model(**data)
            return SchemaValidationResult.success(instance.model_dump())
        except ValidationError as e:
            errors = []
            for err in e.errors():
                field = ".".join(str(loc) for loc in err["loc"])
                msg = err["msg"]
                errors.append(f"{field}: {msg}")
            return SchemaValidationResult.failure(errors)

    # ── 重试与降级 ──

    def track_retry(self, schema_type: str) -> bool:
        """
        记录一次重试。
        返回: True 表示可以继续重试，False 表示已超限需降级
        """
        count = self._retry_counts.get(schema_type, 0) + 1
        self._retry_counts[schema_type] = count
        return count <= self.max_retries

    def reset_retries(self, schema_type: str):
        """成功后重置重试计数"""
        self._retry_counts.pop(schema_type, None)

    def get_fallback(self, schema_type: str) -> dict:
        """生成降级输出"""
        logger.warning("[Validator] %s 降级到模板", schema_type)
        self.reset_retries(schema_type)

        fallbacks = {
            "itinerary_plan": {
                "plan_id": "fallback",
                "summary": "方案生成失败，已切换到低风险模板",
                "status": "fallback",
                "nodes": [],
                "feasibility": {"passed": False},
            },
            "status_stream": {
                "items": [{
                    "id": "fb_001",
                    "level": "warning",
                    "message": "状态流生成失败",
                }],
            },
            "risk_modal": {
                "modal_id": "fb_modal",
                "severity": "warning",
                "title": "系统提示",
                "description": "异常处理方案生成失败，请稍后重试",
                "actions": [{
                    "action_id": "fb_retry",
                    "label": "重试",
                    "type": "confirm",
                    "recommended": True,
                    "payload": {},
                }],
            },
            "fulfillment_status": {
                "session_id": "",
                "overall_status": "all_failed",
                "progress_pct": 0,
                "tasks": [],
            },
        }
        return fallbacks.get(schema_type, {
            "status": "fallback",
            "message": "系统繁忙，请稍后重试",
        })

    def build_error_feedback(self, result: SchemaValidationResult,
                              retry_count: int) -> str:
        """
        构建给 LLM 的错误反馈（用于重试时告诉模型哪错了）。

        输出示例:
        "JSON Schema 校验失败（第 1 次）：
         - nodes.0.title: 字段必填
         - summary: 字段必填

        请只修复以上字段，不要修改业务内容。"
        """
        lines = [f"JSON Schema 校验失败（第 {retry_count} 次）："]
        for error in result.errors[:5]:
            lines.append(f" - {error}")
        if len(result.errors) > 5:
            lines.append(f" - ... 还有 {len(result.errors) - 5} 个错误")

        if retry_count == 1:
            lines.append("\n请修复以上字段，保持业务内容不变。")
        elif retry_count == 2:
            lines.append("\n只修复结构错误，不改业务内容。必须输出完整 JSON。")
        else:
            lines.append("\n最后一次重试，请确保输出有效 JSON。")

        return "\n".join(lines)


# ─── 快捷函数 ────────────────────────────────────────────────

_default_validator = None


def get_validator() -> OutputValidator:
    global _default_validator
    if _default_validator is None:
        _default_validator = OutputValidator()
    return _default_validator


def validate_output(schema_type: str, raw_output: str) -> SchemaValidationResult:
    """快捷校验"""
    return get_validator().validate(schema_type, raw_output)
