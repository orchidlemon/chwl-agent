"""
Group Policy Rule
触发场景: 识别到特殊同行人群（老人/儿童/大人数朋友群）时调用

LLM 调用时机:
- 用户提到"老人/爸妈/爷爷奶奶" → 传入 has_elderly=True, elderly_no_walking=None
- 用户提到"朋友/同事/同学" → 传入 scenario="friends", male_count/female_count
- 用户描述了性别构成 → 传入对应 gender 字段

返回的 questions_to_ask_user 告诉 LLM 在继续规划前要向用户确认什么。
"""
from __future__ import annotations


def check_group_policy(
    scenario: str | None = None,
    has_elderly: bool | None = None,
    elderly_no_walking: bool | None = None,
    has_children: bool | None = None,
    child_age: int | None = None,
    child_purpose: str | None = None,
    male_count: int | None = None,
    female_count: int | None = None,
    friends_activity_type: str | None = None,
    female_weight_loss: bool | None = None,
    female_prefer_low_intensity: bool | None = None,
    female_prefer_indoor: bool | None = None,
    male_prefer_high_intensity: bool | None = None,
    group_size: int | None = None,
) -> dict:
    """
    检查同行人群带来的活动约束与追问需求。

    Returns:
        同 age_policy 结构，额外有 search_params_hint 指导 search_activities 的参数。
    """
    result: dict = {
        "hard_blocks": [],
        "constraints": [],
        "recommendations": [],
        "search_filter_hints": {
            "exclude_tags": [],
            "require_tags": [],
            "preferred_categories": [],
        },
        "search_params_hint": {},
        "missing_for_complete_check": [],
        "questions_to_ask_user": [],
    }

    # ── 老人 ─────────────────────────────────────────────────────────
    if has_elderly is True:
        result["constraints"].append("全程步行距离 ≤ 800m/段")
        result["constraints"].append("避免久站、高强度体力活动")
        result["search_filter_hints"]["require_tags"].append("accessible")
        result["search_filter_hints"]["exclude_tags"].extend(["high_intensity", "mountain_hiking"])

        if elderly_no_walking is None:
            result["missing_for_complete_check"].append("elderly_no_walking")
            result["questions_to_ask_user"].append({
                "field": "elderly_no_walking",
                "question": "老人步行方便吗？需要避免大量步行的景区吗？",
                "priority": "high",
                "trigger_condition": "has_elderly=True 且 步行能力未知",
            })
        elif elderly_no_walking is True:
            result["constraints"].append("节点间必须可打车直达，不选需要大量步行的开放式景区")
            result["search_filter_hints"]["exclude_tags"].extend(["outdoor_park", "large_open_area"])

    # ── 儿童目的 ─────────────────────────────────────────────────────
    if has_children is True and child_age is not None:
        if child_purpose is None:
            result["missing_for_complete_check"].append("child_purpose")
            result["questions_to_ask_user"].append({
                "field": "child_purpose",
                "question": "这次带孩子主要是科普学习（博物馆/科技馆）还是轻松好玩为主？",
                "priority": "high",
                "trigger_condition": "has_children=True 且 child_purpose 未知",
            })
        elif child_purpose == "education":
            result["recommendations"].append("优先科技馆、博物馆、自然博物馆等教育属性强的场所")
            result["search_filter_hints"]["preferred_categories"].extend([
                "museum", "science_center", "natural_history"
            ])
        elif child_purpose == "fun":
            result["recommendations"].append("优先游乐园、亲子乐园、游戏体验类场所")
            result["search_filter_hints"]["preferred_categories"].extend([
                "indoor_playground", "amusement_park", "kids_experience"
            ])

        age = int(child_age)
        if 3 <= age <= 6:
            result["recommendations"].append("适合3-6岁: 动物园、幼儿体验馆、儿童乐园")
        elif 7 <= age <= 12:
            result["recommendations"].append("适合7-12岁: 科技馆、博物馆、自然主题")

    # ── 朋友出行 ─────────────────────────────────────────────────────
    if scenario == "friends":
        if male_count is None and female_count is None:
            result["missing_for_complete_check"].extend(["male_count", "female_count"])
            result["questions_to_ask_user"].append({
                "field": "gender_composition",
                "question": "你们男生女生各几个？",
                "priority": "medium",
                "trigger_condition": "scenario=friends 且 性别构成未知",
            })

        if friends_activity_type is None:
            result["missing_for_complete_check"].append("friends_activity_type")
            result["questions_to_ask_user"].append({
                "field": "friends_activity_type",
                "question": "大家更倾向哪类活动？①社交互动(剧本杀/密室) ②文化展览 ③逛街购物 ④出片打卡 ⑤混搭",
                "priority": "high",
                "trigger_condition": "scenario=friends 且 活动偏好未知",
            })
        else:
            type_map = {
                "social": ["script_kill", "escape_room", "board_game"],
                "exhibition": ["museum", "exhibition", "art_gallery"],
                "mall": ["mall_exhibition", "shopping_center"],
                "photo_spot": ["citywalk", "landmark", "art_installation"],
                "mixed": [],
            }
            cats = type_map.get(friends_activity_type, [])
            if cats:
                result["search_filter_hints"]["preferred_categories"].extend(cats)
            result["search_params_hint"]["categories"] = ",".join(cats) if cats else ""

    # ── 女生偏好 ─────────────────────────────────────────────────────
    if female_count and int(female_count) > 0:
        if female_weight_loss is True:
            result["constraints"].append("餐厅必须有低卡/健康/轻食选项")
            result["search_filter_hints"]["require_tags"].append("healthy_options")

        if female_prefer_indoor is True:
            result["constraints"].append("优先室内场所，天气不佳时绝对室内")
            result["search_filter_hints"]["preferred_categories"].extend(["indoor"])
            result["search_params_hint"]["venue_preference"] = "indoor"

        if female_prefer_low_intensity is True:
            result["constraints"].append("不安排爬山、徒步、高强度运动")
            result["search_filter_hints"]["exclude_tags"].extend(["high_intensity", "hiking"])

    # ── 男生偏好 ─────────────────────────────────────────────────────
    if male_prefer_high_intensity is True:
        result["recommendations"].append("可选户外运动、爬山、运动公园、体育场馆")
        result["search_filter_hints"]["preferred_categories"].append("sports_outdoor")

    return result
