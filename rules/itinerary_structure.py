"""
Itinerary Structure Rule
校验行程节点结构合法性，并对比用户原始偏好与规划结果，输出偏好未满足项。

LLM 调用时机:
- 在 finish_planning 之前，传入草拟的 nodes 和用户偏好
- violations 非空 → 修正后再提交
- preference_violations 非空 → 在 finish_planning.unmet_preferences 中列出
"""
from __future__ import annotations


def check_itinerary_structure(
    nodes: list[dict] | None = None,
    skip_restaurant: bool = False,
    has_children: bool = False,
    start_time: str | None = None,
    duration_hours: float | None = None,
    user_prefs: dict | None = None,
) -> dict:
    """
    校验行程节点结构，并检测用户偏好是否得到满足。

    Args:
        nodes: 草拟节点列表
        skip_restaurant: 是否跳过餐厅
        has_children: 是否有儿童
        start_time: 出发时间 HH:MM
        duration_hours: 总时长（小时）
        user_prefs: 用户原始偏好，格式:
            {
              "food": ["日料", "川菜"],      # 饮食偏好
              "activity": "博物馆",           # 活动类型偏好
              "venue": "outdoor",            # 场地偏好
              "skip_restaurant": false       # 是否跳过餐厅
            }

    Returns:
        {
          valid: bool,
          violations: [{rule, node_ids, message, fix_suggestion}],
          preference_violations: [{preference, type, found_in_plan, reason, detail}],
          structure_summary: 节点统计,
          missing_for_complete_check: [],
          questions_to_ask_user: []
        }
    """
    result: dict = {
        "valid": True,
        "violations": [],
        "preference_violations": [],
        "structure_summary": {},
        "missing_for_complete_check": [],
        "questions_to_ask_user": [],
    }

    if not nodes:
        result["valid"] = False
        result["violations"].append({
            "rule": "min_nodes",
            "node_ids": [],
            "message": "节点列表为空，至少需要 4 个节点",
            "fix_suggestion": "先调用 search_activity 和 search_restaurant 获取 POI 数据",
        })
        return result

    def to_minutes(t: str | None) -> int | None:
        if not t:
            return None
        try:
            clean = str(t).replace("次日", "").strip()
            h, m = map(int, clean.split(":"))
            return h * 60 + m
        except Exception:
            return None

    node_count = len(nodes)
    activity_nodes = [n for n in nodes if n.get("type") == "activity"]
    restaurant_nodes = [n for n in nodes if n.get("type") == "restaurant"]

    result["structure_summary"] = {
        "total": node_count,
        "activities": len(activity_nodes),
        "restaurants": len(restaurant_nodes),
    }

    # ── 节点数量检查 ──────────────────────────────────────────────────
    if node_count < 4:
        result["valid"] = False
        result["violations"].append({
            "rule": "min_nodes",
            "node_ids": [],
            "message": f"当前仅 {node_count} 个节点，需要 4-6 个",
            "fix_suggestion": "增加活动或餐厅节点",
        })
    elif node_count > 6:
        result["violations"].append({
            "rule": "max_nodes",
            "node_ids": [n.get("id") for n in nodes[6:]],
            "message": f"节点数 {node_count} 超过上限 6 个",
            "fix_suggestion": "删除评分最低或最不符合偏好的节点",
        })

    # ── 活动数量检查 ──────────────────────────────────────────────────
    if len(activity_nodes) < 2:
        result["valid"] = False
        result["violations"].append({
            "rule": "min_activities",
            "node_ids": [],
            "message": f"活动节点仅 {len(activity_nodes)} 个，需要至少 2-3 个",
            "fix_suggestion": "增加 search_activity 搜索结果中的节点",
        })

    # Restaurant interval: at least one activity must appear between any two restaurants
    last_restaurant = None
    has_activity_after_last_restaurant = True
    for node in nodes:
        node_type = node.get("type")
        if node_type == "activity":
            has_activity_after_last_restaurant = True
        elif node_type == "restaurant":
            if last_restaurant is not None and not has_activity_after_last_restaurant:
                result["valid"] = False
                result["violations"].append({
                    "rule": "restaurant_requires_activity_between",
                    "node_ids": [last_restaurant.get("id"), node.get("id")],
                    "message": (
                        f"{last_restaurant.get('name', '\u4e0a\u4e00\u5bb6\u9910\u5385')} \u548c "
                        f"{node.get('name', '\u4e0b\u4e00\u5bb6\u9910\u5385')} \u4e4b\u95f4\u6ca1\u6709\u6d3b\u52a8\u8282\u70b9"
                    ),
                    "fix_suggestion": "\u4e24\u4e2a\u9910\u5385\u4e4b\u95f4\u5fc5\u987b\u81f3\u5c11\u63d2\u5165\u4e00\u4e2a type=activity \u7684\u6d3b\u52a8\uff1b\u4ea4\u901a\u3001\u4f11\u606f\u3001\u6392\u961f\u3001light \u8282\u70b9\u4e0d\u7b97\u6d3b\u52a8",
                })
            last_restaurant = node
            has_activity_after_last_restaurant = False

    # ── 时间连续性检查 ────────────────────────────────────────────────
    prev_end = None
    for node in nodes:
        start_min = to_minutes(node.get("timeStart") or node.get("startTime"))
        end_min = to_minutes(node.get("timeEnd") or node.get("endTime"))
        dur = node.get("duration", 0)
        if not isinstance(dur, (int, float)):
            dur = end_min - start_min if start_min is not None and end_min is not None else 0

        min_duration = node.get("min_duration_min")
        max_duration = node.get("max_duration_min")
        recommended = node.get("recommended_duration_min") or node.get("estimated_duration_min")
        if min_duration is None or max_duration is None:
            category = node.get("category") or node.get("type")
            duration_defaults = {
                "restaurant": (35, 120, 60),
                "museum": (60, 150, 120),
                "art_gallery": (45, 120, 90),
                "exhibition": (45, 120, 90),
                "script_kill": (150, 270, 210),
                "escape_room": (60, 120, 90),
                "board_game": (90, 210, 120),
                "mall_exhibition": (45, 150, 90),
                "shopping_center": (60, 180, 120),
                "citywalk": (60, 180, 90),
                "livehouse": (90, 180, 120),
            }
            fallback = duration_defaults.get(category, (45, 180, 90))
            min_duration = min_duration if min_duration is not None else fallback[0]
            max_duration = max_duration if max_duration is not None else fallback[1]
            recommended = recommended or fallback[2]

        if dur and min_duration and dur < int(min_duration):
            result["valid"] = False
            result["violations"].append({
                "rule": "duration_too_short",
                "node_ids": [node.get("id")],
                "message": f"{node.get('name','?')} 停留 {dur}min 太短，至少需要 {min_duration}min",
                "fix_suggestion": "延长该节点停留时间，或替换为更短时长的候选",
                "recommended_duration_min": recommended,
            })
        if dur and max_duration and dur > int(max_duration):
            result["violations"].append({
                "rule": "duration_too_long",
                "node_ids": [node.get("id")],
                "message": f"{node.get('name','?')} 停留 {dur}min 过长，建议不超过 {max_duration}min",
                "fix_suggestion": "缩短该节点，或补充一个新活动节点",
                "recommended_duration_min": recommended,
            })

        if start_min is not None and prev_end is not None:
            transit = start_min - prev_end
            stay = dur or (end_min - start_min if end_min else 60)
            if transit > stay:
                result["violations"].append({
                    "rule": "transit_le_stay",
                    "node_ids": [node.get("id")],
                    "message": f"{node.get('name','?')} 通勤 {transit}min > 停留 {stay}min",
                    "fix_suggestion": "选择距离更近的替代 POI，或缩短通勤路段",
                })

        if end_min is not None:
            prev_end = end_min

    # ── 儿童结束时间检查 ──────────────────────────────────────────────
    if has_children and nodes:
        last_end = to_minutes(nodes[-1].get("timeEnd") or nodes[-1].get("endTime"))
        if last_end and last_end > 20 * 60:
            result["valid"] = False
            result["violations"].append({
                "rule": "children_end_time",
                "node_ids": [nodes[-1].get("id")],
                "message": f"有儿童时行程最晚 20:00 结束，当前结束于 {nodes[-1].get('timeEnd')}",
                "fix_suggestion": "压缩最后节点时长或删除最后节点",
            })

    if result["violations"] and all(
        v.get("rule") not in ("min_nodes", "min_activities",
                              "restaurant_requires_activity_between", "children_end_time")
        for v in result["violations"]
    ):
        result["valid"] = True

    # ── 用户偏好对比检查 ──────────────────────────────────────────────
    if user_prefs:
        food_prefs = user_prefs.get("food") or []
        if isinstance(food_prefs, str):
            food_prefs = [p.strip() for p in food_prefs.split(",") if p.strip()]
        activity_pref = user_prefs.get("activity")
        venue_pref = user_prefs.get("venue")
        want_restaurant = not user_prefs.get("skip_restaurant", False)

        # --- 饮食偏好对比 ---
        if food_prefs and want_restaurant:
            if not restaurant_nodes:
                for pref in food_prefs:
                    result["preference_violations"].append({
                        "preference": pref,
                        "type": "food",
                        "found_in_plan": False,
                        "reason": "no_restaurant_in_plan",
                        "detail": f"行程中无餐厅节点，{pref}偏好未能安排",
                    })
            else:
                rest_texts = []
                for rn in restaurant_nodes:
                    rest_texts.append(rn.get("name", "").lower())
                    rest_texts.extend(t.lower() for t in rn.get("tags", []))
                    rest_texts.append(rn.get("category", "").lower())
                rest_combined = " ".join(rest_texts)

                for pref in food_prefs:
                    if pref.lower() not in rest_combined:
                        result["preference_violations"].append({
                            "preference": pref,
                            "type": "food",
                            "found_in_plan": False,
                            "reason": "no_match",
                            "detail": f"规划餐厅中未找到{pref}选项，已安排其他餐厅",
                        })

        # --- 活动类型偏好对比 ---
        if activity_pref:
            act_texts = []
            for an in activity_nodes:
                act_texts.append(an.get("name", "").lower())
                act_texts.extend(t.lower() for t in an.get("tags", []))
                act_texts.append(an.get("category", "").lower())
            act_combined = " ".join(act_texts)

            if activity_pref.lower() not in act_combined:
                result["preference_violations"].append({
                    "preference": activity_pref,
                    "type": "activity",
                    "found_in_plan": False,
                    "reason": "no_match",
                    "detail": f"规划活动中未找到{activity_pref}类型，已安排其他活动",
                })

        # --- 场地偏好对比 ---
        if venue_pref and venue_pref not in ("null", "None"):
            _venue_tag_map = {
                "outdoor": ("outdoor", "park", "nature", "open"),
                "indoor": ("indoor", "mall", "museum", "center"),
                "mall": ("mall", "shopping", "商场"),
            }
            expected_tags = _venue_tag_map.get(venue_pref, (venue_pref,))
            act_tags_all = []
            for an in activity_nodes:
                act_tags_all.extend(t.lower() for t in an.get("tags", []))
                act_tags_all.append(an.get("venue_environment", "").lower())

            if not any(et in " ".join(act_tags_all) for et in expected_tags):
                result["preference_violations"].append({
                    "preference": venue_pref,
                    "type": "venue",
                    "found_in_plan": False,
                    "reason": "no_match",
                    "detail": f"未找到{venue_pref}类型场地，已安排其他场所",
                })

    return result
