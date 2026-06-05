"""Output validation helpers for LLM plans and current frontend nodes.

This module adapts the teammate backend's OutputValidator idea to the current
backend contract. It validates structured JSON when needed, and adds business
checks for frontend-shaped itinerary nodes without changing the public API.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from .models import ItineraryData, ItineraryNode


@dataclass
class SchemaValidationResult:
    passed: bool
    data: Any = None
    errors: list[str] = field(default_factory=list)

    @classmethod
    def success(cls, data: Any) -> "SchemaValidationResult":
        return cls(passed=True, data=data)

    @classmethod
    def failure(cls, errors: list[str]) -> "SchemaValidationResult":
        return cls(passed=False, errors=errors)


@dataclass
class ItineraryValidationResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)
        self.passed = False

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def issues(self) -> list[str]:
        return [*self.errors, *self.warnings]


class FallbackRepair:
    """Small JSON repair utility adapted from the teammate backend."""

    def repair(self, raw: str) -> Optional[dict[str, Any]]:
        if not raw or not raw.strip():
            return None
        text = raw.strip()

        for candidate in (
            text,
            self._extract_code_block(text),
            self._extract_outer_braces(text),
            self._fix_common_json_errors(text),
            self._complete_truncated(text),
        ):
            if not candidate:
                continue
            parsed = self._try_parse(candidate)
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _try_parse(text: str) -> Optional[Any]:
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _extract_code_block(text: str) -> Optional[str]:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_outer_braces(text: str) -> Optional[str]:
        stack: list[str] = []
        start = -1
        for index, ch in enumerate(text):
            if ch == "{":
                if not stack:
                    start = index
                stack.append(ch)
            elif ch == "}" and stack:
                stack.pop()
                if not stack:
                    return text[start:index + 1]
        return None

    def _fix_common_json_errors(self, text: str) -> str:
        fixed = re.sub(r"```\w*", "", text).strip()
        fixed = re.sub(r"^#+\s*", "", fixed, flags=re.MULTILINE)
        fixed = re.sub(r"^[^{]*", "", fixed)
        if "}" in fixed:
            fixed = fixed[:fixed.rindex("}") + 1]
        fixed = self._fix_single_quotes(fixed)
        fixed = re.sub(r",\s*}", "}", fixed)
        fixed = re.sub(r",\s*\]", "]", fixed)
        fixed = re.sub(r'(?<!")\b([A-Za-z_][A-Za-z0-9_]*)\b(?=\s*:)', r'"\1"', fixed)
        return fixed.replace("True", "true").replace("False", "false").replace("None", "null")

    @staticmethod
    def _complete_truncated(text: str) -> str:
        fixed = text.rstrip().rstrip(",")
        if fixed.count('"') % 2:
            fixed += '"'
        closers: list[str] = []
        for ch in fixed:
            if ch == "{":
                closers.append("}")
            elif ch == "[":
                closers.append("]")
            elif ch == "}" and closers and closers[-1] == "}":
                closers.pop()
            elif ch == "]" and closers and closers[-1] == "]":
                closers.pop()
        return fixed + "".join(reversed(closers))

    @staticmethod
    def _fix_single_quotes(text: str) -> str:
        result: list[str] = []
        in_double = False
        in_single = False
        escape = False
        for ch in text:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == "\\":
                result.append(ch)
                escape = True
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                result.append(ch)
            elif ch == "'" and not in_double:
                in_single = not in_single
                result.append('"')
            else:
                result.append(ch)
        return "".join(result)


class OutputValidator:
    def __init__(self) -> None:
        self.repairer = FallbackRepair()

    def validate_json(self, raw_output: str, model: Type[BaseModel]) -> SchemaValidationResult:
        parsed = self.repairer.repair(raw_output)
        if parsed is None:
            return SchemaValidationResult.failure(["JSON 解析失败，且自动修复无效"])
        return self.validate_dict(parsed, model)

    def validate_dict(self, data: dict[str, Any], model: Type[BaseModel]) -> SchemaValidationResult:
        try:
            instance = model(**data)
        except ValidationError as exc:
            errors = []
            for err in exc.errors():
                field_name = ".".join(str(part) for part in err.get("loc", []))
                errors.append(f"{field_name}: {err.get('msg', 'invalid')}")
            return SchemaValidationResult.failure(errors)
        return SchemaValidationResult.success(_model_to_dict(instance))

    def validate_itinerary_plan(
        self,
        plan: dict[str, Any],
        session_facts: dict[str, Any],
        preferences: Optional[dict[str, Any]] = None,
        allowed_poi_ids: Optional[set[str]] = None,
    ) -> ItineraryValidationResult:
        nodes = plan.get("nodes", []) if isinstance(plan, dict) else []
        result = validate_itinerary_nodes(nodes, session_facts, preferences, allowed_poi_ids)
        try:
            ItineraryData.from_frontend_plan(plan)
        except Exception as exc:
            result.add_warning(f"行程结构可读但未完全符合标准模型：{exc}")
        return result


_ACTIVITY_MATCHERS = {
    "park": ["公园", "户外", "草坪", "自然", "露营", "park"],
    "mall": ["商场", "购物", "购物中心", "mall", "Mall"],
    "exhibition": ["展览", "美术馆", "博物馆", "科技馆", "画展"],
    "citywalk": ["citywalk", "Citywalk", "街区", "老街", "步行街"],
    "social": ["剧本杀", "密室", "桌游"],
    "playground": ["亲子乐园", "游乐园", "儿童乐园", "乐园"],
}

_VENUE_MATCHERS = {
    "indoor": ["室内", "商场", "购物中心", "美术馆", "博物馆", "展览", "空调"],
    "mall": ["商场", "购物中心", "mall", "Mall", "购物"],
    "outdoor": ["户外", "公园", "草坪", "自然", "露天"],
}

_REQUIRED_NODE_FIELDS = ("id", "name", "type", "timeStart", "timeEnd")


def validate_itinerary_nodes(
    nodes: list[dict[str, Any]],
    session_facts: dict[str, Any],
    preferences: Optional[dict[str, Any]] = None,
    allowed_poi_ids: Optional[set[str]] = None,
) -> ItineraryValidationResult:
    result = ItineraryValidationResult()
    facts = session_facts or {}
    prefs = preferences or {}

    if not nodes:
        result.add_error("行程为空")
        return result

    canonical_nodes: list[ItineraryNode] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            result.add_error(f"第 {index + 1} 个节点不是对象")
            continue
        missing = [field for field in _REQUIRED_NODE_FIELDS if not node.get(field)]
        if missing:
            result.add_warning(f"「{node.get('name', f'节点{index + 1}')}」缺少字段: {', '.join(missing)}")
        try:
            canonical_nodes.append(ItineraryNode.from_frontend_dict(node))
        except Exception as exc:
            result.add_warning(f"「{node.get('name', f'节点{index + 1}')}」无法转换为标准节点: {exc}")

        poi_id = node.get("poiId") or node.get("poi_id")
        if allowed_poi_ids is not None and poi_id and poi_id not in allowed_poi_ids and poi_id != "walk_001":
            result.add_error(f"「{node.get('name', poi_id)}」的 poiId 不在工具返回结果中: {poi_id}")

    if len(nodes) > 6:
        result.add_warning(f"节点数 {len(nodes)} 超过6个上限")

    _validate_time_order(nodes, result)
    _validate_restaurant_rules(nodes, facts, prefs, result)
    _validate_user_requirements(nodes, facts, prefs, result)
    _validate_children_end_time(nodes, facts, result)
    return result


def _validate_time_order(nodes: list[dict[str, Any]], result: ItineraryValidationResult) -> None:
    for index, node in enumerate(nodes):
        start = _parse_hhmm(_node_time(node, "timeStart", "startTime"))
        end = _parse_hhmm(_node_time(node, "timeEnd", "endTime"))
        if start is None or end is None:
            continue
        if end <= start:
            result.add_warning(f"「{node.get('name', index + 1)}」结束时间不晚于开始时间")
        if index == 0:
            continue
        prev = nodes[index - 1]
        prev_end = _parse_hhmm(_node_time(prev, "timeEnd", "endTime"))
        if prev_end is not None and start < prev_end:
            result.add_warning(f"「{prev.get('name', index)}」→「{node.get('name', index + 1)}」时间重叠")


def _validate_restaurant_rules(
    nodes: list[dict[str, Any]],
    facts: dict[str, Any],
    prefs: dict[str, Any],
    result: ItineraryValidationResult,
) -> None:
    skip_restaurant = bool(prefs.get("skip_restaurant") or facts.get("skip_restaurant"))
    restaurant_nodes = [node for node in nodes if _is_restaurant(node)]
    if skip_restaurant and restaurant_nodes:
        result.add_warning("用户表示不外出用餐，但行程仍包含餐厅节点")

    for index in range(len(nodes) - 1):
        if _is_restaurant(nodes[index]) and _is_restaurant(nodes[index + 1]):
            result.add_warning("检测到连续两个餐厅节点")
            break

    food_prefs = _as_list(facts.get("food_preferences")) or _as_list(prefs.get("food"))
    if food_prefs and restaurant_nodes:
        restaurant_text = " ".join(_node_text(node) for node in restaurant_nodes)
        missing = [food for food in food_prefs if food and food not in restaurant_text]
        if missing:
            result.add_warning(f"用户要求饮食偏好「{'、'.join(food_prefs)}」，餐厅节点未明显体现: {'、'.join(missing)}")


def _validate_user_requirements(
    nodes: list[dict[str, Any]],
    facts: dict[str, Any],
    prefs: dict[str, Any],
    result: ItineraryValidationResult,
) -> None:
    activity_preference = facts.get("activity_preference")
    if activity_preference:
        words = _ACTIVITY_MATCHERS.get(activity_preference, [])
        activity_text = " ".join(_node_text(node) for node in nodes if not _is_restaurant(node))
        if words and not any(word in activity_text for word in words):
            label = facts.get("activity_preference_label") or activity_preference
            result.add_warning(f"用户活动偏好「{label}」未在活动节点中明显体现")

    venue = facts.get("venue_preference") or prefs.get("venue")
    if venue:
        words = _VENUE_MATCHERS.get(venue, [])
        text = " ".join(_node_text(node) for node in nodes if not _is_restaurant(node))
        if words and not any(word in text for word in words):
            label = {"indoor": "室内", "outdoor": "户外", "mall": "商场"}.get(venue, venue)
            result.add_warning(f"用户场地偏好「{label}」未在活动节点中明显体现")


def _validate_children_end_time(nodes: list[dict[str, Any]], facts: dict[str, Any], result: ItineraryValidationResult) -> None:
    has_child = facts.get("has_children") or facts.get("has_child") or bool(facts.get("child_age"))
    if not has_child:
        return
    last_end = _parse_hhmm(_node_time(nodes[-1], "timeEnd", "endTime"))
    if last_end is not None and last_end > 20 * 60:
        result.add_warning(f"结束时间 {_node_time(nodes[-1], 'timeEnd', 'endTime')} 超过20:00（有儿童场景）")


def _is_restaurant(node: dict[str, Any]) -> bool:
    return node.get("type") == "restaurant" or node.get("category") == "restaurant"


def _node_time(node: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        if value:
            return str(value)
    return ""


def _parse_hhmm(value: str) -> Optional[int]:
    try:
        hour, minute = map(int, value.split(":")[:2])
    except Exception:
        return None
    return hour * 60 + minute


def _node_text(node: dict[str, Any]) -> str:
    parts = [
        node.get("name", ""),
        node.get("sub", ""),
        node.get("reason", ""),
        node.get("planner_reason", ""),
        node.get("venue", ""),
        " ".join(str(tag) for tag in node.get("tags", []) or []),
        " ".join(str(tag) for tag in node.get("risk_facts", []) or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


_default_validator: Optional[OutputValidator] = None


def get_validator() -> OutputValidator:
    global _default_validator
    if _default_validator is None:
        _default_validator = OutputValidator()
    return _default_validator
