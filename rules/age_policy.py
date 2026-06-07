"""
Age Policy Rule
触发场景: 已知有孩子 / 用户提到酒吧夜店 / 涉及密室剧本杀

LLM 调用时机:
- 用户提到"孩子/娃/儿子/女儿" → 立即调用，传入已知 child_age（可为 None）
- 用户提到"酒吧/夜店/喝酒" → 立即调用，传入 has_minor 和 all_adults_confirmed

返回的 questions_to_ask_user 告诉 LLM 在继续规划前需向用户确认什么。
"""
from __future__ import annotations


def check_age_policy(
    child_age: int | None = None,
    has_minor: bool | None = None,
    venue_types_requested: list[str] | None = None,
    all_adults_confirmed: bool | None = None,
) -> dict:
    """
    检查年龄相关的场所限制和法律约束。

    Args:
        child_age: 孩子年龄（岁）。已知时传入，未知传 None。
        has_minor: 是否存在未成年人。True=确认有，None=不确定，False=全成年。
        venue_types_requested: 用户提到想去的场所类型列表，如 ["bar","escape_room"]。
        all_adults_confirmed: 用户是否确认所有人成年（用于酒吧场景）。

    Returns:
        {
          hard_blocks: 绝对禁止的场所/行为列表,
          constraints: 软性约束,
          recommendations: 推荐方向,
          search_filter_hints: 传给 search_activities 的 exclude_tags,
          missing_for_complete_check: 规则还需要哪些字段,
          questions_to_ask_user: [{field, question, priority, trigger_condition}]
        }
    """
    result: dict = {
        "hard_blocks": [],
        "constraints": [],
        "recommendations": [],
        "search_filter_hints": {"exclude_tags": [], "require_tags": []},
        "missing_for_complete_check": [],
        "questions_to_ask_user": [],
    }

    venue_types = venue_types_requested or []
    alcohol_venues = {"bar", "nightclub", "alcohol_bar", "酒吧", "夜店", "清吧"}
    escape_room_venues = {"escape_room", "script_kill", "密室", "剧本杀"}

    wants_alcohol = bool(alcohol_venues & set(venue_types))
    wants_escape = bool(escape_room_venues & set(venue_types))

    # ── 情形1: 确认有未成年人 ─────────────────────────────────────────
    if has_minor is True or (child_age is not None and int(child_age) < 18):
        result["hard_blocks"].append("禁止酒吧、夜店、清吧、一切以饮酒为主题的场所（未成年人保护法）")
        result["search_filter_hints"]["exclude_tags"].extend(["bar", "nightclub", "alcohol"])

        if child_age is None:
            # 知道有孩子但不知道年龄 → 必须问
            result["missing_for_complete_check"].append("child_age")
            result["questions_to_ask_user"].append({
                "field": "child_age",
                "question": "孩子大概几岁呀？（影响密室/剧本杀的主题选择）",
                "priority": "high",
                "trigger_condition": "has_minor=True 且 child_age 未知",
            })
        else:
            age = int(child_age)
            if age < 5:
                result["hard_blocks"].append("禁止密室逃脱、剧本杀（孩子不足5岁）")
                result["search_filter_hints"]["exclude_tags"].extend(["escape_room", "script_kill"])
            elif 5 <= age <= 13:
                result["hard_blocks"].append("禁止恐怖/惊悚主题密室逃脱和剧本杀")
                result["constraints"].append("密室/剧本杀仅限亲子型、推理型、轻松型")
                result["search_filter_hints"]["exclude_tags"].append("horror_theme")
                result["search_filter_hints"]["require_tags"].append("family_friendly")
            # 14岁及以上: 密室无主题限制

        result["constraints"].append("末节点结束时间 ≤ 20:00（有儿童）")
        result["constraints"].append("单段步行距离 ≤ 500m（有儿童）")
        result["recommendations"].append("优先亲子友好、安全评级高的场所")

    # ── 情形2: 不确定是否有未成年人，但用户要去酒吧 ──────────────────
    elif wants_alcohol and all_adults_confirmed is None and has_minor is None:
        result["missing_for_complete_check"].append("all_adults_confirmed")
        result["questions_to_ask_user"].append({
            "field": "all_adults_confirmed",
            "question": "请确认同行所有人均已年满18周岁（酒吧场所的法律要求）",
            "priority": "legal",
            "trigger_condition": "用户提到酒吧/夜店 且 未确认成年状态",
        })

    # ── 情形3: 已确认全员成年 ─────────────────────────────────────────
    elif all_adults_confirmed is True:
        result["constraints"].append("酒吧/夜店场所已确认全员成年，可安排")

    # ── 密室追问（有孩子但年龄未知时已处理；无孩子时的单独判断） ────────
    if wants_escape and has_minor is None and child_age is None:
        result["missing_for_complete_check"].append("has_minor")
        result["questions_to_ask_user"].append({
            "field": "has_minor",
            "question": "同行有未成年人吗？（影响密室主题的选择）",
            "priority": "medium",
            "trigger_condition": "用户提到密室/剧本杀 且 未成年状态未知",
        })

    return result
