"""
Food Policy Rule
触发场景: 用户提到饮食偏好、限制、或 skip_restaurant 时调用

LLM 调用时机:
- 用户提到菜系/饮食限制 → 传入 dietary_prefs
- 用户说"不用吃饭/回家吃" → 传入 skip_restaurant=True
- 规划餐厅前调用，获取 search_restaurants 的过滤参数
"""
from __future__ import annotations

_CONFLICT_PAIRS = [
    ({"川菜", "火锅", "辣"}, {"清淡", "低油", "减脂"}),
    ({"清真"}, {"猪肉", "红烧肉"}),
    ({"素食"}, {"烤肉", "烧烤", "肉"}),
]


def check_food_policy(
    dietary_prefs: list[str] | None = None,
    skip_restaurant: bool = False,
    has_children: bool = False,
    child_age: int | None = None,
    female_weight_loss: bool = False,
    participant_preferences: dict | None = None,
) -> dict:
    """
    检查饮食约束并生成 search_restaurants 的参数提示。

    Args:
        dietary_prefs: 饮食偏好列表，如 ["川菜", "日料"]
        skip_restaurant: 是否不安排餐厅
        has_children: 是否有儿童
        child_age: 孩子年龄（影响辣度要求）
        female_weight_loss: 女生减脂需求
        participant_preferences: {owner: {food: [...], ...}} 多人偏好

    Returns:
        constraints, search_params_hint 指导 search_restaurants 调用。
    """
    result: dict = {
        "hard_blocks": [],
        "constraints": [],
        "recommendations": [],
        "search_params_hint": {
            "preferences": [],
            "exclude_tags": [],
            "require_tags": [],
        },
        "conflict_detected": False,
        "conflict_detail": [],
        "missing_for_complete_check": [],
        "questions_to_ask_user": [],
    }

    if skip_restaurant:
        result["hard_blocks"].append("不安排餐厅节点（用户明确不需要外出用餐）")
        result["search_params_hint"]["skip"] = True
        return result

    prefs = list(dietary_prefs or [])

    # 合并多人偏好
    if participant_preferences:
        for owner, pref in participant_preferences.items():
            for food in (pref.get("food") or []):
                if food not in prefs:
                    prefs.append(food)

    # 儿童饮食约束
    if has_children and child_age is not None and int(child_age) < 10:
        result["constraints"].append("有幼儿：餐厅需儿童友好，避免重辣菜系")
        result["search_params_hint"]["require_tags"].append("children_friendly")
        if "川菜" in prefs or "火锅" in prefs:
            result["recommendations"].append("建议选有清淡选项的川菜/火锅，或为孩子单点")

    # 减脂需求
    if female_weight_loss:
        result["constraints"].append("餐厅须有低卡/健康/轻食/沙拉选项")
        result["search_params_hint"]["require_tags"].append("healthy_options")
        if "烤肉" in prefs or "火锅" in prefs:
            result["recommendations"].append("烤肉/火锅店需确认有轻食蔬菜拼盘选项")

    # 偏好冲突检测
    pref_set = set(prefs)
    for group_a, group_b in _CONFLICT_PAIRS:
        if pref_set & group_a and pref_set & group_b:
            conflict_msg = f"偏好冲突: {pref_set & group_a} vs {pref_set & group_b}"
            result["conflict_detected"] = True
            result["conflict_detail"].append(conflict_msg)
            result["questions_to_ask_user"].append({
                "field": "food_preference_priority",
                "question": f"饮食偏好有冲突，以谁的为准？（{' vs '.join(str(x) for x in [group_a & pref_set, group_b & pref_set])}）",
                "priority": "medium",
                "trigger_condition": "多人偏好或同一人偏好出现矛盾",
            })

    # 构建 search_restaurants 的 preferences 参数
    if prefs:
        result["search_params_hint"]["preferences"] = prefs
        result["constraints"].append(f"餐厅偏好: {', '.join(prefs)}")

    return result
