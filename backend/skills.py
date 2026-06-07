"""
LLM skill functions.
Supports Anthropic (claude-*) and DeepSeek (deepseek-chat / deepseek-reasoner).
DeepSeek-reasoner's reasoning_content is used directly as CoT display steps.
"""
import json
import logging
import os
import re
import tempfile
import time
from typing import Optional

from . import prompts

logger = logging.getLogger(__name__)


def _planning_cache_path(session_id: str | None) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(session_id or "default"))[:80]
    return os.path.join(tempfile.gettempdir(), "meituan_planner_cache", f"planning_poi_cache_{safe_id}.json")


def _planner_cache_file(session_id: str | None, name: str) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(session_id or "default"))[:80]
    return os.path.join(tempfile.gettempdir(), "meituan_planner_cache", f"{name}_{safe_id}.json")


def _write_json_cache(path: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.warning(f"[AgentPlan] failed to write cache {path}: {exc}")


def _read_json_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(f"[AgentPlan] failed to read cache {path}: {exc}")
        return {}


def write_current_itinerary_cache(session_id: str | None, nodes: list[dict]) -> None:
    _write_json_cache(
        _planner_cache_file(session_id, "current_itinerary"),
        {"session_id": session_id, "updated_at": time.time(), "nodes": nodes or []},
    )


def read_current_itinerary_cache(session_id: str | None) -> list[dict]:
    data = _read_json_cache(_planner_cache_file(session_id, "current_itinerary"))
    nodes = data.get("nodes", [])
    return nodes if isinstance(nodes, list) else []


def write_node_alternatives_cache(session_id: str | None, alternatives_by_node: dict[str, dict]) -> None:
    _write_json_cache(
        _planner_cache_file(session_id, "node_alternatives"),
        {
            "session_id": session_id,
            "updated_at": time.time(),
            "nodes": alternatives_by_node or {},
        },
    )


def read_node_alternatives_cache(session_id: str | None) -> dict:
    data = _read_json_cache(_planner_cache_file(session_id, "node_alternatives"))
    nodes = data.get("nodes", {})
    return nodes if isinstance(nodes, dict) else {}


def _write_planning_poi_cache(session_id: str | None, poi_data: dict[str, dict]) -> None:
    try:
        path = _planning_cache_path(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "session_id": session_id,
            "updated_at": time.time(),
            "items": list((poi_data or {}).values()),
        }
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.warning(f"[AgentPlan] failed to write POI cache: {exc}")


def read_planning_poi_cache(session_id: str | None) -> list[dict]:
    try:
        path = _planning_cache_path(session_id)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning(f"[AgentPlan] failed to read POI cache: {exc}")
        return []


def update_planning_poi_cache(session_id: str | None,
                              remove_poi_id: str | None = None,
                              add_item: dict | None = None) -> None:
    items = read_planning_poi_cache(session_id)
    remove_poi_id = remove_poi_id or ""
    add_poi_id = (add_item or {}).get("poi_id") or (add_item or {}).get("poiId") or ""
    next_items = []
    for item in items:
        pid = item.get("poi_id") or item.get("poiId")
        if pid and (pid == remove_poi_id or pid == add_poi_id):
            continue
        next_items.append(item)
    if add_item and add_poi_id:
        normalized = dict(add_item)
        normalized["poi_id"] = add_poi_id
        next_items.append(normalized)
    _write_planning_poi_cache(
        session_id,
        {
            (item.get("poi_id") or item.get("poiId") or f"item_{idx}"): item
            for idx, item in enumerate(next_items)
        },
    )


def _write_ranked_planning_poi_cache(session_id: str | None,
                                     state: dict,
                                     session_facts: dict,
                                     preferences: dict) -> None:
    sf_for_score = {
        **dict(session_facts or {}),
        "food_preferences": preferences.get("food") or session_facts.get("food_preferences") or [],
    }
    activities = sorted(
        [dict(item) for item in (state.get("seen_activities") or []) if item.get("open_status") != "closed"],
        key=lambda item: _score_activity(item, sf_for_score),
        reverse=True,
    )[:8]
    restaurants = sorted(
        [dict(item) for item in (state.get("seen_restaurants") or []) if item.get("open_status") != "closed"],
        key=lambda item: _score_restaurant(item, sf_for_score),
        reverse=True,
    )[:8]
    ranked: dict[str, dict] = {}
    for group, items in (("activity", activities), ("restaurant", restaurants)):
        for index, item in enumerate(items, 1):
            poi_id = item.get("poi_id") or item.get("poiId")
            if not poi_id:
                continue
            ranked[poi_id] = {
                **item,
                "poi_id": poi_id,
                "type": item.get("type") or group,
                "alternative_group": group,
                "alternative_rank": index,
            }
    _write_planning_poi_cache(session_id, ranked)



# ---- Explicit user preference extraction (rule fallback for every phase) ----
_FOOD_KEYWORDS = [
    "川菜", "日料", "日本料理", "寿司", "火锅", "粤菜", "西餐", "烤肉", "烧烤",
    "韩餐", "东南亚菜", "泰餐", "清真", "素食", "轻食", "甜品", "咖啡", "茶餐厅",
]

_ACTIVITY_KEYWORDS = [
    ("park", ["公园", "逛公园", "户外", "草坪", "自然", "露营"]),
    ("mall", ["商场", "购物中心", "逛街", "购物", "mall", "Mall"]),
    ("exhibition", ["展览", "美术馆", "博物馆", "科技馆", "画展"]),
    ("citywalk", ["citywalk", "Citywalk", "街区", "老街", "步行街"]),
    ("social", ["剧本杀", "密室", "桌游", "桌游吧", "社交场合", "社交活动", "互动", "一起玩", "聚会活动"]),
    ("playground", ["亲子乐园", "游乐园", "儿童乐园", "乐园"]),
]

_ACTIVITY_LABELS = {
    "park": "逛公园/户外",
    "mall": "逛商场/购物中心",
    "exhibition": "展览/博物馆/美术馆",
    "citywalk": "Citywalk/街区",
    "social": "剧本杀/密室/桌游",
    "playground": "亲子乐园/游乐园",
}

_ACTIVITY_TO_FRIENDS_TYPE = {
    "mall": "mall",
    "exhibition": "exhibition",
    "citywalk": "photo_spot",
    "social": "social",
}

_ACTIVITY_TO_VENUE = {
    "park": "outdoor",
    "mall": "mall",
    "exhibition": "indoor",
    "citywalk": "outdoor",
    "social": "indoor",
    "playground": "indoor",
}

_PARTICIPANT_LABELS = {
    "self": "我的偏好",
    "spouse": "配偶偏好",
    "child": "孩子偏好",
    "friends": "朋友偏好",
}

_OWNER_ALIASES = {
    "spouse": ["老婆", "妻子", "媳妇", "太太", "夫人", "女朋友", "女友", "对象", "伴侣", "另一半", "她"],
    "self": ["我", "自己", "本人", "我的"],
}

_SELF_MARKERS = ("我是男", "我是女", "我男", "我女", "本人男", "本人女", "我是个男", "我是个女")
_CHILD_MARKERS = ("孩子", "娃", "小朋友", "宝贝", "儿子", "女儿", "儿童")


def _append_unique(items: list, values: list) -> list:
    result = list(items or [])
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _parse_hhmm_minutes(value: str) -> Optional[int]:
    try:
        text = str(value).strip()
        day_offset = 0
        if text.startswith("次日"):
            day_offset = 24 * 60
            text = text.replace("次日", "", 1).strip()
        h, m = map(int, text.split(":"))
        return day_offset + h * 60 + m
    except Exception:
        return None


def _fmt_hhmm(total_minutes: int) -> str:
    total_minutes = max(0, int(total_minutes))
    day = total_minutes // (24 * 60)
    minute_of_day = total_minutes % (24 * 60)
    text = f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
    return f"次日 {text}" if day == 1 else (f"第{day + 1}天 {text}" if day > 1 else text)


def _add_minutes(total_minutes: int, delta: int) -> int:
    return max(0, int(total_minutes) + int(delta))


def _clamp_nodes_to_day(nodes: list[dict]) -> list[dict]:
    cleaned = []
    previous_end = -1
    for node in nodes or []:
        item = dict(node)
        start_raw = item.get("timeStart") or item.get("startTime")
        end_raw = item.get("timeEnd") or item.get("endTime")
        start_min = _parse_hhmm_minutes(start_raw)
        end_min = _parse_hhmm_minutes(end_raw)
        if start_min is not None:
            start_min = max(previous_end, start_min)
            item["timeStart"] = _fmt_hhmm(start_min)
        if end_min is not None:
            if start_min is not None:
                end_min = max(start_min, end_min)
            item["timeEnd"] = _fmt_hhmm(end_min)
            previous_end = max(previous_end, end_min)
        cleaned.append(item)
    return cleaned


_CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_cn_number(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CN_NUM_MAP:
        return _CN_NUM_MAP[text]
    if text.startswith("十") and len(text) == 2:
        tail = _CN_NUM_MAP.get(text[1])
        return 10 + tail if tail is not None else None
    if text.endswith("十") and len(text) == 2:
        head = _CN_NUM_MAP.get(text[0])
        return head * 10 if head is not None else None
    if "十" in text and len(text) == 3:
        head = _CN_NUM_MAP.get(text[0])
        tail = _CN_NUM_MAP.get(text[2])
        return head * 10 + tail if head is not None and tail is not None else None
    return None


def _format_start_time(minutes: int) -> str:
    minutes = max(0, int(minutes))
    day = minutes // (24 * 60)
    minute_of_day = minutes % (24 * 60)
    hhmm = f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
    return f"次日 {hhmm}" if day == 1 else hhmm


def _normalize_start_time_for_math(value: str, fallback: str = "14:00") -> int:
    parsed = _parse_hhmm_minutes(value)
    if parsed is not None:
        return parsed
    parsed = _parse_hhmm_minutes(fallback)
    return parsed if parsed is not None else 14 * 60


def _planned_end_for_search(planned_time: str, duration_min: int) -> str | None:
    try:
        return _format_start_time(
            _normalize_start_time_for_math(planned_time) + int(duration_min)
        )
    except Exception:
        return None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _safe_distance_text(value, default: float = 0.0) -> str:
    return f"{_safe_float(value, default):.1f}公里"


def parse_time_and_duration_updates(message: str,
                                    previous_facts: dict | None = None) -> dict:
    """Extract explicit monitoring-stage schedule edits from natural language."""
    text = message or ""
    previous_facts = previous_facts or {}
    updates: dict = {}

    time_pattern = re.compile(
        r"(上午|早上|下午|晚上|傍晚|中午|凌晨)?\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*[点时:：]\s*([0-9]{1,2})?"
    )
    match = time_pattern.search(text)
    relative_point_words = (
        "晚一点", "晚点", "早一点", "早点", "长一点", "长时间一点",
        "久一点", "短一点", "快一点",
    )
    if match:
        period, hour_raw, minute_raw = match.groups()
    explicit_time_context = bool(
        match
        and (
            period
            or str(hour_raw).isdigit()
            or any(w in text for w in ["出发", "开始", "改到", "改成", "改为", "换到", "时间到", "到", "定在", "约在", "几点"])
        )
        and not (
            not period
            and str(hour_raw) in ("一", "1")
            and any(w in text for w in relative_point_words)
        )
    )
    if match and explicit_time_context:
        hour = _parse_cn_number(hour_raw)
        if hour is not None:
            minute = int(minute_raw) if minute_raw else 0
            if period in ("下午", "晚上", "傍晚") and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            elif period == "凌晨" and hour == 12:
                hour = 0
            elif not period and 1 <= hour < 8:
                hour += 12
            updates["start_time"] = _format_start_time(hour * 60 + minute)

    current_start = _normalize_start_time_for_math(previous_facts.get("start_time"))
    if "start_time" not in updates:
        if any(w in text for w in ["晚一点", "晚点", "往后", "推迟", "延后"]):
            updates["start_time"] = _format_start_time(current_start + 60)
        elif any(w in text for w in ["早一点", "早点", "提前", "往前"]):
            updates["start_time"] = _format_start_time(current_start - 60)

    duration_match = re.search(
        r"([0-9]+|[一二两三四五六七八九十]{1,3})\s*(个)?\s*小时", text
    )
    if duration_match and any(w in text for w in ["时长", "时间", "玩", "逛", "改", "换", "长", "久"]):
        hours = _parse_cn_number(duration_match.group(1))
        if hours is not None:
            updates["duration_hours"] = max(1, min(12, hours))
    elif any(w in text for w in ["长时间", "久一点", "玩久", "多玩", "逛久", "时间长一点"]):
        current_duration = float(previous_facts.get("duration_hours") or 3)
        updates["duration_hours"] = max(1, min(12, current_duration + 1))
    elif any(w in text for w in ["短一点", "少玩", "快一点", "时间短一点"]):
        current_duration = float(previous_facts.get("duration_hours") or 3)
        updates["duration_hours"] = max(1, min(12, current_duration - 1))

    return updates


def _default_start_time_from_current(current_time: str) -> str:
    current_min = _normalize_start_time_for_math(current_time, "14:00")
    return _format_start_time(current_min)


def _detect_specific_preferences_core(message: str) -> dict:
    """Detect concrete food/activity preferences from user text."""
    skip_restaurant = any(w in message for w in [
        "吃过饭", "吃过了", "已经吃了", "刚吃完", "吃完饭了",
        "不吃", "不吃了", "不吃饭", "不想吃饭", "不用吃饭", "不需要吃饭", "不用餐", "不需要餐厅",
        "不去吃饭", "不外出吃饭", "不在外面吃饭", "不出去吃饭",
        "不去餐厅", "不要餐厅", "别去餐厅", "餐厅去掉", "去掉餐厅",
        "取消餐厅", "删除餐厅", "移除餐厅", "不安排餐厅",
        "不安排餐", "别安排餐", "不安排吃饭", "回家吃", "自己吃", "自己解决",
        "带饭了", "只玩", "只安排活动",
    ])
    wants_restaurant = any(w in message for w in [
        "反悔要去餐厅", "反悔去餐厅", "反悔吃饭", "还是去餐厅", "还是吃饭",
        "想去餐厅", "要去餐厅", "加个餐厅", "加一家餐厅", "安排餐厅",
        "安排吃饭", "想吃饭", "要吃饭", "去吃饭", "吃个饭", "外面吃",
    ]) and not skip_restaurant

    food = []
    for keyword in _FOOD_KEYWORDS:
        if keyword in message:
            food.append("日料" if keyword in ("日本料理", "寿司") else keyword)
    if any(w in message for w in ["减肥", "减脂", "低卡", "清淡", "健康", "低油"]):
        food = _append_unique(food, ["清淡", "低油"])

    activity = None
    negated_outdoor = any(w in message for w in ["不要户外", "别去户外", "不想户外", "不要室外", "别去室外"])
    for code, words in _ACTIVITY_KEYWORDS:
        if code == "park" and negated_outdoor:
            continue
        if any(word in message for word in words):
            activity = code
            break

    venue = _ACTIVITY_TO_VENUE.get(activity)
    if not venue:
        if any(w in message for w in ["室内", "空调", "不晒", "别晒", "避暑"]):
            venue = "indoor"
        elif any(w in message for w in ["户外", "室外"]):
            venue = "outdoor"

    return {
        "food": food,
        "activity_preference": activity,
        "activity_preference_label": _ACTIVITY_LABELS.get(activity),
        "venue": venue,
        "friends_activity_type": _ACTIVITY_TO_FRIENDS_TYPE.get(activity),
        "skip_restaurant": skip_restaurant,
        "wants_restaurant": wants_restaurant,
    }


def _compact_preference(detected: dict) -> dict:
    pref = {}
    for key in ("venue", "activity_preference", "activity_preference_label", "friends_activity_type"):
        if detected.get(key):
            pref[key] = detected[key]
    if detected.get("food"):
        pref["food"] = list(detected["food"])
    return pref


def _apply_preference_priority(facts: dict, requested_owner: str, hard_pref: dict) -> None:
    hard = dict(hard_pref or {})
    if hard:
        facts["hard_constraints"] = {
            "owner": requested_owner,
            "preference": hard,
        }
    participant_preferences = facts.get("participant_preferences") or {}
    soft = {
        owner: pref
        for owner, pref in participant_preferences.items()
        if owner != requested_owner and pref
    }
    if soft:
        facts["soft_preferences"] = soft


def detect_participant_preferences(message: str) -> dict:
    """Capture owner-specific preferences, e.g. spouse likes malls, user likes parks."""
    text = message or ""
    result = {}
    fragments = [p.strip() for p in re.split(r"[，,。；;\n]", text) if p.strip()]
    for fragment in fragments:
        owner = None
        # Spouse markers must win over a generic "我" in phrases like "我老婆".
        for alias in _OWNER_ALIASES["spouse"]:
            if alias in fragment:
                owner = "spouse"
                break
        if not owner:
            for alias in _OWNER_ALIASES["self"]:
                if alias in fragment:
                    owner = "self"
                    break
        if not owner:
            continue
        detected = _compact_preference(_detect_specific_preferences_core(fragment))
        if detected:
            result.setdefault(owner, {}).update(detected)
    return result


def _requested_preference_owner(message: str) -> Optional[str]:
    text = message or ""
    spouse_words = ("老婆", "妻子", "媳妇", "太太", "夫人", "女朋友", "女友", "对象", "伴侣", "另一半", "她")
    self_words = ("我", "我的", "自己", "本人")
    child_words = ("孩子", "小朋友", "娃", "宝贝", "儿童")
    friends_words = ("朋友", "哥们", "闺蜜", "同事", "同学")
    if any(re.search(rf"(按|按照|听|以|根据|优先).{{0,8}}{re.escape(word)}", text) for word in spouse_words):
        return "spouse"
    if any(re.search(rf"{re.escape(word)}.{{0,8}}(喜好|偏好|想法|为准|来)", text) for word in spouse_words):
        return "spouse"
    if "按她的" in text or "她喜欢的" in text:
        return "spouse"
    if any(re.search(rf"(按|按照|听|以|根据|优先).{{0,8}}{re.escape(word)}", text) for word in self_words):
        return "self"
    if any(re.search(rf"{re.escape(word)}.{{0,8}}(喜好|偏好|想法|为准|来)", text) for word in self_words):
        return "self"
    if any(re.search(rf"(按|按照|听|以|根据|优先).{{0,8}}{re.escape(word)}", text) for word in child_words):
        return "child"
    if any(re.search(rf"(以|按|按照).{{0,8}}{re.escape(word)}.{{0,6}}(为主|优先|为准)", text) for word in friends_words):
        return "friends"
    return None


def _has_owner_conflict(participant_preferences: dict) -> bool:
    if len(participant_preferences or {}) < 2:
        return False
    venues = {
        pref.get("venue")
        for pref in participant_preferences.values()
        if isinstance(pref, dict) and pref.get("venue")
    }
    activities = {
        pref.get("activity_preference")
        for pref in participant_preferences.values()
        if isinstance(pref, dict) and pref.get("activity_preference")
    }
    return len(venues) > 1 or len(activities) > 1


def detect_specific_preferences(message: str) -> dict:
    """Detect concrete preferences from user text, including owner-specific ones."""
    detected = _detect_specific_preferences_core(message or "")
    detected["participant_preferences"] = detect_participant_preferences(message or "")
    detected["requested_preference_owner"] = _requested_preference_owner(message or "")
    return detected


def merge_specific_preferences(session_facts: dict, preferences: dict, message: str) -> tuple[dict, dict]:
    """Merge explicit user demands into facts/preferences without dropping old values."""
    facts = dict(session_facts or {})
    prefs = dict(preferences or {})
    detected = detect_specific_preferences(message or "")
    text = message or ""
    child_mentioned = any(w in text for w in _CHILD_MARKERS)
    if child_mentioned or _child_context_present(facts, text):
        facts["has_children"] = True
        facts["scenario"] = "family"
        companions = list(facts.get("companions") or [])
        if "child" not in companions:
            companions.append("child")
        facts["companions"] = companions
        facts["child_confirmed_by_user"] = True
        child_age = _extract_child_age(text)
        if child_age is not None:
            facts["child_age"] = child_age
            facts["child_age_confirmed_by_user"] = True
        child_purpose = _infer_child_purpose(text)
        if child_purpose:
            facts["child_purpose"] = child_purpose
            facts["child_purpose_confirmed_by_user"] = True
    schedule_updates = parse_time_and_duration_updates(message or "", facts)
    if schedule_updates:
        facts.update(schedule_updates)
    participant_preferences = dict(facts.get("participant_preferences") or {})
    for owner, owner_pref in (detected.get("participant_preferences") or {}).items():
        participant_preferences[owner] = {
            **dict(participant_preferences.get(owner) or {}),
            **dict(owner_pref or {}),
        }
    if participant_preferences:
        facts["participant_preferences"] = participant_preferences

    requested_owner = detected.get("requested_preference_owner")
    if requested_owner and participant_preferences.get(requested_owner):
        owner_pref = participant_preferences[requested_owner]
        facts["preference_basis"] = requested_owner
        if owner_pref.get("food"):
            prefs["food"] = _append_unique(prefs.get("food", []), owner_pref["food"])
            facts["food_preferences"] = _append_unique(facts.get("food_preferences", []), owner_pref["food"])
        if owner_pref.get("venue"):
            prefs["venue"] = owner_pref["venue"]
            facts["venue_preference"] = owner_pref["venue"]
        if owner_pref.get("activity_preference"):
            facts["activity_preference"] = owner_pref["activity_preference"]
            facts["activity_preference_label"] = owner_pref.get("activity_preference_label")
            if owner_pref.get("friends_activity_type"):
                facts["friends_activity_type"] = owner_pref["friends_activity_type"]
            else:
                facts.pop("friends_activity_type", None)
        _apply_preference_priority(facts, requested_owner, owner_pref)
        facts.pop("preference_conflict", None)
    elif requested_owner:
        hard_pref = _compact_preference(detected)
        if hard_pref:
            facts["preference_basis"] = requested_owner
            if hard_pref.get("food"):
                prefs["food"] = _append_unique(prefs.get("food", []), hard_pref["food"])
                facts["food_preferences"] = _append_unique(facts.get("food_preferences", []), hard_pref["food"])
            if hard_pref.get("venue"):
                prefs["venue"] = hard_pref["venue"]
                facts["venue_preference"] = hard_pref["venue"]
            if hard_pref.get("activity_preference"):
                facts["activity_preference"] = hard_pref["activity_preference"]
                facts["activity_preference_label"] = hard_pref.get("activity_preference_label")
                if hard_pref.get("friends_activity_type"):
                    facts["friends_activity_type"] = hard_pref["friends_activity_type"]
            _apply_preference_priority(facts, requested_owner, hard_pref)
            facts.pop("preference_conflict", None)

    owner_conflict = _has_owner_conflict(detected.get("participant_preferences") or {})
    if owner_conflict and not requested_owner:
        facts["preference_conflict"] = "participant_preferences"
        prefs.pop("venue", None)
        facts.pop("venue_preference", None)
        facts.pop("activity_preference", None)
        facts.pop("activity_preference_label", None)
        facts.pop("friends_activity_type", None)

    if detected["food"]:
        replace_food = any(w in (message or "") for w in ["换成", "改成", "改为", "不要", "别吃"])
        prefs["food"] = list(detected["food"]) if replace_food else _append_unique(prefs.get("food", []), detected["food"])
        facts["food_preferences"] = list(detected["food"]) if replace_food else _append_unique(facts.get("food_preferences", []), detected["food"])

    if detected["venue"] and not owner_conflict and not requested_owner:
        prefs["venue"] = detected["venue"]
        facts["venue_preference"] = detected["venue"]

    if detected["activity_preference"] and not owner_conflict and not requested_owner:
        facts["activity_preference"] = detected["activity_preference"]
        facts["activity_preference_label"] = detected["activity_preference_label"]
        if detected["friends_activity_type"]:
            facts["friends_activity_type"] = detected["friends_activity_type"]

    if detected.get("skip_restaurant"):
        facts["skip_restaurant"] = True
        prefs["skip_restaurant"] = True
        prefs["food"] = []
        facts["food_preferences"] = []
    elif detected.get("wants_restaurant"):
        facts["skip_restaurant"] = False
        prefs["skip_restaurant"] = False

    return facts, prefs


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _merge_lists(base: list, incoming: list) -> list:
    return _append_unique(list(base or []), list(incoming or []))


def _explicit_negative_update(message: str) -> bool:
    return any(w in (message or "") for w in [
        "不是", "没有", "不用", "不需要", "不要", "别", "取消",
        "不想", "无所谓", "都可以", "不限",
    ])


def _self_gender_explicit(message: str) -> bool:
    return any(marker in (message or "") for marker in _SELF_MARKERS)


def _sanitize_identity_fields(facts: dict, user_message: str = "") -> dict:
    """Remove the user from companions/gender counts unless self gender is explicit."""
    clean = dict(facts or {})
    self_values = {"self", "user", "me", "我", "自己", "本人"}
    companions = clean.get("companions")
    if isinstance(companions, list):
        clean["companions"] = [c for c in companions if c not in self_values]
    else:
        clean["companions"] = []

    msg = user_message or ""
    if any(w in msg for w in _OWNER_ALIASES["spouse"]) and "spouse" not in clean["companions"]:
        clean["companions"].append("spouse")

    child_mentioned = any(w in msg for w in _CHILD_MARKERS)
    explicit_child_age = _extract_child_age(msg)
    explicit_child_purpose = _infer_child_purpose(msg)
    if child_mentioned:
        clean["child_confirmed_by_user"] = True
        clean["has_children"] = True
        clean["scenario"] = "family"
        if "child" not in clean["companions"]:
            clean["companions"].append("child")
        if explicit_child_age is not None:
            clean["child_age"] = explicit_child_age
            clean["child_age_confirmed_by_user"] = True
        elif clean.get("child_age") is not None and not clean.get("child_age_confirmed_by_user"):
            clean["child_age"] = None
        if explicit_child_purpose:
            clean["child_purpose"] = explicit_child_purpose
            clean["child_purpose_confirmed_by_user"] = True
        elif clean.get("child_purpose") and not clean.get("child_purpose_confirmed_by_user"):
            clean["child_purpose"] = None
    elif clean.get("has_children") and not clean.get("child_confirmed_by_user"):
        clean["has_children"] = False
        clean["child_age"] = None
        clean["child_purpose"] = None
        clean["companions"] = [c for c in clean["companions"] if c != "child"]

    desc = str(clean.get("companions_desc") or "").strip()
    if desc:
        for token in ("你+", "我+", "用户+", "本人+", "自己+", "你和", "我和", "用户和", "本人和", "自己和"):
            desc = desc.replace(token, "")
        if not child_mentioned and not clean.get("child_confirmed_by_user"):
            for token in ("+孩子", "孩子+", "和孩子", "带孩子", "孩子"):
                desc = desc.replace(token, "")
        desc = desc.strip("+、，, 和")
        clean["companions_desc"] = desc

    if _self_gender_explicit(msg):
        return clean

    female_other_markers = ("老婆", "妻子", "媳妇", "太太", "夫人", "女朋友", "女友", "妈妈", "母亲", "女儿", "姐妹", "女生", "女孩")
    male_other_markers = ("老公", "丈夫", "男朋友", "男友", "爸爸", "父亲", "儿子", "兄弟", "哥们", "男生", "男孩")
    has_female_other = any(w in msg for w in female_other_markers) or "spouse" in (clean.get("companions") or [])
    has_male_other = any(w in msg for w in male_other_markers)

    male_count = int(clean.get("male_count") or 0)
    female_count = int(clean.get("female_count") or 0)
    if has_female_other and female_count == 0:
        female_count = 1
    if male_count and not has_male_other:
        male_count = 0
    clean["male_count"] = male_count
    clean["female_count"] = female_count
    clean["group_gender"] = "unknown"
    return clean


def _child_context_present(facts: dict, message: str = "") -> bool:
    companions = facts.get("companions") or []
    return (
        bool(facts.get("has_children"))
        or "child" in companions
        or bool(facts.get("child_age"))
        or any(w in (message or "") for w in _CHILD_MARKERS)
    )


def _infer_child_purpose(text: str = "") -> str | None:
    if any(w in (text or "") for w in ("科普", "博物馆", "科技馆", "自然馆", "学习", "涨知识", "展览")):
        return "education"
    if any(w in (text or "") for w in ("纯粹", "好玩", "游玩", "乐园", "游乐", "玩就好", "开心")):
        return "fun"
    return None


def _extract_child_age(text: str = "") -> int | None:
    match = re.search(r"(\d{1,2})\s*(?:岁|周岁)", text or "")
    if not match:
        match = re.search(r"(?:孩子|娃|小朋友|宝贝|儿子|女儿).{0,6}?(\d{1,2})", text or "")
    if not match:
        return None
    try:
        age = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return age if 0 < age < 18 else None


def required_missing_fields(facts: dict, message: str = "") -> list[str]:
    missing = []
    if _child_context_present(facts or {}, message):
        if not _has_value((facts or {}).get("child_age")):
            missing.append("child_age")
        if not _has_value((facts or {}).get("child_purpose")):
            missing.append("child_purpose")
    return missing


def enforce_required_clarifications(result: dict, message: str = "") -> dict:
    """Apply non-prompt mandatory follow-up rules to clarify output."""
    fixed = dict(result or {})
    inferred = dict(fixed.get("inferred") or {})
    missing = list(fixed.get("missing_fields") or [])

    if _child_context_present(inferred, message):
        inferred["has_children"] = True
        inferred["scenario"] = "family"
        companions = list(inferred.get("companions") or [])
        if "child" not in companions:
            companions.append("child")
        inferred["companions"] = companions
        inferred["child_confirmed_by_user"] = True
        if not inferred.get("child_purpose"):
            inferred["child_purpose"] = _infer_child_purpose(message)

    for field in required_missing_fields(inferred, message):
        if field not in missing:
            missing.append(field)

    if "child_age" in missing or "child_purpose" in missing:
        question_parts = []
        if "child_age" in missing:
            question_parts.append("孩子大概几岁")
        if "child_purpose" in missing:
            question_parts.append("这次更偏科普学习（博物馆/科技馆）还是纯粹好玩")
        followup = "另外我需要确认：" + "？".join(question_parts) + "？"
        msg = str(fixed.get("confirm_message") or "").strip()
        if followup not in msg:
            fixed["confirm_message"] = f"{msg}\n{followup}" if msg else followup
        fixed["confidence"] = "medium"

    fixed["inferred"] = inferred
    fixed["missing_fields"] = missing
    return fixed


def merge_confirmed_state(previous_facts: dict,
                          previous_preferences: dict,
                          new_facts: dict,
                          new_preferences: dict,
                          user_message: str = "") -> tuple[dict, dict]:
    """
    Merge clarification/confirmation output without erasing explicit demands.

    The LLM often returns a complete schema with default false/null/empty values.
    During confirmation, those defaults must not wipe user-stated needs that were
    already captured from earlier turns.
    """
    facts = dict(previous_facts or {})
    prefs = dict(previous_preferences or {})
    incoming_facts = dict(new_facts or {})

    explicit_child_age = _extract_child_age(user_message or "")
    explicit_child_purpose = _infer_child_purpose(user_message or "")
    if explicit_child_age is None:
        incoming_facts.pop("child_age", None)
        incoming_facts.pop("child_age_confirmed_by_user", None)
    if explicit_child_purpose is None:
        incoming_facts.pop("child_purpose", None)
        incoming_facts.pop("child_purpose_confirmed_by_user", None)

    for key, value in incoming_facts.items():
        if isinstance(value, list):
            facts[key] = _merge_lists(facts.get(key, []), value)
        elif isinstance(value, bool) and facts.get(key) is True and value is False and not _explicit_negative_update(user_message):
            continue
        elif _has_value(value):
            facts[key] = value

    for key, value in (new_preferences or {}).items():
        if isinstance(value, list):
            prefs[key] = _merge_lists(prefs.get(key, []), value)
        elif isinstance(value, bool) and prefs.get(key) is True and value is False and not _explicit_negative_update(user_message):
            continue
        elif _has_value(value):
            prefs[key] = value

    # Keep the two representations in sync for planner + profile panel.
    if facts.get("food_preferences"):
        prefs["food"] = _merge_lists(prefs.get("food", []), facts.get("food_preferences", []))
    if prefs.get("food"):
        facts["food_preferences"] = _merge_lists(facts.get("food_preferences", []), prefs.get("food", []))

    if facts.get("venue_preference") and not prefs.get("venue"):
        prefs["venue"] = facts.get("venue_preference")
    if prefs.get("venue") and not facts.get("venue_preference"):
        facts["venue_preference"] = prefs.get("venue")

    facts, prefs = merge_specific_preferences(facts, prefs, user_message or "")
    facts = _sanitize_identity_fields(facts, user_message or "")
    if not _has_value(facts.get("start_time")):
        facts["start_time"] = _default_start_time_from_current(time.strftime("%H:%M"))
    if not _has_value(facts.get("duration_hours")):
        facts["duration_hours"] = 3
    return facts, prefs


def build_confirmed_requirements(facts: dict, preferences: dict) -> list[str]:
    """Human-readable confirmed requirement lines for every clarify output."""
    f = facts or {}
    p = preferences or {}
    lines = []

    scenario_map = {
        "family": "家庭出行",
        "friends": "朋友出行",
        "couple": "情侣出行",
        "solo": "独自出行",
    }
    if f.get("scenario"):
        lines.append(f"出行场景：{scenario_map.get(f['scenario'], f['scenario'])}")
    if f.get("start_time"):
        lines.append(f"出发时间：{f['start_time']}")
    if f.get("duration_hours"):
        lines.append(f"游玩时长：约{f['duration_hours']}小时")
    if f.get("home_area") or f.get("detected_location"):
        lines.append(f"出发地点：{f.get('home_area') or f.get('detected_location')}")

    companions = [c for c in (f.get("companions") or []) if c not in ("self", "user", "me", "我", "自己", "本人")]
    if companions:
        label_map = {
            "spouse": "配偶", "child": "孩子", "parents": "父母",
            "friends": "朋友", "elderly": "老人", "partner": "另一半",
            "family": "家人",
        }
        lines.append("同行人：" + "、".join(label_map.get(c, c) for c in companions))
    elif f.get("companions_desc"):
        lines.append(f"同行人：{f['companions_desc']}")

    if f.get("child_age"):
        lines.append(f"孩子年龄：{f['child_age']}岁")
    if f.get("child_purpose"):
        cp = {"education": "科普学习", "fun": "轻松好玩"}.get(f["child_purpose"], f["child_purpose"])
        lines.append(f"孩子偏好：{cp}")
    male_count = f.get("male_count", 0)
    female_count = f.get("female_count", 0)
    if male_count or female_count:
        scenario = f.get("scenario", "")
        label = "朋友构成" if scenario == "friends" else "同行性别"
        lines.append(f"{label}：男{male_count}人、女{female_count}人")
    if f.get("friends_activity_type"):
        fat = {
            "social": "社交互动", "exhibition": "文化展览",
            "mall": "逛街购物", "photo_spot": "出片打卡", "mixed": "综合体验",
        }.get(f["friends_activity_type"], f["friends_activity_type"])
        lines.append(f"活动偏好：{fat}")

    participant_preferences = f.get("participant_preferences") or {}
    for owner in ("spouse", "self"):
        owner_pref = participant_preferences.get(owner) or {}
        label = _PARTICIPANT_LABELS.get(owner, owner)
        details = []
        if owner_pref.get("activity_preference_label"):
            details.append(owner_pref["activity_preference_label"])
        elif owner_pref.get("venue"):
            details.append({"mall": "商场/购物中心", "indoor": "室内优先", "outdoor": "户外/公园"}.get(owner_pref["venue"], owner_pref["venue"]))
        if owner_pref.get("food"):
            details.append("饮食：" + "、".join(owner_pref["food"]))
        if details:
            lines.append(f"{label}：" + "；".join(details))
    if f.get("preference_basis"):
        basis_label = _PARTICIPANT_LABELS.get(f["preference_basis"], f["preference_basis"])
        lines.append(f"当前取向：按{basis_label}")
    if f.get("hard_constraints"):
        owner = f["hard_constraints"].get("owner")
        owner_label = _PARTICIPANT_LABELS.get(owner, owner or "用户")
        lines.append(f"硬约束：优先满足{owner_label}")

    food = p.get("food") or f.get("food_preferences") or []
    if food:
        lines.append("饮食偏好：" + "、".join(food))
    venue = p.get("venue") or f.get("venue_preference")
    if venue:
        venue_label = {"mall": "商场/购物中心", "indoor": "室内优先", "outdoor": "户外/公园"}.get(venue, venue)
        lines.append(f"场地偏好：{venue_label}")
    if f.get("activity_preference_label"):
        lines.append(f"具体活动：{f['activity_preference_label']}")
    if f.get("special_needs"):
        lines.append("特殊需求：" + "、".join(f["special_needs"]))
    if p.get("skip_restaurant") or f.get("skip_restaurant"):
        lines.append("餐食安排：不安排外出餐厅")

    return lines


def append_confirmed_requirements(message: str, facts: dict, preferences: dict) -> str:
    lines = build_confirmed_requirements(facts, preferences)
    if not lines:
        return message
    block = "已确定的需求：\n" + "\n".join(f"- {line}" for line in lines)
    return f"{message}\n\n{block}"


def enforce_planning_tool_args(name: str,
                               args: dict,
                               session_facts: dict,
                               preferences: dict) -> dict:
    """Force confirmed user demands into search tool parameters."""
    patched = dict(args or {})
    if name == "search_restaurants":
        start_time = session_facts.get("start_time") or "14:00"
        if not patched.get("planned_time"):
            patched["planned_time"] = start_time
        if not patched.get("planned_end_time"):
            planned_end = _planned_end_for_search(patched["planned_time"], 60)
            if planned_end:
                patched["planned_end_time"] = planned_end
        food = preferences.get("food") or session_facts.get("food_preferences") or []
        if food:
            existing = [p.strip() for p in str(patched.get("preferences", "")).split(",") if p.strip()]
            patched["preferences"] = ",".join(_append_unique(existing, list(food)))

    if name == "search_activities":
        start_time = session_facts.get("start_time") or "14:00"
        if not patched.get("planned_time"):
            patched["planned_time"] = start_time
        if not patched.get("planned_end_time"):
            planned_end = _planned_end_for_search(patched["planned_time"], 90)
            if planned_end:
                patched["planned_end_time"] = planned_end
        venue = preferences.get("venue") or session_facts.get("venue_preference")
        activity_pref = session_facts.get("activity_preference")
        categories = [c.strip() for c in str(patched.get("categories", "")).split(",") if c.strip()]
        category_map = {
            "mall": ["mall_exhibition"],
            "indoor": ["indoor_playground", "mall_exhibition", "museum", "exhibition", "board_game"],
            "outdoor": ["outdoor_park", "citywalk"],
            "park": ["outdoor_park"],
            "exhibition": ["museum", "exhibition"],
            "citywalk": ["citywalk"],
            "social": ["board_game"],
            "playground": ["indoor_playground"],
        }
        forced = []
        if activity_pref in category_map:
            forced.extend(category_map[activity_pref])
        elif venue in category_map:
            forced.extend(category_map[venue])
        if forced:
            patched["categories"] = ",".join(_append_unique(categories, forced))

    return patched

# ── Provider detection ───────────────────────────────────────────────

def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "anthropic").lower()


def _has_api_key() -> bool:
    # Any one key available = LLM usable
    return bool(
        os.getenv("LONGCAT_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )


def _openai_clients_priority() -> list:
    """Return (client, model_alias) pairs in priority order for OpenAI-compat APIs."""
    clients = []
    if os.getenv("LONGCAT_API_KEY"):
        clients.append(("longcat", _get_longcat()))
    if os.getenv("DEEPSEEK_API_KEY"):
        clients.append(("deepseek", _get_deepseek()))
    return clients


# ── Client singletons ────────────────────────────────────────────────

_anthropic_client = None
_deepseek_client  = None
_longcat_client   = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic as _ant
        _anthropic_client = _ant.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_deepseek():
    global _deepseek_client
    if _deepseek_client is None and os.getenv("DEEPSEEK_API_KEY"):
        from openai import AsyncOpenAI
        _deepseek_client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
    return _deepseek_client


def _get_longcat():
    global _longcat_client
    if _longcat_client is None and os.getenv("LONGCAT_API_KEY"):
        from openai import AsyncOpenAI
        _longcat_client = AsyncOpenAI(
            api_key=os.getenv("LONGCAT_API_KEY"),
            base_url=os.getenv("LONGCAT_BASE_URL", "https://api.longcat.chat/openai"),
        )
    return _longcat_client


# ── Model mapping ────────────────────────────────────────────────────

_MODEL_MAP = {
    "fast_model": {
        "anthropic": "claude-haiku-4-5-20251001",
        "deepseek":  "deepseek-chat",
        "longcat":   "LongCat-2.0-Preview",
    },
    "main_model": {
        "anthropic": "claude-sonnet-4-6",
        "deepseek":  "deepseek-reasoner",
        "longcat":   "LongCat-2.0-Preview",
    },
}


def _resolve_model(alias: str) -> str:
    return _MODEL_MAP.get(alias, {}).get(_provider(), alias)


def _deepseek_model(alias: str) -> str:
    return _MODEL_MAP.get(alias, {}).get("deepseek", alias)


def _is_quota_error(e: Exception) -> bool:
    try:
        from openai import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    return getattr(e, "status_code", None) == 429


# ── Core async LLM call → (thinking, answer) ────────────────────────

async def _call_llm(system: str, user: str, model_alias: str = "fast_model",
                    max_tokens: int = 4096,
                    require_json: bool = False) -> tuple[str, str]:
    """
    require_json=True: pass response_format={"type":"json_object"} to DeepSeek
    so the model is contractually obligated to return valid JSON.
    """
    model = _resolve_model(model_alias)
    p = _provider()

    if p in ("deepseek", "longcat"):
        client = _get_deepseek() if p == "deepseek" else _get_longcat()
        if not client:
            raise RuntimeError(f"{p.upper()}_API_KEY not set")
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        if require_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as e:
            if p == "longcat" and _is_quota_error(e) and _get_deepseek():
                logger.warning(f"LongCat quota error, falling back to DeepSeek: {e}")
                fb_kwargs = {**kwargs, "model": _deepseek_model(model_alias)}
                resp = await _get_deepseek().chat.completions.create(**fb_kwargs)
            else:
                raise
        msg = resp.choices[0].message
        thinking = getattr(msg, "reasoning_content", None) or ""
        answer   = msg.content or ""
        return thinking, answer

    client = _get_anthropic()
    if not client:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "", resp.content[0].text


# ── JSON parsing ─────────────────────────────────────────────────────

def _fix_single_quotes(text: str) -> str:
    result, in_double, in_single, escape = [], False, False, False
    for ch in text:
        if escape:
            result.append(ch); escape = False; continue
        if ch == '\\':
            result.append(ch); escape = True; continue
        if ch == '"' and not in_single:
            in_double = not in_double; result.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single; result.append('"')
        else:
            result.append(ch)
    return ''.join(result)


def _fix_and_parse(text: str) -> dict | None:
    fixed = re.sub(r'```\w*', '', text)
    fixed = re.sub(r'^#+\s*', '', fixed, flags=re.MULTILINE)
    fixed = re.sub(r'^[^{]*', '', fixed)
    if '}' in fixed:
        fixed = fixed[:fixed.rindex('}') + 1]
    fixed = _fix_single_quotes(fixed)
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*\]', ']', fixed)
    fixed = re.sub(r'(?<!")(\b[a-zA-Z_][a-zA-Z0-9_]*)(?=\s*:)', r'"\1"', fixed)
    fixed = fixed.replace("True", "true").replace("False", "false").replace("None", "null")
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _complete_truncated(text: str) -> dict | None:
    text = text.rstrip().rstrip(',')
    if text.count('"') % 2 != 0:
        text += '"'
    closers = []
    for ch in text:
        if ch == '{':
            closers.append('}')
        elif ch == '[':
            closers.append(']')
        elif ch == '}' and closers and closers[-1] == '}':
            closers.pop()
        elif ch == ']' and closers and closers[-1] == ']':
            closers.pop()
    text += ''.join(reversed(closers))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_json(text: str) -> dict:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    result = _fix_and_parse(t)
    if result is not None:
        return result
    result = _complete_truncated(t)
    if result is not None:
        return result
    raise ValueError(f"Cannot parse JSON: {t[:300]}")


def _build_field_error_msg(answer: str, schema_hint: str) -> str:
    """生成字段级错误信息，用于重试时精准告知 LLM 哪里出错。"""
    errors = []
    parsed = None
    try:
        parsed = json.loads(answer.strip())
    except json.JSONDecodeError as e:
        errors.append(f"JSON 语法错误：第{e.lineno}行第{e.colno}列，附近：{answer[max(0,e.pos-15):e.pos+15]!r}")

    if parsed is not None and schema_hint:
        try:
            schema_obj = json.loads(schema_hint)

            def check_keys(actual, expected, path=""):
                if isinstance(expected, dict):
                    if not isinstance(actual, dict):
                        errors.append(f"{path or 'root'} 应为对象，实际为 {type(actual).__name__}")
                        return
                    for k in expected:
                        if k not in actual:
                            errors.append(f"缺少字段: {(path + '.' + k).lstrip('.')}")
                        else:
                            check_keys(actual[k], expected[k], (path + '.' + k).lstrip('.'))
                elif isinstance(expected, list) and expected and isinstance(actual, list) and actual:
                    check_keys(actual[0], expected[0], f"{path}[0]")

            check_keys(parsed, schema_obj)
        except (json.JSONDecodeError, TypeError):
            pass

    return "；".join(errors[:5]) if errors else "结构与预期 Schema 不符"


async def _call_json_with_retry(system: str, user: str, model_alias: str,
                                schema_hint: str = "", max_tokens: int = 4096,
                                max_retries: int = 3) -> tuple[str, dict]:
    last_answer = ""
    last_error  = ""
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                thinking, answer = await _call_llm(system, user, model_alias, max_tokens)
            else:
                field_errors = _build_field_error_msg(last_answer, schema_hint)
                repair_user = prompts.REPAIR_USER.format(
                    original=last_answer, error=last_error,
                    schema=schema_hint, field_errors=field_errors
                )
                thinking, answer = await _call_llm(
                    prompts.REPAIR_SYSTEM, repair_user, "fast_model", 4096
                )
            last_answer = answer
            return thinking, _parse_json(answer)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
    raise ValueError(f"All {max_retries} attempts failed. Last: {last_answer[:200]}")


# ── LLM Tool Calling (Function Calling) ──────────────────────────────

async def _call_llm_with_tools(messages: list, tools: list,
                                model_alias: str = "fast_model",
                                max_tokens: int = 4096) -> dict:
    """
    Call LLM with tool definitions. Returns unified result:
    {
        "finish_reason": "tool_calls" | "stop",
        "content": str,
        "tool_calls": [{"id": str, "name": str, "args": dict}],
        "raw_assistant_msg": dict,   # append this to messages for history
    }
    """
    p = _provider()

    if p in ("deepseek", "longcat"):
        client = _get_deepseek() if p == "deepseek" else _get_longcat()
        if not client:
            raise RuntimeError(f"{p.upper()}_API_KEY not set")

        call_kwargs = dict(
            model=_resolve_model(model_alias),
            messages=messages,
            tools=tools,
            tool_choice="required",   # force tool use; finish_planning serves as the exit
            max_tokens=max_tokens,
        )
        try:
            resp = await client.chat.completions.create(**call_kwargs)
        except Exception as e:
            if p == "longcat" and _is_quota_error(e) and _get_deepseek():
                logger.warning(f"LongCat quota error (tools), falling back to DeepSeek: {e}")
                fb_kwargs = {**call_kwargs, "model": _deepseek_model(model_alias)}
                resp = await _get_deepseek().chat.completions.create(**fb_kwargs)
            else:
                raise
        msg = resp.choices[0].message
        finish_reason = resp.choices[0].finish_reason or "stop"

        tool_calls = []
        raw_tcs = []
        for tc in (msg.tool_calls or []):
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "args": json.loads(tc.function.arguments or "{}"),
            })
            raw_tcs.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            })

        raw_assistant = {"role": "assistant", "content": msg.content or ""}
        if raw_tcs:
            raw_assistant["tool_calls"] = raw_tcs

        return {
            "finish_reason": "tool_calls" if tool_calls else finish_reason,
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "raw_assistant_msg": raw_assistant,
        }

    # Anthropic
    client = _get_anthropic()
    if not client:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    from . import agent_tools as _at
    ant_tools = _at.to_anthropic_tools(tools)

    resp = await client.messages.create(
        model=_resolve_model(model_alias),
        messages=messages,
        tools=ant_tools,
        tool_choice={"type": "any"},  # "any" forces tool use in Anthropic API
        max_tokens=max_tokens,
    )

    content_text = ""
    tool_calls = []
    raw_content = []
    for block in resp.content:
        if block.type == "text":
            content_text += block.text
            raw_content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "args": block.input,
            })
            raw_content.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })

    return {
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "content": content_text,
        "tool_calls": tool_calls,
        "raw_assistant_msg": {"role": "assistant", "content": raw_content},
    }


def _make_tool_result_messages(tool_calls: list, results: list,
                                provider: str) -> list[dict]:
    """Build the tool result messages to append to conversation history."""
    if provider in ("deepseek", "longcat"):
        return [
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(res, ensure_ascii=False, default=str),
            }
            for tc, res in zip(tool_calls, results)
        ]

    # Anthropic: tool results go in a single user message
    content = [
        {
            "type": "tool_result",
            "tool_use_id": tc["id"],
            "content": json.dumps(res, ensure_ascii=False, default=str),
        }
        for tc, res in zip(tool_calls, results)
    ]
    return [{"role": "user", "content": content}]


# ── Agent Planning Loop ───────────────────────────────────────────────

async def run_agent_plan(session_facts: dict, preferences: dict):
    """
    Tool-use agent loop for itinerary planning.

    Async generator that yields progress events and one final result:
      {"_type": "cot_step",  "text": str}        — tool call progress
      {"_type": "status",    "id": int, ...}      — status bar updates
      {"_type": "result",    "plan": dict}        — final itinerary (last item)

    The LLM autonomously decides which tools to call and in what order.
    Planning ends when the LLM calls `finish_planning` with the itinerary.
    Falls back to non-tool plan_itinerary() if no API key or tool loop fails.
    """
    import time as _time
    from . import agent_tools as _at

    if not _has_api_key():
        # Fallback: old non-tool approach
        activities = []
        restaurants = []
        plan = _fallback_plan(
            activities,
            restaurants,
            {"session_facts": session_facts, "preferences": preferences},
            "full_managed",
        )
        yield {"_type": "result", "plan": plan}
        return

    scenario = session_facts.get("scenario", "family")
    start_time = session_facts.get("start_time") or _default_start_time_from_current(time.strftime("%H:%M"))
    duration_hours = _safe_float(session_facts.get("duration_hours"), 3.0)

    # Derive end_time from start + duration if not explicitly stored
    raw_end = session_facts.get("end_time")
    if raw_end:
        end_time = raw_end
    else:
        try:
            total_min = _normalize_start_time_for_math(start_time) + int(duration_hours * 60)
            end_time = _format_start_time(total_min)
        except Exception:
            end_time = "20:00"

    food_prefs  = preferences.get("food", [])
    venue_pref  = preferences.get("venue") or ""
    skip_restaurant = bool(preferences.get("skip_restaurant") or session_facts.get("skip_restaurant"))
    if skip_restaurant:
        food_prefs = []
    has_child   = session_facts.get("has_children", session_facts.get("has_child", False))
    has_elderly = session_facts.get("has_elderly", False)
    child_age   = session_facts.get("child_age")
    # Fix: field is special_needs (list), not special_requirements (str)
    sn_raw = session_facts.get("special_needs", [])
    if isinstance(sn_raw, list):
        special = "、".join(sn_raw) if sn_raw else "无"
    else:
        special = str(sn_raw) if sn_raw else "无"
    current_time = _time.strftime("%H:%M")

    # Build group description
    group_parts = []
    if has_child:
        age_str = f"{child_age}岁" if child_age else "未知年龄"
        group_parts.append(f"有孩子({age_str})")
    if has_elderly:
        group_parts.append("有老人")
    male_count   = session_facts.get("male_count", 0)
    female_count = session_facts.get("female_count", 0)
    if male_count or female_count:
        group_parts.append(f"男{male_count}人女{female_count}人")
    if not group_parts:
        group_desc = {"family": "家庭", "friends": "朋友", "couple": "情侣",
                      "elderly": "老人为主", "solo": "单人"}.get(scenario, scenario)
    else:
        group_desc = "、".join(group_parts)

    # Build person_constraints block
    constraints = []

    # ── Legal / age limits (highest priority) ────────────────────────
    all_adults = session_facts.get("all_adults_confirmed")
    has_minor  = bool(child_age and int(child_age) < 18)
    if has_minor or all_adults is not True:
        constraints.append("⚖️【法律最高优先级】未成年人禁酒：绝对禁止安排酒吧、夜店、清吧、任何以饮酒为主题的场所")
    if child_age:
        ca = int(child_age)
        if 5 <= ca < 14:
            constraints.append(f"⚖️【最高优先级】孩子{ca}岁(5~13)：可安排非恐密室/非恐剧本杀（亲子型/推理型/轻松型）；恐怖/惊悚主题绝对禁止")
        elif ca < 5:
            constraints.append(f"⚖️【最高优先级】孩子{ca}岁(<5)：禁止密室逃脱、剧本杀")

    # ── Children ─────────────────────────────────────────────────────
    if has_child:
        constraints.append("有儿童：活动必须亲子友好，末节点结束≤20:00，步行≤500m/段")
        cp = session_facts.get("child_purpose")
        if cp == "education":
            constraints.append("孩子出行目的=科普学习：优先科技馆、博物馆、自然博物馆等教育属性强场所")
        elif cp == "fun":
            constraints.append("孩子出行目的=轻松好玩：优先游乐园、亲子乐园、游戏体验类场所")

    # ── Elderly ───────────────────────────────────────────────────────
    if has_elderly:
        constraints.append("有老人：全程步行≤800m，避免久站/高强度体力活动")
        if session_facts.get("elderly_no_walking"):
            constraints.append("老人不宜步行：节点间必须可打车直达，不选需大量步行的景区")

    # ── Female preferences ────────────────────────────────────────────
    female_count_val = session_facts.get("female_count", 0)
    if female_count_val:
        if session_facts.get("female_weight_loss"):
            constraints.append(f"{female_count_val}位女生需减脂/健康餐：餐厅必须有低卡/轻食/沙拉选项")
        if session_facts.get("female_prefer_low_intensity"):
            constraints.append("女生偏好低体力：不安排爬山、徒步、高强度运动")
        if session_facts.get("female_prefer_indoor"):
            constraints.append("女生倾向室内：优先室内场所")

    # ── Male preferences ──────────────────────────────────────────────
    gg = session_facts.get("group_gender", "")
    if gg == "all_male" and session_facts.get("male_prefer_high_intensity"):
        constraints.append("全男生组且偏好高强度：优先户外运动、爬山、体育场馆")

    # ── Friends activity type ─────────────────────────────────────────
    fat = session_facts.get("friends_activity_type")
    fat_map = {
        "social":     "社交互动（剧本杀/密室/桌游）",
        "exhibition": "文化展览（博物馆/美术馆）",
        "mall":       "逛街购物（商场/步行街）",
        "photo_spot": "出片打卡（网红/文艺街区）",
        "mixed":      "综合体验",
    }
    if fat and fat in fat_map:
        constraints.append(f"朋友活动偏好={fat_map[fat]}：优先匹配此类场所（但仍受法律约束）")

    hard_constraints = session_facts.get("hard_constraints") or {}
    if hard_constraints.get("preference"):
        owner = hard_constraints.get("owner", "user")
        owner_label = _PARTICIPANT_LABELS.get(owner, owner)
        constraints.append(
            f"【用户指定优先级】当前必须按{owner_label}执行："
            f"{json.dumps(hard_constraints['preference'], ensure_ascii=False)}"
        )
    soft_preferences = session_facts.get("soft_preferences") or {}
    if soft_preferences:
        constraints.append(
            "其他人的偏好仅作为 soft preference 加分，不得覆盖上述用户指定优先级："
            f"{json.dumps(soft_preferences, ensure_ascii=False)}"
        )

    if not constraints:
        constraints.append("无特殊人员约束")

    # ── Pinned-node time exclusions ───────────────────────────────────
    pinned_nodes = session_facts.get("_pinned_nodes", [])
    if pinned_nodes:
        constraints.append("")
        constraints.append("【已锁定节点 — 以下时间段禁止安排任何新节点】")
        for pn in pinned_nodes:
            constraints.append(
                f"  • 🔒 {pn.get('name', '?')}  {pn.get('timeStart','')}–{pn.get('timeEnd','')}  "
                f"（该节点由用户锁定，新行程中不得出现与此时间段重叠的节点）"
            )

    person_constraints = "\n".join(constraints)

    # Build user demand instructions block
    demand_lines = []
    if skip_restaurant:
        demand_lines.append(
            "- 用户已说明吃过饭/不需要用餐：本轮规划必须跳过 search_restaurants 和 get_queue_status，"
            "finish_planning 的 nodes 中绝对不能出现 type=restaurant 的节点"
        )
    elif food_prefs:
        food_str = "、".join(food_prefs)
        demand_lines.append(
            f"- 用户要求吃「{food_str}」：调用 search_restaurants 时 preferences 参数必须填「{food_str}」，"
            f"选餐厅时必须优先满足此口味，找不到时才退而求其次"
        )
    activity_pref = session_facts.get("activity_preference")
    activity_label = session_facts.get("activity_preference_label") or _ACTIVITY_LABELS.get(activity_pref, "")
    if activity_pref:
        demand_lines.append(
            f"- 用户活动偏好「{activity_label}」：活动节点必须优先匹配此类型；只有没有可用候选或违反安全约束时才降级"
        )

    if venue_pref == "mall":
        demand_lines.append(
            "- 用户要求去商场：调用 search_activities 时 categories 参数填「mall」"
            "（系统会返回商场/购物中心类活动），选活动时必须优先选商场/室内购物中心内的场所"
        )
    elif venue_pref == "indoor":
        demand_lines.append(
            "- 用户偏好室内：调用 search_activities 时 categories 参数填「indoor」"
            "（系统会返回室内类活动），优先选 venue.environment=indoor 的活动"
        )
    elif venue_pref == "outdoor":
        demand_lines.append("- 用户偏好户外：优先选户外公园、开放景区等活动")

    fat = session_facts.get("friends_activity_type")
    fat_label = {
        "social": "剧本杀/密室/桌游", "exhibition": "博物馆/美术馆/展览",
        "mall": "逛商场/购物街", "photo_spot": "打卡出片地", "mixed": "混搭综合",
    }.get(fat, "")
    if fat_label:
        demand_lines.append(
            f"- 用户朋友活动偏好「{fat_label}」：活动节点必须优先选此类场所，只有在没有合适选项时才选其他类型"
        )

    if not demand_lines:
        demand_lines.append("- 无额外具体需求，按综合评分选择最优方案")

    user_demand_instructions = "\n".join(demand_lines)

    user_msg = prompts.AGENT_PLAN_USER.format(
        scenario=scenario,
        start_time=start_time,
        end_time=end_time,
        group_desc=group_desc,
        food_prefs=("不安排餐食" if skip_restaurant else ("、".join(food_prefs) if food_prefs else "不限")),
        venue_pref=venue_pref if venue_pref else "不限",
        special_requirements=special,
        user_demand_instructions=user_demand_instructions,
        current_time=current_time,
        person_constraints=person_constraints,
    )
    if skip_restaurant:
        user_msg += (
            "\n\n## 餐食跳过规则（最高优先级）\n"
            "- 用户已经吃过饭或不需要餐食，本次只安排活动/轻活动/交通\n"
            "- 禁止调用 search_restaurants、get_queue_status\n"
            "- 禁止输出 type=restaurant 的节点\n"
            "- 节点数量可以为 2-4 个活动节点，不因缺少餐厅而视为不合格"
        )

    system_prompt = prompts.AGENT_PLAN_SYSTEM
    planning_tools = _at.PLANNING_TOOLS
    if skip_restaurant:
        system_prompt = prompts.AGENT_PLAN_SYSTEM + """

## 动态工具约束：用户不需要餐食
- 跳过 search_restaurants；该工具本轮不可用
- 跳过 get_queue_status；没有餐厅节点无需查排队
- finish_planning 的前提改为：已调用 get_weather 和 search_activities，且必要时调用 get_booking_status
- 行程节点数允许 2-4 个，全部为 activity/light，严禁 restaurant
"""
        planning_tools = [
            json.loads(json.dumps(tool, ensure_ascii=False))
            for tool in _at.PLANNING_TOOLS
            if tool.get("function", {}).get("name") not in {"search_restaurants", "get_queue_status"}
        ]
        for tool in planning_tools:
            fn = tool.get("function", {})
            if fn.get("name") == "finish_planning":
                fn["description"] = (
                    "收集足够数据后调用此工具提交最终行程方案。"
                    "【前提】用户不需要餐食，本轮只需已调用 get_weather 和 search_activities，"
                    "必要时调用 get_booking_status。"
                    "【约束】nodes 中每个 poiId 必须来自工具返回的真实数据，严禁编造；"
                    "严禁 restaurant 节点。"
                )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_msg},
    ]

    seen_poi_ids:  set[str]        = set()
    seen_poi_data: dict[str, dict] = {}
    seen_poi_ids.add("walk_001")  # always allow walk nodes
    session_id = session_facts.get("_session_id") or session_facts.get("session_id")
    _write_planning_poi_cache(session_id, seen_poi_data)
    planning_state = {
        "user_context": {"session_facts": session_facts, "preferences": preferences},
        "weather": None,
        "seen_activities": [],
        "seen_restaurants": [],
        "activity_search_args": {},
        "restaurant_search_args": {},
        "queue_results": {},
        "booking_results": {},
        "route_results": {},
        "route_risks": [],
        "route_replacements": {},
        "route_set_counts": {},
        "route_exec_count": 0,
    }

    MAX_ITERATIONS = 8
    consecutive_tool_errors = 0  # counter for auto-replan trigger
    finish_reject_count = 0      # counter for finish_planning ID rejection

    for iteration in range(MAX_ITERATIONS):
        logger.info(f"[AgentPlan] iteration={iteration}")

        try:
            result = await _call_llm_with_tools(
                messages, planning_tools,
                model_alias="fast_model",
                max_tokens=4096,
            )
        except Exception as e:
            logger.error(f"[AgentPlan] LLM call failed: {e}")
            yield {"_type": "cot_step", "text": f"⚠️ 工具调用异常，切换备用规划方式"}
            plan = await plan_itinerary([], [], {}, {"session_facts": session_facts, "preferences": preferences}, "full_managed")
            yield {"_type": "result", "plan": plan}
            return

        # Append assistant message to history
        messages.append(result["raw_assistant_msg"])

        tool_calls = result["tool_calls"]

        # LLM decided to stop without calling any tool
        if not tool_calls:
            content = result["content"]
            if content.strip():
                try:
                    plan = _parse_json(content)
                    yield {"_type": "result", "plan": plan}
                    return
                except Exception:
                    pass
            yield {"_type": "cot_step", "text": "⚠️ Agent 未输出有效方案，切换备用规划方式"}
            plan = await plan_itinerary([], [], {}, {"session_facts": session_facts, "preferences": preferences}, "full_managed")
            yield {"_type": "result", "plan": plan}
            return

        # Execute each tool call; track (tc, result) pairs for history
        processed: list[tuple[dict, dict]] = []
        early_finish = False

        for tc in tool_calls:
            name = tc["name"]
            args = enforce_planning_tool_args(name, tc["args"], session_facts, preferences)

            if name == "finish_planning":
                nodes = args.get("nodes", [])
                if skip_restaurant:
                    before = len(nodes)
                    nodes = [
                        n for n in nodes
                        if n.get("type") != "restaurant" and n.get("category") != "restaurant"
                    ]
                    removed = before - len(nodes)
                    if removed:
                        yield {"_type": "cot_step",
                               "text": f"⚠️ 已移除 {removed} 个餐厅节点：用户已说明不需要餐食"}
                valid_nodes = [n for n in nodes if n.get("poiId", "") in seen_poi_ids]
                dropped = len(nodes) - len(valid_nodes)
                if dropped:
                    yield {"_type": "cot_step",
                           "text": f"⚠️ 过滤 {dropped} 个不在候选列表中的节点"}

                if len(valid_nodes) >= 2:
                    # Accept: normalize field names and enrich display data from search results
                    enriched = _clamp_nodes_to_day(_enrich_nodes(valid_nodes, seen_poi_data))
                    summary = args.get("summary", "")
                    no_restaurant_reason = ""
                    if (
                        not skip_restaurant
                        and not planning_state.get("seen_restaurants")
                        and not any(n.get("type") == "restaurant" or n.get("category") == "restaurant" for n in enriched)
                    ):
                        no_restaurant_reason = "没有找到满足时间/偏好/营业状态的可用餐厅，已将餐厅时段降级替换为合适活动"
                        summary = no_restaurant_reason if not summary else f"{summary}；{no_restaurant_reason}"
                    plan = {
                        "nodes": enriched,
                        "summary": summary,
                        "cot":     [*args.get("cot", []), *([no_restaurant_reason] if no_restaurant_reason else [])],
                    }
                    yield {"_type": "cot_step", "text": f"✅ Agent 完成规划：{plan['summary']}"}
                    yield {"_type": "result", "plan": plan}
                    return

                # Reject: nodes don't match any searched POI IDs
                finish_reject_count += 1
                valid_id_list = ", ".join(list(seen_poi_ids)[:10])
                yield {"_type": "cot_step",
                       "text": f"⚠️ 节点 poiId 无效（第{finish_reject_count}次拒绝），需使用搜索结果中的真实 ID"}

                # After 2 rejections, stop waiting for LLM and auto-build from real data
                if finish_reject_count >= 2 and seen_poi_data:
                    yield {"_type": "cot_step",
                           "text": "🔄 多次 ID 无效，从已搜索数据中自动构建行程…"}
                    auto_plan = _auto_finish_from_planning_state(
                        planning_state, session_facts, preferences, skip_restaurant
                    )
                    if len(auto_plan.get("nodes", [])) >= 2:
                        yield {"_type": "cot_step",
                               "text": f"✅ 自动构建完成：{len(auto_plan['nodes'])} 个节点"}
                        yield {"_type": "result", "plan": auto_plan}
                        return

                processed.append((tc, {
                    "error": (
                        "finish_planning 被拒绝：nodes 中的 poiId 不在已搜索的 POI 列表中。"
                        f"请直接使用以下已知真实 poiId 构造节点，不要重新搜索：{valid_id_list}。"
                        "用这些 id 填写 nodes[].poiId，立即再次调用 finish_planning。"
                    )
                }))
                early_finish = True
                break  # stop processing further tools in this batch

            if _planning_state_ready(planning_state, skip_restaurant):
                yield {"_type": "cot_step",
                       "text": "✅ 已具备活动/餐饮/路线信息，后端强制收束规划"}
                auto_plan = _auto_finish_from_planning_state(
                    planning_state, session_facts, preferences, skip_restaurant
                )
                if len(auto_plan.get("nodes", [])) >= 2:
                    yield {"_type": "result", "plan": auto_plan}
                    return

            cached_route = None
            route_hint = ""
            if name in ("estimate_routes", "route_check"):
                sequence_key, set_key = _route_cache_keys(args)
                cached_route = planning_state["route_results"].get(sequence_key)
                set_counts = planning_state["route_set_counts"]
                set_counts[set_key] = int(set_counts.get(set_key, 0)) + 1
                same_set_retry = set_counts[set_key] > 1
                route_exec_count = int(planning_state["route_exec_count"])

                if cached_route:
                    route_hint = "路线已估算，无需再次调用 estimate_routes，请调用 finish_planning。"
                elif same_set_retry:
                    cached_route = _nearest_route_result(planning_state["route_results"], set_key)
                    route_hint = "同一批地点的路线已估算，无需调整顺序反复试算，请调用 finish_planning。"
                elif route_exec_count >= 2:
                    cached_route = _nearest_route_result(planning_state["route_results"], set_key)
                    route_hint = "路线估算已达到全局上限，请基于现有结果调用 finish_planning。"
                elif route_exec_count >= 1:
                    known_sets = [
                        key for key, count in planning_state["route_set_counts"].items()
                        if key != set_key and count > 0
                    ]
                    if not known_sets:
                        cached_route = _nearest_route_result(planning_state["route_results"], set_key)
                        route_hint = "路线估算每轮只执行一次，请调用 finish_planning。"

                if cached_route is not None:
                    tool_result = {
                        **dict(cached_route or {}),
                        "cached": True,
                        "message": route_hint,
                    }
                    yield {"_type": "cot_step", "text": "🔧 调用工具 estimate_routes"}
                    yield {"_type": "cot_step", "text": f"   ↳ {route_hint}"}
                    processed.append((tc, tool_result))
                    if _planning_state_ready(planning_state, skip_restaurant):
                        yield {"_type": "cot_step",
                               "text": "✅ 路线重复调用已拦截，自动生成当前最优方案"}
                        auto_plan = _auto_finish_from_planning_state(
                            planning_state, session_facts, preferences, skip_restaurant
                        )
                        if len(auto_plan.get("nodes", [])) >= 2:
                            yield {"_type": "result", "plan": auto_plan}
                            return
                    continue

            # Data tool: execute and emit CoT step
            yield {"_type": "cot_step", "text": f"🔧 调用工具 {name}"}
            tool_result = await _at.execute_tool(name, args, seen_poi_ids)
            summary = _at.summarize_tool_result(name, tool_result)
            yield {"_type": "cot_step", "text": f"   ↳ {summary}"}
            _update_planning_state(planning_state, name, args, tool_result)

            # Track consecutive tool errors (empty results or error keys)
            is_error_result = (
                "error" in tool_result
                or (name in ("search_activities", "search_restaurants")
                    and len(tool_result.get("items", [])) == 0)
            )
            if is_error_result:
                consecutive_tool_errors += 1
                logger.warning(f"[AgentPlan] tool error #{consecutive_tool_errors} for {name}")
                if consecutive_tool_errors >= 5:
                    yield {"_type": "cot_step",
                           "text": "⚠️ 工具连续失败 5 次，自动触发重新规划…"}
                    yield {"_type": "tool_error_limit"}
                    return
            else:
                consecutive_tool_errors = 0  # reset on success

            # Store full POI data for later enrichment
            if name in ("search_activities", "search_restaurants"):
                for item in tool_result.get("items", []):
                    pid = item.get("poi_id", "")
                    if pid:
                        seen_poi_data[pid] = item
                _write_ranked_planning_poi_cache(session_id, planning_state, session_facts, preferences)

            processed.append((tc, tool_result))

        # Append tool results to conversation history
        if processed:
            tcs  = [p[0] for p in processed]
            ress = [p[1] for p in processed]
            result_msgs = _make_tool_result_messages(tcs, ress, _provider())
            messages.extend(result_msgs)

    # Exceeded max iterations
    yield {"_type": "cot_step", "text": "⚠️ Agent 迭代超限，切换备用规划方式"}
    plan = _auto_finish_from_planning_state(
        planning_state, session_facts, preferences, skip_restaurant
    )
    if len(plan.get("nodes", [])) < 2:
        plan = await plan_itinerary(
            planning_state.get("seen_activities", []),
            planning_state.get("seen_restaurants", []),
            planning_state.get("weather") or {},
            {"session_facts": session_facts, "preferences": preferences},
            "full_managed",
        )
    yield {"_type": "result", "plan": plan}


_AGENT_ICON_MAP = {
    "indoor_playground": "🎪", "picture_book_library": "📚",
    "children_science": "🔬", "citywalk": "🚶", "board_game": "🎲",
    "exhibition": "🎨", "livehouse": "🎵", "restaurant": "🍽️",
    "light_food": "🥗", "bar": "🍻", "park": "🌳", "museum": "🏛️",
    "cinema": "🎬", "sports": "⚽", "shopping": "🛍️",
    "mall_exhibition": "🏬", "outdoor_park": "🌳", "bar_entertainment": "🍺",
}


def _auto_build_plan_from_data(seen_poi_data: dict,
                                session_facts: dict, preferences: dict) -> dict:
    """Build minimal valid plan from already-searched POI data when LLM repeatedly hallucinates IDs."""
    activities  = [v for v in seen_poi_data.values() if v.get("type") == "activity"]
    restaurants = [v for v in seen_poi_data.values() if v.get("type") == "restaurant"]
    activities.sort(key=lambda x: _safe_float(x.get("rating"), 0), reverse=True)
    restaurants.sort(key=lambda x: _safe_float(x.get("rating"), 0), reverse=True)

    start_time = session_facts.get("start_time", "14:00")
    try:
        start_min = _normalize_start_time_for_math(start_time)
        sh, sm = start_min // 60, start_min % 60
    except Exception:
        sh, sm = 14, 0

    def add_min(h, m, d):
        t = _add_minutes(h * 60 + m, d); return t // 60, t % 60

    def fmt(h, m):
        return _fmt_hhmm(h * 60 + m)

    cur_h, cur_m = add_min(sh, sm, 20)
    nodes = []

    for act in activities[:2]:
        dur = act.get("estimated_duration_min", 90)
        eh, em = add_min(cur_h, cur_m, dur)
        q = act.get("queue_min")
        nodes.append({
            "id": f"node_{len(nodes)+1:03d}",
            "type": "activity",
            "icon": _AGENT_ICON_MAP.get(act.get("category", ""), "🎯"),
            "name": act.get("name", "活动"),
            "sub": act.get("address", ""),
            "timeStart": fmt(cur_h, cur_m),
            "timeEnd": fmt(eh, em),
            "distance": _safe_distance_text(act.get("distance_km")),
            "queueMin": q,
            "queueText": f"约{q}分钟" if q else "无需排队",
            "price": f"¥{act.get('ticket_price', 0)}/位" if act.get("ticket_price") else "免费",
            "ticket_price": act.get("ticket_price"),
            "purchase_required": bool(act.get("ticket_price")),
            "ticket_required": bool(act.get("ticket_price")),
            "rating": act.get("rating"),
            "tags": list(act.get("tags", []))[:3],
            "reason": "评分最高候选",
            "poiId": act.get("poi_id", ""),
            "status": "planned", "pinned": False, "locked": False,
            "risk_facts": act.get("risk_facts", []),
            "business_hours": act.get("business_hours", ""),
        })
        cur_h, cur_m = add_min(eh, em, 20)

    skip_restaurant = bool(
        preferences.get("skip_restaurant") or session_facts.get("skip_restaurant")
    )
    if restaurants and not skip_restaurant:
        rst = restaurants[0]
        eh, em = add_min(cur_h, cur_m, 60)
        q = rst.get("queue_min", 0)
        nodes.append({
            "id": f"node_{len(nodes)+1:03d}",
            "type": "restaurant",
            "icon": "🍽️",
            "name": rst.get("name", "餐厅"),
            "sub": rst.get("address", ""),
            "timeStart": fmt(cur_h, cur_m),
            "timeEnd": fmt(eh, em),
            "distance": _safe_distance_text(rst.get("distance_km")),
            "queueMin": q,
            "queueText": f"约{q}分钟" if q > 0 else "无需排队",
            "price": f"¥{rst.get('avg_price', 80)}/位",
            "ticket_price": None,
            "purchase_required": False,
            "ticket_required": False,
            "rating": rst.get("rating"),
            "tags": list(rst.get("tags", []))[:3],
            "reason": "评分最高餐厅",
            "poiId": rst.get("poi_id", ""),
            "status": "planned", "pinned": False, "locked": False,
            "risk_facts": rst.get("risk_facts", []),
            "business_hours": rst.get("business_hours", ""),
        })

    if len(nodes) < 2:
        return {"nodes": [], "summary": "", "cot": ["候选数据不足，无法自动构建"]}

    nodes = _clamp_nodes_to_day(nodes)
    return {
        "nodes": nodes,
        "summary": f"从 {len(seen_poi_data)} 个候选中自动选出 {len(nodes)} 个节点",
        "cot": ["Agent 规划多次失败，已从搜索结果中取评分最高的候选自动构建行程"],
    }


def _route_cache_keys(args: dict) -> tuple[tuple, tuple]:
    origin = str(args.get("origin") or args.get("from_poi") or "current_location")
    destinations = args.get("destinations") or []
    if isinstance(destinations, str):
        destinations = [d.strip() for d in destinations.split(",") if d.strip()]
    destinations = tuple(str(d) for d in destinations if d)
    mode = str(args.get("mode") or args.get("transport_mode") or "taxi")
    sequence_key = (origin, destinations, mode)
    set_key = (origin, tuple(sorted(destinations)), mode)
    return sequence_key, set_key


def _nearest_route_result(route_results: dict, set_key: tuple) -> dict:
    if not route_results:
        return {}
    for sequence_key, result in route_results.items():
        origin, destinations, mode = sequence_key
        if (origin, tuple(sorted(destinations)), mode) == set_key:
            return result
    return next(iter(route_results.values()))


def _update_planning_state(state: dict, name: str, args: dict, result: dict) -> None:
    if not isinstance(result, dict) or "error" in result:
        return
    if name == "get_weather":
        state["weather"] = result
    elif name == "search_activities":
        state["seen_activities"] = list(result.get("items", []) or [])
        state["activity_search_args"] = dict(args or {})
    elif name == "search_restaurants":
        state["seen_restaurants"] = list(result.get("items", []) or [])
        state["restaurant_search_args"] = dict(args or {})
    elif name == "get_queue_status":
        poi_id = args.get("poi_id")
        if poi_id:
            state["queue_results"][poi_id] = result
    elif name == "get_booking_status":
        poi_id = args.get("poi_id")
        if poi_id:
            state["booking_results"][poi_id] = result
    elif name in ("estimate_routes", "route_check"):
        sequence_key, _ = _route_cache_keys(args)
        state["route_results"][sequence_key] = result
        state["route_exec_count"] = int(state.get("route_exec_count", 0)) + 1
        state["route_risks"] = _detect_route_risks(result)


def _planning_state_ready(state: dict, skip_restaurant: bool) -> bool:
    if not state.get("weather"):
        return False
    activities = state.get("seen_activities") or []
    restaurants = state.get("seen_restaurants") or []
    if not activities:
        return False
    if not skip_restaurant and not restaurants:
        return False
    planned_candidate_count = len(activities[:2]) + (0 if skip_restaurant else min(len(restaurants), 1))
    if planned_candidate_count >= 2 and not state.get("route_results"):
        return False
    return True


def _detect_route_risks(route_result: dict, threshold_min: int = 35) -> list[dict]:
    risks = []
    for segment in route_result.get("segments", []) or []:
        travel_time = int(segment.get("travel_time_min") or 0)
        if travel_time > threshold_min:
            risks.append({
                "type": "route_risk",
                "from": segment.get("from"),
                "to": segment.get("to"),
                "duration_min": travel_time,
                "threshold_min": threshold_min,
                "action": "replace_destination",
            })
    return risks


def _replace_route_risky_candidates(selected: list[dict],
                                    candidates: list[dict],
                                    risky_ids: set[str],
                                    replacement_counts: dict,
                                    slot_key: str,
                                    max_replacements: int = 3) -> list[dict]:
    result = list(selected)
    used_ids = {item.get("poi_id") for item in result}
    for index, item in enumerate(list(result)):
        poi_id = item.get("poi_id")
        if poi_id not in risky_ids:
            continue
        count_key = f"{slot_key}:{index}"
        count = int(replacement_counts.get(count_key, 0))
        if count >= max_replacements:
            item.setdefault("risk_facts", [])
            item["risk_facts"] = _append_unique(
                item.get("risk_facts", []),
                ["路线偏远，已达到该节点最多3次替换上限"],
            )
            continue
        replacement = next(
            (
                candidate for candidate in candidates
                if candidate.get("poi_id") not in used_ids
                and candidate.get("poi_id") not in risky_ids
            ),
            None,
        )
        if not replacement:
            item.setdefault("risk_facts", [])
            item["risk_facts"] = _append_unique(
                item.get("risk_facts", []),
                ["路线偏远，但暂无更近同类候选"],
            )
            continue
        result[index] = replacement
        used_ids.discard(poi_id)
        used_ids.add(replacement.get("poi_id"))
        replacement_counts[count_key] = count + 1
    return result


def _auto_finish_from_planning_state(state: dict, session_facts: dict,
                                     preferences: dict,
                                     skip_restaurant: bool = False) -> dict:
    """Build a frontend-ready plan from collected tool state."""
    activities = list(state.get("seen_activities") or [])
    restaurants = [] if skip_restaurant else list(state.get("seen_restaurants") or [])
    if not activities and not restaurants:
        reason = "活动和餐厅候选均为空，无法提供备用规划或降级方案"
        return {"nodes": [], "summary": reason, "no_plan_reason": reason, "cot": [reason]}

    sf_for_score = {
        **dict(session_facts or {}),
        "food_preferences": preferences.get("food") or session_facts.get("food_preferences") or [],
    }
    activities = sorted(
        [item for item in activities if item.get("open_status") != "closed"],
        key=lambda item: _score_activity(item, sf_for_score),
        reverse=True,
    )
    restaurants = sorted(
        restaurants,
        key=lambda item: _score_restaurant(item, sf_for_score),
        reverse=True,
    )
    if not activities:
        reason = "没有找到可用活动候选，无法提供备用规划或降级方案"
        return {"nodes": [], "summary": reason, "no_plan_reason": reason, "cot": [reason]}

    start_time = session_facts.get("start_time", "14:00")
    try:
        start_min = _normalize_start_time_for_math(start_time)
        sh, sm = start_min // 60, start_min % 60
    except Exception:
        sh, sm = 14, 0

    def add_min(h, m, d):
        t = _add_minutes(h * 60 + m, d)
        return t // 60, t % 60

    def fmt(h, m):
        return _fmt_hhmm(h * 60 + m)

    def queue_for(poi_id: str, fallback: int | None = None) -> int:
        queue = (state.get("queue_results") or {}).get(poi_id) or {}
        return int(
            queue.get("estimated_wait_min")
            or queue.get("wait_minutes")
            or fallback
            or 0
        )

    def booking_for(poi_id: str) -> dict:
        return (state.get("booking_results") or {}).get(poi_id) or {}

    cur_h, cur_m = add_min(sh, sm, 20)
    nodes = []
    no_restaurant_reason = ""
    if not restaurants and not skip_restaurant:
        no_restaurant_reason = "没有找到满足时间/偏好/营业状态的可用餐厅，已将餐厅时段降级替换为合适活动"
    selected_activities = activities[:3] if no_restaurant_reason else activities[:2]
    selected_restaurants = restaurants[:1]
    route_risks = state.get("route_risks") or []
    risky_destination_ids = {
        risk.get("to")
        for risk in route_risks
        if risk.get("action") == "replace_destination"
    }
    replacement_counts = state.setdefault("route_replacements", {})
    selected_activities = _replace_route_risky_candidates(
        selected_activities,
        activities,
        risky_destination_ids,
        replacement_counts,
        "activity",
    )
    selected_restaurants = _replace_route_risky_candidates(
        selected_restaurants,
        restaurants,
        risky_destination_ids,
        replacement_counts,
        "restaurant",
    )

    for act in selected_activities:
        dur = int(act.get("estimated_duration_min") or 90)
        eh, em = add_min(cur_h, cur_m, dur)
        poi_id = act.get("poi_id", "")
        booking = booking_for(poi_id)
        q = queue_for(poi_id, act.get("queue_min"))
        tags = list(act.get("tags", []) or [])[:3]
        reason = act.get("_llm_reason") or (tags[0] if tags else "匹配用户偏好")
        if booking.get("availability") in ("limited", "low"):
            reason = "余量紧张·" + reason
        nodes.append({
            "id": f"node_{len(nodes)+1:03d}",
            "type": "activity",
            "icon": _AGENT_ICON_MAP.get(act.get("category", ""), "🎯"),
            "name": act.get("name", "活动"),
            "sub": act.get("address", ""),
            "timeStart": fmt(cur_h, cur_m),
            "timeEnd": fmt(eh, em),
            "distance": _safe_distance_text(act.get("distance_km")),
            "queueMin": q,
            "queueText": f"约{q}分钟" if q > 0 else "无需排队",
            "price": f"¥{act.get('ticket_price', 0)}/位" if act.get("ticket_price") else "免费",
            "ticket_price": act.get("ticket_price"),
            "purchase_required": bool(act.get("ticket_price")),
            "ticket_required": bool(act.get("ticket_price")),
            "rating": act.get("rating"),
            "tags": tags,
            "reason": str(reason)[:30],
            "poiId": poi_id,
            "status": "planned", "pinned": False, "locked": False,
            "booking_required": act.get("booking_required", False),
            "booking_urgent": booking.get("availability") in ("limited", "low"),
            "risk_facts": act.get("risk_facts", []),
            "business_hours": act.get("business_hours", ""),
        })
        cur_h, cur_m = add_min(eh, em, 20)

    if selected_restaurants:
        rst = selected_restaurants[0]
        eh, em = add_min(cur_h, cur_m, 60)
        poi_id = rst.get("poi_id", "")
        q = queue_for(poi_id, rst.get("queue_min"))
        tags = list(rst.get("tags", []) or [])[:3]
        food_prefs = preferences.get("food") or session_facts.get("food_preferences") or []
        reason = "符合" + "、".join(food_prefs) if food_prefs else (tags[0] if tags else "餐厅候选最优")
        nodes.append({
            "id": f"node_{len(nodes)+1:03d}",
            "type": "restaurant",
            "icon": "🍽️",
            "name": rst.get("name", "餐厅"),
            "sub": rst.get("address", ""),
            "timeStart": fmt(cur_h, cur_m),
            "timeEnd": fmt(eh, em),
            "distance": _safe_distance_text(rst.get("distance_km")),
            "queueMin": q,
            "queueText": f"约{q}分钟" if q > 0 else "无需排队",
            "price": f"¥{rst.get('avg_price', 80)}/位",
            "ticket_price": None,
            "purchase_required": False,
            "ticket_required": False,
            "rating": rst.get("rating"),
            "tags": tags,
            "reason": str(reason)[:30],
            "poiId": poi_id,
            "status": "planned", "pinned": False, "locked": False,
            "booking_required": rst.get("booking_required", False),
            "booking_urgent": rst.get("booking_required", False),
            "risk_facts": rst.get("risk_facts", []),
            "business_hours": rst.get("business_hours", ""),
        })

    if len(nodes) < 2:
        reason = "可用候选不足 2 个节点，无法提供备用规划或降级方案"
        return {"nodes": [], "summary": reason, "no_plan_reason": reason, "cot": [reason]}

    nodes = _clamp_nodes_to_day(nodes)
    route_count = len(state.get("route_results") or {})
    weather = state.get("weather") or {}
    route_risk_count = len(state.get("route_risks") or [])
    replacement_total = sum(int(v) for v in (state.get("route_replacements") or {}).values())
    route_note = (
        f"检测到 {route_risk_count} 段路线偏远，已局部换点 {replacement_total} 次"
        if route_risk_count else
        "路线无明显偏远风险"
    )
    return {
        "nodes": nodes,
        "summary": no_restaurant_reason or f"已基于实时候选自动收束：{nodes[0]['name']} → {nodes[-1]['name']}",
        "cot": [
            "后端检测到规划信息已满足完成条件，自动收束",
            "候选地点来自 Mock API 参数化搜索结果",
            *([no_restaurant_reason] if no_restaurant_reason else []),
            f"天气：{weather.get('condition', 'unknown')}",
            f"路线估算结果：{route_count} 组",
            route_note,
            "已结合排队/预约状态生成可展示行程",
        ],
    }


def _enrich_nodes(nodes: list[dict], poi_data: dict[str, dict]) -> list[dict]:
    """Normalize field names from LLM output and enrich display fields from search data."""
    result = []
    for i, n in enumerate(nodes):
        poi_id = n.get("poiId", "")
        src = poi_data.get(poi_id, {})

        time_start = n.get("timeStart") or n.get("startTime", "")
        time_end   = n.get("timeEnd")   or n.get("endTime",   "")

        price_val = (n.get("price") or n.get("price_per_person")
                     or src.get("avg_price") or src.get("ticket_price"))
        if price_val is None:
            price_str = "免费"
        elif isinstance(price_val, (int, float)) and price_val > 0:
            price_str = f"¥{int(price_val)}/位"
        elif isinstance(price_val, str) and price_val:
            price_str = price_val
        else:
            price_str = "免费"

        rating   = n.get("rating") or src.get("rating")
        tags     = n.get("tags")   or src.get("tags") or []
        dist_km  = src.get("distance_km")
        distance = _safe_distance_text(dist_km) if dist_km is not None else n.get("distance", "")

        queue_min  = src.get("queue_min")
        queue_text = n.get("queueText")
        if queue_text is None:
            queue_text = f"约{queue_min}分钟" if queue_min and queue_min > 0 else "无需排队"

        reason = (n.get("reason") or n.get("notes")
                  or src.get("_llm_reason") or "")
        if not reason and tags:
            reason = tags[0]

        sub = n.get("sub") or n.get("address") or src.get("address") or ""

        icon = (n.get("icon")
                or _AGENT_ICON_MAP.get(src.get("category", ""), "")
                or ("🥗" if src.get("type") == "restaurant" else "📍"))

        result.append({
            "id":               n.get("id", f"node_{i+1:03d}"),
            "poiId":            poi_id,
            "name":             n.get("name") or src.get("name", ""),
            "type":             n.get("type", "activity"),
            "icon":             icon,
            "timeStart":        time_start,
            "timeEnd":          time_end,
            "sub":              sub,
            "distance":         distance,
            "queueMin":         queue_min,
            "queueText":        queue_text,
            "price":            price_str,
            "ticket_price":     n.get("ticket_price") if n.get("ticket_price") is not None else src.get("ticket_price"),
            "purchase_required": n.get("purchase_required", bool(src.get("ticket_price"))),
            "ticket_required":   n.get("ticket_required", bool(src.get("ticket_price"))),
            "requires_user_action": n.get("requires_user_action", False),
            "manual_action_required": n.get("manual_action_required", False),
            "rating":           rating,
            "tags":             list(tags)[:3],
            "reason":           str(reason)[:30] if reason else "",
            "status":           n.get("status", "planned"),
            "pinned":           n.get("pinned", False),
            "locked":           n.get("locked", False),
            "booking_required": n.get("booking_required") or src.get("booking_required", False),
            "booking_urgent":   n.get("booking_urgent", False),
            "transit":          n.get("transit"),
            "risk_facts":       src.get("risk_facts", []),
            "business_hours":   src.get("business_hours", ""),
            "open_time":        src.get("open_time", ""),
            "close_time":       src.get("close_time", ""),
            "is_24h":           src.get("is_24h", False),
        })
    return result


# ── CoT extraction from R1 reasoning ────────────────────────────────

def _thinking_to_cot(thinking: str) -> list[str]:
    if not thinking:
        return []
    parts = re.split(r"[。\n]+", thinking)
    steps = [s.strip() for s in parts if len(s.strip()) > 10]
    seen, deduped = set(), []
    for s in steps:
        key = s[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    result = deduped[:7]
    result.append("✅ 方案合格，可输出")
    return result


# ════════════════════════════════════════════════════════════════════
# Skill 1: Needs Clarification
# ════════════════════════════════════════════════════════════════════

async def clarify_needs(message: str, current_time: str, location_hint: str = "") -> dict:
    """
    Infer user needs from first message and generate a confirmation message.
    Returns: {inferred, confidence, confirm_message, missing_fields}
    """
    if not _has_api_key():
        return _fallback_clarify(message, current_time)

    user_p = prompts.CLARIFY_USER.format(
        message=message,
        current_time=current_time,
        location_hint=location_hint,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.CLARIFY_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"inferred":{...},"confirm_message":"...","missing_fields":[]}',
            max_tokens=1024,
        )
        return enforce_required_clarifications(result, message)
    except Exception as e:
        logger.error(f"clarify_needs failed: {e}")
        return enforce_required_clarifications(_fallback_clarify(message, current_time), message)


def _fallback_clarify(message: str, current_time: str, location_hint: str = "") -> dict:
    msg_lower = message.lower()
    detected = detect_specific_preferences(message)

    # Detect scenario
    if any(w in message for w in ['孩子', '娃', '小朋友', '宝贝', '儿子', '女儿']):
        scenario = 'family'
        companions_desc = '一家人出行'
        has_child = True
        companions = ['spouse', 'child']
    elif any(w in message for w in ['朋友', '哥们', '闺蜜', '同事', '同学']):
        scenario = 'friends'
        companions_desc = '和朋友一起'
        has_child = False
        companions = ['friends']
    elif any(w in message for w in ['老婆', '男友', '女友', '对象', '男朋友', '女朋友']):
        scenario = 'couple'
        companions_desc = '两人出行'
        has_child = False
        companions = ['partner']
    elif any(w in message for w in ['老人', '爸妈', '父母', '外公', '外婆', '奶奶', '爷爷']):
        scenario = 'family'
        companions_desc = '带老人出行'
        has_child = False
        companions = ['elderly']
    else:
        scenario = 'family'
        companions_desc = '家人出行'
        has_child = False
        companions = ['family']

    # Detect start time
    time_match = re.search(r'(\d{1,2})\s*[点:时]', message)
    if time_match:
        h = int(time_match.group(1))
        if h < 8:
            h += 12
        start_time = f"{h:02d}:00"
    else:
        start_time = _default_start_time_from_current(current_time)

    # Detect travel style
    if any(w in message for w in ['轻松', '慢慢', '休闲', '不累', '随便', '逛逛']):
        style = 'relaxed'
        style_label = '轻松休闲'
    elif any(w in message for w in ['活力', '活跃', '走走', '多玩', '精彩', '充实']):
        style = 'active'
        style_label = '活力满满'
    else:
        style = 'relaxed'
        style_label = '轻松休闲'

    # Detect food preferences
    food_prefs = list(detected.get("food", []))
    if any(w in message for w in ['减肥', '减脂', '低卡', '清淡', '健康']):
        food_prefs = _append_unique(food_prefs, ['清淡', '低油'])

    # Build confirm message
    has_elderly = any(w in message for w in ['老人', '爸妈', '父母', '外公', '外婆', '奶奶', '爷爷'])

    missing = []
    if has_child:
        missing = ['child_age', 'child_purpose']

    lines = [f"好的！我帮你推测了一下本次出行：\n"]
    if scenario == 'family':
        lines.append(f"👨‍👩‍👧 {companions_desc}")
    elif scenario == 'friends':
        lines.append(f"👥 {companions_desc}")
    elif scenario == 'couple':
        lines.append(f"💑 {companions_desc}")
    lines.append(f"⏰ 出发时间：{start_time}")
    lines.append(f"⏱ 计划时长：约3-4小时")
    lines.append(f"✨ 出行风格：{style_label}")
    if food_prefs:
        lines.append(f"🥗 饮食偏好：{' + '.join(food_prefs)}")
    if detected.get("venue"):
        venue_label = {"mall": "商场/购物中心", "indoor": "室内优先", "outdoor": "户外/公园"}.get(detected["venue"], detected["venue"])
        lines.append(f"📍 场地偏好：{venue_label}")
    if detected.get("activity_preference_label"):
        lines.append(f"🎯 活动偏好：{detected['activity_preference_label']}")
    if detected.get("skip_restaurant"):
        lines.append("🍽️ 餐食安排：已吃过/不安排餐食")
    if has_child:
        lines.append(f"👶 孩子年龄：（还没填，帮我补充一下？）")
        lines.append("🎯 孩子偏好：科普学习（博物馆/科技馆）还是纯粹好玩？")
    if has_elderly:
        lines.append(f"👴 已考虑老人体力限制")

    lines.append(f"\n有什么需要纠正或补充的吗？{('（孩子多大？更偏科普还是纯玩？）' if has_child else '')}")

    inferred = {
        "scenario": scenario,
        "start_time": start_time,
        "duration_hours": 3,
        "companions_desc": companions_desc,
        "companions": companions,
        "has_children": has_child,
        "child_age": None,
        "child_purpose": None,
        "has_elderly": has_elderly,
        "special_needs": (['dietary_restriction'] if food_prefs else []),
        "travel_style": style,
        "food_preferences": food_prefs,
        "venue_preference": detected.get("venue"),
        "activity_preference": detected.get("activity_preference"),
        "activity_preference_label": detected.get("activity_preference_label"),
        "friends_activity_type": detected.get("friends_activity_type"),
        # skip_restaurant is determined by LLM in confirm_preferences;
        # fallback defaults to False (conservative)
        "skip_restaurant": detected.get("skip_restaurant", False),
    }

    return {
        "inferred": inferred,
        "confidence": "medium",
        "confirm_message": "\n".join(lines),
        "missing_fields": missing,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 2: Confirm Preferences
# ════════════════════════════════════════════════════════════════════

def _format_clarify_history(history: list) -> str:
    if not history:
        return ""
    lines = ["## 对话历史（按时间顺序，用于理解指代性表达如「按老婆的来」）"]
    for i, msg in enumerate(history, 1):
        lines.append(f"- 用户第{i}条：【{msg}】")
    lines.append("---\n")
    return "\n".join(lines) + "\n"


async def confirm_preferences(inferred: dict, user_response: str,
                              history: list = None) -> dict:
    """
    Extract final confirmed preferences from user's response to clarification.
    history: list of prior user messages (strings) for context-aware refinement.
    Returns: {session_facts, preferences, ready_to_plan, start_message}
    """
    if not _has_api_key():
        return _fallback_confirm_prefs(inferred, user_response)

    user_p = prompts.CONFIRM_PREFS_USER.format(
        history_section=_format_clarify_history(history),
        inferred=json.dumps(inferred, ensure_ascii=False),
        user_response=user_response,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.CONFIRM_PREFS_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"session_facts":{...},"preferences":{...},"ready_to_plan":true}',
            max_tokens=1024,
        )
        base_preferences = {
            "food": inferred.get("food_preferences", []),
            "venue": inferred.get("venue_preference"),
            "skip_restaurant": inferred.get("skip_restaurant", False),
        }
        facts, prefs = merge_confirmed_state(
            inferred,
            base_preferences,
            result.get("session_facts", {}) or {},
            result.get("preferences", {}),
            user_response,
        )
        result["session_facts"] = facts
        result["preferences"] = prefs
        return result
    except Exception as e:
        logger.error(f"confirm_preferences failed: {e}")
        return _fallback_confirm_prefs(inferred, user_response)


def _fallback_confirm_prefs(inferred: dict, user_response: str) -> dict:
    prefs = dict(inferred)

    # Extract child age
    child_age = _extract_child_age(user_response)
    if child_age is not None:
        prefs['child_age'] = child_age
        prefs["child_age_confirmed_by_user"] = True
    child_purpose = _infer_child_purpose(user_response)
    if child_purpose:
        prefs['child_purpose'] = child_purpose
        prefs["child_purpose_confirmed_by_user"] = True

    # Extract start time override
    time_match = re.search(r'(\d{1,2})\s*[点:时]', user_response)
    if time_match:
        h = int(time_match.group(1))
        if h < 8:
            h += 12
        prefs['start_time'] = f"{h:02d}:00"

    # Extract style override
    if any(w in user_response for w in ['轻松', '慢慢', '随便']):
        prefs['travel_style'] = 'relaxed'
    elif any(w in user_response for w in ['活力', '多玩', '精彩']):
        prefs['travel_style'] = 'active'

    detected_response = detect_specific_preferences(user_response)
    skip_restaurant = bool(prefs.get("skip_restaurant") or detected_response.get("skip_restaurant"))

    # Extract explicit food/activity preferences
    prefs, detected_pref = merge_specific_preferences(prefs, {}, user_response)
    food_prefs = list(prefs.get('food_preferences', []))
    if skip_restaurant:
        food_prefs = []
        prefs["food_preferences"] = []
    if any(w in user_response for w in ['老人', '爸妈', '父母']):
        prefs['has_elderly'] = True

    scenario = prefs.get('scenario', 'family')
    style = prefs.get('travel_style', 'relaxed')

    session_facts = {
        "scenario": scenario,
        "start_time": prefs.get('start_time', '14:00'),
        "duration_hours": prefs.get('duration_hours', 3),
        "companions": prefs.get('companions', ['family']),
        "has_children": bool(prefs.get("has_children") or prefs.get("child_age") or "child" in (prefs.get("companions") or [])),
        "child_age": prefs.get('child_age'),
        "child_purpose": prefs.get("child_purpose"),
        "has_elderly": prefs.get('has_elderly', False),
        "special_needs": prefs.get('special_needs', []),
        "home_area": "北京望京",
        "travel_style": style,
        "skip_restaurant": skip_restaurant,
    }

    mode = "light_managed" if style == 'relaxed' else "full_managed"

    child_age = prefs.get('child_age')
    if child_age:
        start_msg = f"明白了！{child_age}岁小朋友，{prefs.get('start_time','14:00')}出发，帮你找最合适的方案！"
    else:
        start_msg = f"明白了！{prefs.get('start_time','14:00')}出发，马上帮你规划！"

    return {
        "session_facts": session_facts,
        "preferences": {
            "distance": "nearby",
            "food": food_prefs,
            "venue": detected_pref.get("venue") or prefs.get("venue_preference"),
            "mode": mode,
            "avoid": [],
            "skip_restaurant": skip_restaurant,
        },
        "ready_to_plan": True,
        "start_message": start_msg,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 3: Preference Extraction (for direct plan requests)
# ════════════════════════════════════════════════════════════════════

PREFERENCE_EXTRACTION_SYSTEM = prompts.BUTLER_SYSTEM + """

你当前的任务是：从用户的自然语言输入和快捷标签中提取结构化偏好信息。
只输出 JSON，禁止任何其他文字。"""

PREFERENCE_EXTRACTION_USER = """用户输入：{message}
用户选择的快捷标签：{tags}
已识别场景：{scenario}

请提取以下信息并输出 JSON：

{{
  "session_facts": {{
    "scenario": "family|friends",
    "start_time": "HH:MM",
    "companions": ["spouse", "child", ...],
    "child_age": null,
    "home_area": "字符串或null"
  }},
  "preferences": {{
    "distance": "nearby|any",
    "food": ["清淡", "低油", "减脂"] 或空数组,
    "mode": "light_managed|full_managed",
    "avoid": []
  }},
  "need_clarification": false,
  "clarifying_question": null
}}"""


async def extract_preferences(message: str, tags: list, scenario: str) -> dict:
    if not _has_api_key():
        return _fallback_preferences(message, tags, scenario)
    user_p = PREFERENCE_EXTRACTION_USER.format(
        message=message,
        tags=json.dumps(tags, ensure_ascii=False),
        scenario=scenario,
    )
    try:
        _, result = await _call_json_with_retry(
            PREFERENCE_EXTRACTION_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"session_facts":{...},"preferences":{...}}',
            max_tokens=1024,
        )
        return result
    except Exception as e:
        logger.error(f"extract_preferences failed: {e}")
        return _fallback_preferences(message, tags, scenario)


def _fallback_preferences(message: str, tags: list, scenario: str) -> dict:
    start_time = "14:00"
    for t in tags:
        if "1点" in t: start_time = "13:00"
        elif "2点" in t: start_time = "14:00"
        elif "3点" in t: start_time = "15:00"

    tagged_text = " ".join(tags) + " " + message
    child_age = _extract_child_age(tagged_text)
    companions = (["spouse"] if scenario == "family" else ["friends"])
    if any("孩子" in t or "岁" in t for t in tags) or any(w in tagged_text for w in _CHILD_MARKERS):
        companions.append("child")
    detected = detect_specific_preferences(tagged_text)
    food = detected.get("food", [])

    return {
        "session_facts": {
            "scenario": scenario, "start_time": start_time,
            "companions": companions, "child_age": child_age, "home_area": "北京望京",
            "has_children": "child" in companions,
            "child_confirmed_by_user": "child" in companions,
            "child_age_confirmed_by_user": child_age is not None,
            "has_elderly": False, "special_needs": [], "travel_style": "relaxed",
            "food_preferences": food,
            "venue_preference": detected.get("venue"),
            "activity_preference": detected.get("activity_preference"),
            "activity_preference_label": detected.get("activity_preference_label"),
            "friends_activity_type": detected.get("friends_activity_type"),
        },
        "preferences": {"distance": "nearby", "food": food, "venue": detected.get("venue"), "mode": "full_managed", "avoid": []},
        "need_clarification": False, "clarifying_question": None,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 3.5: LLM Candidate Scoring
# ════════════════════════════════════════════════════════════════════

async def score_candidates(activities: list, restaurants: list,
                            session_facts: dict,
                            weather: dict = None) -> tuple[list, list]:
    """
    LLM-based scoring of all POI candidates.
    Attaches _llm_score and _llm_reason to each item in-place, then sorts both lists.
    Falls back to Python scoring if LLM unavailable.
    Returns (sorted_activities, sorted_restaurants).
    """
    if not _has_api_key():
        _fallback_attach_scores(activities, restaurants, session_facts)
        return activities, restaurants

    sf = session_facts
    has_child = sf.get("has_children", False) or bool(sf.get("child_age"))
    food_prefs = sf.get("food_preferences") or sf.get("food", [])

    score_fields = [
        "poi_id", "name", "category", "type", "distance_km", "queue_min",
        "rating", "tags", "facts_tags", "venue", "age_policy", "open_status",
        "availability", "booking_required", "menu_features", "facilities",
    ]
    candidates = []
    for a in activities:
        c = {k: v for k, v in a.items() if k in score_fields}
        c["_type"] = "activity"
        candidates.append(c)
    for r in restaurants:
        c = {k: v for k, v in r.items() if k in score_fields}
        c["_type"] = "restaurant"
        candidates.append(c)

    valid_ids = {c["poi_id"] for c in candidates if c.get("poi_id")}

    user_p = prompts.SCORING_USER.format(
        scenario=sf.get("scenario", "family"),
        special_requirements=json.dumps(food_prefs, ensure_ascii=False),
        has_child=has_child,
        child_age=sf.get("child_age"),
        has_elderly=sf.get("has_elderly", False),
        travel_style=sf.get("travel_style", "relaxed"),
        weather=json.dumps(weather or {}, ensure_ascii=False),
        count=len(candidates),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
    )

    try:
        _, result = await _call_json_with_retry(
            prompts.SCORING_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"scored":[{"poi_id":"...","score":85,"planner_reason":"...","recommended":true}]}',
            max_tokens=4096,
        )
        scored = result.get("scored", [])

        # Anti-hallucination: drop any score whose poi_id isn't in our input
        validated = [s for s in scored if s.get("poi_id") in valid_ids]
        if len(validated) < len(scored):
            logger.warning(f"score_candidates: filtered {len(scored)-len(validated)} hallucinated POI scores")

        score_map = {s["poi_id"]: s for s in validated}

        for item in activities + restaurants:
            pid = item.get("poi_id", "")
            if pid in score_map:
                item["_llm_score"] = score_map[pid].get("score", 50)
                item["_llm_reason"] = score_map[pid].get("planner_reason", "")
            else:
                item["_llm_score"] = 50
                item["_llm_reason"] = ""

        activities.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)
        restaurants.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)

        top_act = activities[0]["name"] if activities else "-"
        top_rst = restaurants[0]["name"] if restaurants else "-"
        logger.info(
            f"score_candidates: top activity={top_act}({activities[0].get('_llm_score',0) if activities else 0}), "
            f"top restaurant={top_rst}({restaurants[0].get('_llm_score',0) if restaurants else 0})"
        )
        return activities, restaurants

    except Exception as e:
        logger.error(f"score_candidates LLM failed: {e}, using fallback scoring")
        _fallback_attach_scores(activities, restaurants, session_facts)
        return activities, restaurants


def _fallback_attach_scores(activities: list, restaurants: list, session_facts: dict):
    """Attach Python-computed scores and sort in-place."""
    sf = session_facts
    sf_ext = {
        **sf,
        "has_children": sf.get("has_children", False) or bool(sf.get("child_age")),
        "food_preferences": sf.get("food_preferences") or sf.get("food", []),
    }
    for a in activities:
        raw = _score_activity(a, sf_ext)
        a["_llm_score"] = max(0, min(100, int(50 + raw * 5)))
        a["_llm_reason"] = a.get("name", "")[:20]
    for r in restaurants:
        raw = _score_restaurant(r, sf_ext)
        r["_llm_score"] = max(0, min(100, int(50 + raw * 5)))
        r["_llm_reason"] = r.get("name", "")[:20]
    activities.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)
    restaurants.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)


# ════════════════════════════════════════════════════════════════════
# Skill 4: Itinerary Planner
# ════════════════════════════════════════════════════════════════════

ICON_MAP = {
    "indoor_playground": "🎪", "picture_book_library": "📚",
    "children_science": "🔬", "citywalk": "🚶", "board_game": "🎲",
    "exhibition": "🎨", "livehouse": "🎵", "restaurant": "🍽️",
    "light_food": "🥗", "bar": "🍻",
}


async def plan_itinerary(activities: list, restaurants: list, weather: dict,
                          prefs: dict, mode: str,
                          scored: bool = False) -> dict:
    if not _has_api_key():
        return _fallback_plan(activities, restaurants, prefs, mode)

    sf   = prefs.get("session_facts", {})
    pref = prefs.get("preferences", {})

    act_fields = ["poi_id", "name", "category", "distance_km", "business_hours",
                  "open_time", "close_time", "is_24h",
                  "booking_required", "queue_min", "estimated_duration_min",
                  "venue", "age_policy", "risk_facts", "open_status", "availability",
                  "available_slots"]
    rst_fields = ["poi_id", "name", "distance_km", "queue_min", "rating",
                  "avg_price", "facilities", "menu_features", "business_hours",
                  "open_time", "close_time", "is_24h",
                  "risk_facts", "location_features"]

    # Pre-compute first node time so LLM can't get it wrong
    try:
        fn_total = _normalize_start_time_for_math(sf.get("start_time", "14:00")) + 20
        first_node_time = _format_start_time(fn_total)
    except Exception:
        first_node_time = sf.get("start_time", "14:00")

    # If candidates were pre-scored, enrich slim() output with score info
    def slim(items, fields):
        result = [{k: v for k, v in p.items() if k in fields} for p in items[:6]]
        for i, item in enumerate(items[:6]):
            if item.get("_llm_score") is not None:
                result[i]["_llm_score"] = item["_llm_score"]
            if item.get("_llm_reason"):
                result[i]["_llm_reason"] = item["_llm_reason"]
        return result

    user_p = prompts.PLANNER_USER.format(
        scenario                  = sf.get("scenario", "family"),
        start_time                = sf.get("start_time", "14:00"),
        first_node_time           = first_node_time,
        companions                = json.dumps(sf.get("companions", []), ensure_ascii=False),
        travel_style              = sf.get("travel_style", "relaxed"),
        preferences               = json.dumps(pref, ensure_ascii=False),
        skip_restaurant           = pref.get("skip_restaurant", False),
        mode                      = mode,
        weather                   = json.dumps(weather, ensure_ascii=False),
        activities                = json.dumps(slim(activities, act_fields), ensure_ascii=False, indent=2),
        restaurants               = json.dumps(slim(restaurants, rst_fields), ensure_ascii=False, indent=2),
        # children
        has_children              = sf.get("has_children", sf.get("has_child", False)),
        child_age                 = sf.get("child_age", "无"),
        child_purpose             = sf.get("child_purpose", "无"),
        # elderly
        has_elderly               = sf.get("has_elderly", False),
        elderly_no_walking        = sf.get("elderly_no_walking", False),
        # gender
        group_gender              = sf.get("group_gender", "unknown"),
        female_count              = sf.get("female_count", 0),
        male_count                = sf.get("male_count", 0),
        female_weight_loss        = sf.get("female_weight_loss", False),
        female_prefer_low_intensity = sf.get("female_prefer_low_intensity", False),
        female_prefer_indoor      = sf.get("female_prefer_indoor", False),
        male_prefer_high_intensity = sf.get("male_prefer_high_intensity", False),
        # friends
        friends_activity_type     = sf.get("friends_activity_type", "无"),
    )

    try:
        thinking, result = await _call_json_with_retry(
            prompts.PLANNER_SYSTEM, user_p,
            model_alias="main_model",
            schema_hint='{"cot":[...],"nodes":[...],"summary":"..."}',
            max_tokens=8000,
        )
        if thinking:
            result["cot"] = _thinking_to_cot(thinking)
        if "nodes" not in result:
            raise ValueError("Missing 'nodes' field")

        # Anti-hallucination: remove nodes whose poiId isn't in our input lists
        valid_poi_ids = {a["poi_id"] for a in activities} | {r["poi_id"] for r in restaurants}
        valid_poi_ids.add("walk_001")  # standard light-tail placeholder
        orig_count = len(result["nodes"])
        result["nodes"] = [
            n for n in result["nodes"]
            if n.get("poiId", n.get("poi_id", "")) in valid_poi_ids
        ]
        if len(result["nodes"]) < orig_count:
            logger.warning(
                f"plan_itinerary: removed {orig_count - len(result['nodes'])} hallucinated nodes"
            )

        return result
    except Exception as e:
        logger.error(f"plan_itinerary LLM failed: {e}, using fallback")
        return _fallback_plan(activities, restaurants, prefs, mode)


def _score_activity(act: dict, sf: dict) -> float:
    """Score an activity based on user preferences. Higher = better fit."""
    scenario = sf.get("scenario", "family")
    style    = sf.get("travel_style", "relaxed")
    child_age = sf.get("child_age")
    has_child = sf.get("has_children", False) or (child_age is not None)
    has_elderly = sf.get("has_elderly", False)
    score = 0.0

    # Scenario exact match beats fallback
    if act.get("scenario") == scenario:
        score += 4.0
    elif act.get("scenario") == "both":
        score += 2.0

    # Travel style vs venue environment
    env = act.get("venue", {}).get("environment", "indoor")
    if style == "active" and env == "outdoor":
        score += 2.0
    elif style in ("relaxed", "cultural") and env == "indoor":
        score += 1.5

    # Distance (prefer closer; active users tolerate farther)
    dist = _safe_float(act.get("distance_km"), 5.0)
    max_dist = 15.0 if style == "active" else 8.0
    if dist <= max_dist:
        score += max(0, 2.5 - dist * 0.2)
    else:
        score -= (dist - max_dist) * 0.5  # penalize beyond threshold

    # Child fit
    if has_child and child_age is not None:
        age_min = act.get("age_policy", {}).get("min_age", 0)
        age_max = act.get("age_policy", {}).get("max_age", 99)
        if age_min <= child_age <= age_max:
            score += 2.5
        elif child_age < age_min:
            score -= 5.0  # hard penalty: too young

    # Elderly: prefer indoor + close
    if has_elderly:
        if env == "indoor":
            score += 1.0
        score -= dist * 0.3

    # Open status
    if act.get("open_status") == "closed":
        score -= 20.0

    # Booking availability
    if act.get("availability") == "limited":
        score -= 0.5

    # Rating bonus
    rating = _safe_float(act.get("rating"), 4.0)
    score += (rating - 4.0) * 1.5

    return score


def _score_restaurant(rst: dict, sf: dict) -> float:
    """Score a restaurant based on user preferences."""
    scenario = sf.get("scenario", "family")
    has_child = sf.get("has_children", False) or (sf.get("child_age") is not None)
    food_prefs = sf.get("food_preferences", [])
    score = 0.0

    # Scenario match
    if rst.get("scenario") == scenario:
        score += 3.0
    elif rst.get("scenario") == "both":
        score += 1.5

    # Distance
    dist = _safe_float(rst.get("distance_km"), 3.0)
    score += max(0, 2.0 - dist * 0.15)

    # Queue time (shorter = better)
    q = int(_safe_float(rst.get("queue_min"), 0))
    if q > 45:
        score -= 2.0
    elif q < 15:
        score += 1.0

    # Food preferences
    features = set(rst.get("menu_features", []))
    pref_aliases = {"清淡": "light_food_options", "低油": "low_oil_options",
                    "减脂": "light_food_options"}
    for pref in food_prefs:
        if pref_aliases.get(pref, pref) in features:
            score += 2.5

    # Child seat for families
    if has_child and rst.get("facilities", {}).get("child_seat"):
        score += 1.5

    # Rating bonus
    rating = _safe_float(rst.get("rating"), 4.0)
    score += (rating - 4.0) * 1.5

    return score


def _fallback_plan(activities: list, restaurants: list, prefs: dict, mode: str) -> dict:
    sf       = prefs.get("session_facts", {})
    scenario = sf.get("scenario", "family")
    style    = sf.get("travel_style", "relaxed")
    child_age = sf.get("child_age")
    has_child = sf.get("has_children", False) or (child_age is not None)
    has_elderly = sf.get("has_elderly", False)
    food_prefs  = sf.get("food_preferences", []) or prefs.get("preferences", {}).get("food", [])
    skip_restaurant = bool(
        prefs.get("preferences", {}).get("skip_restaurant", False)
        or sf.get("skip_restaurant", False)
    )
    if skip_restaurant:
        food_prefs = []

    def add_min(h, m, d): t = _add_minutes(h*60+m, d); return t//60, t%60
    def fmt(h, m): return _fmt_hhmm(h*60+m)

    start_min = _normalize_start_time_for_math(sf.get("start_time", "14:00"))
    sh, sm = start_min // 60, start_min % 60
    cur_h, cur_m = add_min(sh, sm, 20)
    nodes = []

    # ── Score & pick best activity ──────────────────────────────────
    sf_for_score = {**sf, "has_children": has_child, "food_preferences": food_prefs}
    scored_acts = sorted(
        [a for a in activities if a.get("open_status") != "closed"],
        key=lambda a: _score_activity(a, sf_for_score),
        reverse=True,
    )
    act = scored_acts[0] if scored_acts else None

    # ── Score & pick best restaurant ────────────────────────────────
    scored_rsts = sorted(
        restaurants,
        key=lambda r: _score_restaurant({**r, "queue_min": r.get("queue_min", 0)}, sf_for_score),
        reverse=True,
    )
    rst = scored_rsts[0] if scored_rsts else None

    SCENE_LABELS = {
        "family": "家庭亲子", "friends": "朋友聚会",
        "couple": "情侣约会", "solo": "独自出行",
    }
    scene_label = SCENE_LABELS.get(scenario, scenario)
    style_label = "轻松休闲" if style == "relaxed" else "活力满满" if style == "active" else style

    # ── Build activity node ──────────────────────────────────────────
    if act:
        dur = act.get("estimated_duration_min", 90)
        eh, em = add_min(cur_h, cur_m, dur)
        booking_urgent = (act.get("booking_required") and
                          act.get("availability") in ("limited", "low"))
        act_tags = list(act.get("tags", []))[:3] or (
            ["室内"] if act.get("venue", {}).get("environment") == "indoor" else ["户外"]
        )
        reason = act.get("tags", [""])[0] if act.get("tags") else ""
        if has_child:
            reason = f"儿童友好·{reason}" if reason else "儿童友好"
        elif scenario == "couple":
            reason = f"情侣打卡·{reason}" if reason else "适合情侣"
        nodes.append({
            "id": "node_001", "type": "activity",
            "icon": ICON_MAP.get(act.get("category", ""), "🎪"),
            "name": act["name"], "sub": act.get("address", ""),
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(eh, em),
            "duration": f"约{dur//60}小时{dur%60 if dur%60 else ''}{'分钟' if dur%60 else ''}",
            "distance": _safe_distance_text(act.get("distance_km")),
            "queueMin": 0, "queueText": "无需排队",
            "price": f"¥{act.get('ticket_price',88)}/位" if act.get("ticket_price") else "免费",
            "ticket_price": act.get("ticket_price"),
            "purchase_required": bool(act.get("ticket_price")),
            "ticket_required": bool(act.get("ticket_price")),
            "rating": act.get("rating", 4.5),
            "tags": act_tags,
            "reason": reason[:25] or "距离近，体验好",
            "poiId": act["poi_id"],
            "booking_urgent": booking_urgent,
            "status": "planned", "pinned": False, "locked": False,
        })
        cur_h, cur_m = add_min(eh, em, 25 if style == "active" else 35)

    # ── Build restaurant node ────────────────────────────────────────
    if rst and not skip_restaurant:
        eh, em = add_min(cur_h, cur_m, 60)
        q = rst.get("queue_min", 0)
        rst_tags = list(rst.get("tags", []))[:3] or ["餐厅"]
        nodes.append({
            "id": "node_002", "type": "restaurant",
            "icon": "🥗" if any(t in ["轻食", "清淡健康"] for t in rst.get("tags", [])) else "🍽️",
            "name": rst["name"], "sub": rst.get("address", ""),
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(eh, em),
            "duration": "约1小时",
            "distance": _safe_distance_text(rst.get("distance_km")),
            "queueMin": q, "queueText": f"约{q}分钟" if q > 0 else "无需排队",
            "price": f"¥{rst.get('avg_price',80)}/位",
            "ticket_price": None,
            "purchase_required": False,
            "ticket_required": False,
            "rating": rst.get("rating", 4.5),
            "tags": rst_tags,
            "reason": (rst.get("tags", [""])[0] or "口碑好")[:25],
            "poiId": rst["poi_id"],
            "booking_urgent": rst.get("booking_required", False),
            "status": "planned", "pinned": False, "locked": False,
        })
        cur_h, cur_m = add_min(eh, em, 20)

    # ── Optional light tail node ─────────────────────────────────────
    tail_map = {
        "family": ("🛍️", "商场轻松逛逛", "望京SOHO", "亲子收尾，孩子自由活动"),
        "couple": ("☕", "咖啡甜品收尾",  "周边咖啡馆", "情侣闲聊，完美收尾"),
        "friends":("🍺", "夜市/酒吧续摊",  "周边夜市", "朋友续摊，尽兴而归"),
        "solo":   ("☕", "安静咖啡馆",     "周边咖啡馆", "独自充电，放松回顾"),
    }
    if not has_elderly:
        tail = tail_map.get(scenario, tail_map["friends"])
        le, lm = add_min(cur_h, cur_m, 40)
        nodes.append({
            "id": "node_003", "type": "light", "icon": tail[0],
            "name": tail[1], "sub": tail[2],
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(le, lm),
            "duration": "约40分钟", "distance": "步行可达",
            "queueMin": None, "queueText": "无需等待",
            "price": "免费", "rating": None, "tags": ["轻松", "收尾"],
            "reason": tail[3],
            "poiId": "walk_001",
            "booking_urgent": False,
            "status": "optional", "pinned": False, "locked": False,
        })

    act_name = act["name"] if act else "活动"
    rst_name = rst["name"] if rst else "餐厅"
    nodes = _clamp_nodes_to_day(nodes)
    return {
        "cot": [
            f"识别到：{scene_label}·{style_label}，出发时间 {sf.get('start_time','14:00')}",
            f"约束：{'有孩子' if has_child else ''}{'有老人' if has_elderly else ''}{'·' + ','.join(food_prefs) if food_prefs else ''}",
            f"活动评分 Top1：{act_name}（场景匹配+{style_label}偏好+距离综合评分）",
            f"餐厅评分 Top1：{rst_name}（{'含儿童椅' if has_child else ''}{'·' + ','.join(food_prefs[:1]) if food_prefs else ''}综合评分）",
            f"合理性自检：通勤比例约15% ✓  活动时间充足 ✓  节奏{style_label} ✓",
            "✅ 基于偏好评分规划完成",
        ],
        "nodes": nodes,
        "summary": f"{scene_label}·{style_label}方案：{act_name}→{rst_name}，共{len(nodes)}站",
    }


# ════════════════════════════════════════════════════════════════════
# Skill 5: Partial Replanning
# ════════════════════════════════════════════════════════════════════

async def replan_partial(event: dict, itinerary: list, alternatives: dict, memory: dict) -> dict:
    if not _has_api_key():
        return _fallback_replan(event, alternatives)

    user_p = prompts.REPLANNER_USER.format(
        event       = json.dumps(event,       ensure_ascii=False, indent=2),
        itinerary   = json.dumps(itinerary,   ensure_ascii=False, indent=2),
        alternatives= json.dumps(alternatives, ensure_ascii=False, indent=2),
        memory      = json.dumps(memory,      ensure_ascii=False, indent=2),
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.REPLANNER_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"thought":"...","affected_node_id":"...","recommended":{...},"user_message":"..."}',
            max_tokens=2048,
        )
        return result
    except Exception as e:
        logger.error(f"replan_partial failed: {e}")
        return _fallback_replan(event, alternatives)


def _fallback_replan(event: dict, alternatives: dict) -> dict:
    recs = alternatives.get("recommended", [])
    best = recs[0] if recs else {}
    etype = event.get("type", "")
    msg = (f"餐厅排队突增，推荐备选：{best.get('name','附近餐厅')}" if etype == "queue_spike"
           else f"天气影响户外，推荐室内：{best.get('name','室内活动')}")
    return {
        "thought": f"触发原因：{event.get('message','异常')}。替换受影响节点。",
        "affected_node_id": "node_002" if etype == "queue_spike" else "node_001",
        "recommended": {
            "poi_id": best.get("poi_id", ""),
            "name": best.get("name", "备选方案"),
            "icon": "🥗" if etype == "queue_spike" else "🎨",
            "sub": best.get("address", "同商圈内"),
            "queueText": f"约{best.get('queue_min',15)}分钟",
            "distance": _safe_distance_text(best.get("distance_km"), 1.0),
            "tags": best.get("menu_features", [])[:2] or ["室内"],
            "reason": msg[:25],
        },
        "more_options": alternatives.get("more_options", []),
        "user_message": msg,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 6: LLM Simulator Event Generation
# ════════════════════════════════════════════════════════════════════

async def generate_simulator_event(session_context: dict) -> dict:
    """
    Generate a realistic mock environment event for the demo.
    session_context: {scenario, current_time, itinerary, weather, queues, bookings, scenario_script, recent_events}
    """
    if not _has_api_key():
        return _fallback_simulator_event(session_context)

    itinerary = session_context.get("itinerary", [])
    itinerary_summary = json.dumps(
        [{"name": n.get("name"), "type": n.get("type"), "poiId": n.get("poiId")} for n in itinerary],
        ensure_ascii=False
    )

    queue_status = json.dumps(session_context.get("queues", {}), ensure_ascii=False)
    booking_status = json.dumps(session_context.get("bookings", {}), ensure_ascii=False)
    recent_events_str = json.dumps(
        [e.get("type") for e in session_context.get("recent_events", [])[-5:]],
        ensure_ascii=False
    )

    user_p = prompts.SIMULATOR_USER.format(
        scenario=session_context.get("scenario", "family"),
        current_time=session_context.get("current_time", time.strftime("%H:%M")),
        itinerary_summary=itinerary_summary,
        weather=json.dumps(session_context.get("weather", {}), ensure_ascii=False),
        queue_status=queue_status,
        booking_status=booking_status,
        scenario_script=json.dumps(session_context.get("scenario_script", {}), ensure_ascii=False),
        recent_events=recent_events_str,
    )

    try:
        _, result = await _call_json_with_retry(
            prompts.SIMULATOR_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"event_type":"...","target_poi_id":"...","severity":"...","message":"...","state_patch":{...}}',
            max_tokens=1024,
        )
        return result
    except Exception as e:
        logger.error(f"generate_simulator_event failed: {e}")
        return _fallback_simulator_event(session_context)


def _fallback_simulator_event(context: dict) -> dict:
    """Rule-based fallback event generator."""
    scenario = context.get("scenario", "family")
    queues = context.get("queues", {})
    recent = [e.get("type") for e in context.get("recent_events", [])[-3:]]

    # Pick an event type not recently triggered
    candidates = []
    if "queue_spike" not in recent:
        # Find a restaurant queue that can spike
        rest_poi = "rest_family_001" if scenario == "family" else "rest_friend_001"
        current_wait = queues.get(rest_poi, {}).get("estimated_wait_min", 20)
        if current_wait < 60:
            new_wait = min(current_wait + 25, 75)
            candidates.append({
                "event_type": "queue_spike",
                "target_poi_id": rest_poi,
                "severity": "high" if new_wait > 50 else "medium",
                "message": f"餐厅排队从{current_wait}分钟升至{new_wait}分钟，进入用餐高峰",
                "reason": "临近用餐高峰时段，排队上升属正常现象",
                "agent_dialogue": f"模拟器: 触发排队上升事件 → {rest_poi} {current_wait}min → {new_wait}min",
                "recommended_poll_after_sec": 30,
                "state_patch": {"queue": {
                    "queue_tables": new_wait // 3,
                    "estimated_wait_min": new_wait,
                    "can_take_number": True,
                    "status": "queue_spike" if new_wait > 45 else "rising",
                }},
            })

    if "weather_heavy_rain" not in recent:
        weather = context.get("weather", {})
        if weather.get("rain_level", "none") == "none":
            candidates.append({
                "event_type": "weather_heavy_rain",
                "target_poi_id": None,
                "severity": "high",
                "message": "北京朝阳区开始降雨，户外活动受影响",
                "reason": "午后对流天气，降雨概率较高",
                "agent_dialogue": "模拟器: 触发天气变化事件 → 多云转小雨",
                "recommended_poll_after_sec": 60,
                "state_patch": {"weather": {
                    "condition": "light_rain",
                    "rain_level": "light",
                    "risk_level": "medium",
                }},
            })

    if candidates:
        import random
        return random.choice(candidates)

    # Default: mild queue fluctuation
    rest_poi = "rest_family_001" if scenario == "family" else "rest_friend_001"
    current = queues.get(rest_poi, {}).get("estimated_wait_min", 18)
    new_wait = max(5, current + (10 if current < 30 else -10))
    return {
        "event_type": "queue_spike" if new_wait > current else "queue_drop",
        "target_poi_id": rest_poi,
        "severity": "low",
        "message": f"餐厅排队变化：{current}分钟 → {new_wait}分钟",
        "reason": "常规排队波动",
        "agent_dialogue": f"模拟器: 排队自然波动 {current}→{new_wait}分钟",
        "recommended_poll_after_sec": 30,
        "state_patch": {"queue": {
            "queue_tables": max(1, new_wait // 4),
            "estimated_wait_min": new_wait,
            "can_take_number": True,
            "status": "normal",
        }},
    }


# ════════════════════════════════════════════════════════════════════
# Skill 7: Natural Language → Simulator Event
# ════════════════════════════════════════════════════════════════════

_INJECT_SYSTEM = """你是一个本地生活模拟器，把自然语言描述转换为结构化模拟事件。
只输出 JSON，严禁其他文字：
{
  "event_type": "queue_spike|queue_drop|weather_heavy_rain|weather_clear|activity_closed|custom",
  "target_poi_id": "行程中对应的 poi_id，无则 null",
  "severity": "low|medium|high",
  "message": "中文简洁描述",
  "state_patch": {
    "queue": {"estimated_wait_min": N, "queue_tables": N, "status": "queue_spike|normal"} ,
    "weather": {"condition": "sunny|light_rain|heavy_rain", "rain_level": "none|light|heavy", "risk_level": "low|medium|high"}
  },
  "agent_dialogue": "一句话说明触发了什么"
}"""


async def interpret_simulator_event(text: str, context: dict) -> dict:
    """Convert natural language event text to structured simulator event."""
    if not _has_api_key():
        return _fallback_interpret_event(text, context)

    itinerary_summary = json.dumps(
        [{"name": n.get("name"), "type": n.get("type"), "poiId": n.get("poiId")}
         for n in context.get("itinerary", [])],
        ensure_ascii=False,
    )
    user_p = (
        f"当前行程：{itinerary_summary}\n"
        f"当前天气：{json.dumps(context.get('weather', {}), ensure_ascii=False)}\n"
        f"当前排队：{json.dumps(context.get('queues', {}), ensure_ascii=False)}\n\n"
        f"测试者描述的事件：{text}\n\n"
        "请转换为结构化格式，如果涉及餐厅排队请从行程中找对应 poiId。"
    )
    try:
        _, result = await _call_json_with_retry(
            _INJECT_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"event_type":"...","target_poi_id":"...","severity":"...","message":"...","state_patch":{...}}',
            max_tokens=512,
        )
        return result
    except Exception as e:
        logger.error(f"interpret_simulator_event failed: {e}")
        return _fallback_interpret_event(text, context)


def _fallback_interpret_event(text: str, context: dict) -> dict:
    itinerary = context.get("itinerary", [])
    rest_poi = next(
        (n.get("poiId") for n in itinerary if n.get("type") == "restaurant"),
        "rest_family_001",
    )

    # Queue spike / drop
    if any(w in text for w in ["排队", "等待", "人多", "拥挤", "堵"]):
        m = re.search(r"(\d+)", text)
        wait = int(m.group(1)) if m else 60
        return {
            "event_type": "queue_spike",
            "target_poi_id": rest_poi,
            "severity": "high" if wait > 45 else "medium",
            "message": f"餐厅排队约{wait}分钟，进入高峰",
            "state_patch": {"queue": {
                "estimated_wait_min": wait,
                "queue_tables": max(1, wait // 3),
                "status": "queue_spike" if wait > 45 else "rising",
            }},
            "agent_dialogue": f"注入排队事件 → {rest_poi} 排队 {wait} 分钟",
        }

    # Weather
    if any(w in text for w in ["下雨", "雨", "天气差", "暴雨", "大雨"]):
        heavy = any(w in text for w in ["暴雨", "大雨", "很大"])
        return {
            "event_type": "weather_heavy_rain",
            "target_poi_id": None,
            "severity": "high",
            "message": ("大雨预警" if heavy else "开始降雨") + "，户外活动受影响",
            "state_patch": {"weather": {
                "condition": "heavy_rain" if heavy else "light_rain",
                "rain_level": "heavy" if heavy else "light",
                "risk_level": "high" if heavy else "medium",
            }},
            "agent_dialogue": f"注入天气事件 → {'大雨' if heavy else '小雨'}预警",
        }

    # Activity closed
    if any(w in text for w in ["关闭", "关门", "满了", "停止", "关了"]):
        act_poi = next(
            (n.get("poiId") for n in itinerary if n.get("type") == "activity"), None
        )
        return {
            "event_type": "activity_closed",
            "target_poi_id": act_poi,
            "severity": "high",
            "message": "活动场地临时关闭，请调整行程",
            "state_patch": {},
            "agent_dialogue": "注入场地关闭事件",
        }

    # Queue clear / fallback
    return {
        "event_type": "custom",
        "target_poi_id": None,
        "severity": "medium",
        "message": text[:60],
        "state_patch": {},
        "agent_dialogue": f"注入自定义事件: {text[:30]}",
    }


# ════════════════════════════════════════════════════════════════════
# Skill 8: Queue Timing Advice
# ════════════════════════════════════════════════════════════════════

async def get_queue_advice(restaurant_name: str, current_wait: int,
                            trend: str, history: list,
                            planned_time: str, current_time: str) -> dict:
    if not _has_api_key():
        return _fallback_queue_advice(restaurant_name, current_wait, trend, planned_time, current_time)

    user_p = prompts.QUEUE_ADVICE_USER.format(
        restaurant_name=restaurant_name,
        current_wait=current_wait,
        trend=trend,
        history=json.dumps(history),
        planned_time=planned_time,
        current_time=current_time,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.QUEUE_ADVICE_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"advice_type":"...","chat_message":"...","optimal_depart_time":"..."}',
            max_tokens=512,
        )
        return result
    except Exception as e:
        logger.error(f"get_queue_advice failed: {e}")
        return _fallback_queue_advice(restaurant_name, current_wait, trend, planned_time, current_time)


def _fallback_queue_advice(name: str, wait: int, trend: str,
                            planned_time: str, current_time: str) -> dict:
    if wait <= 15:
        return {
            "advice_type": "go_now",
            "chat_message": f"🍽️ 好消息！{name} 目前排队仅 {wait} 分钟，现在是去的好时机！",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": wait,
        }
    elif trend == "rising" and wait > 40:
        # Suggest going earlier than planned
        try:
            ph, pm = map(int, planned_time.split(":"))
            early_h, early_m = ph, max(0, pm - 30)
            if early_m < 0:
                early_h -= 1
                early_m += 60
            depart = f"{early_h:02d}:{early_m:02d}"
        except Exception:
            depart = planned_time
        return {
            "advice_type": "go_early",
            "chat_message": f"⚠️ {name} 排队 {wait} 分钟且持续上升，建议提前到 {depart} 出发，避开高峰！",
            "optimal_depart_time": depart,
            "expected_wait_at_arrival": wait + 10,
        }
    elif trend == "falling":
        return {
            "advice_type": "wait",
            "chat_message": f"📉 {name} 排队正在减少（当前 {wait} 分钟），可以稍等一会儿再去，预计更短。",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": max(10, wait - 10),
        }
    else:
        return {
            "advice_type": "go_now",
            "chat_message": f"🍽️ {name} 当前排队约 {wait} 分钟，按计划 {planned_time} 出发即可。",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": wait,
        }


# ════════════════════════════════════════════════════════════════════
# Utility: Feasibility Validation
# ════════════════════════════════════════════════════════════════════

def validate_feasibility(nodes: list, session_facts: dict) -> list[str]:
    """
    Quick feasibility check on generated itinerary nodes.
    Returns a list of risk strings (empty = all good).

    Checks:
    - Node count ≤ 6
    - Per-segment commute ratio: transit_to_node ≤ node_activity_duration
      (calculated per leg, not as a global ratio)
    - No time overlaps between consecutive nodes
    - Child-friendly end time ≤ 20:00
    """
    def _t(node: dict, *keys) -> str:
        for k in keys:
            v = node.get(k, "")
            if v:
                return v
        return ""

    def _parse(t: str) -> int | None:
        try:
            h, m = map(int, t.split(":"))
            return h * 60 + m
        except Exception:
            return None

    if not nodes:
        return ["行程为空"]

    risks = []
    has_child = session_facts.get("has_children") or bool(session_facts.get("child_age"))

    if len(nodes) > 6:
        risks.append(f"节点数 {len(nodes)} 超过6个上限")

    # ── Per-segment commute check ──────────────────────────────────────
    # For each node, compare the transit gap before it against the node's own duration.
    # The first leg (home → first POI) uses session start_time as baseline.
    trip_start_min = _parse(session_facts.get("start_time", ""))

    for i, node in enumerate(nodes):
        ns = _parse(_t(node, "startTime", "timeStart"))
        ne = _parse(_t(node, "endTime", "timeEnd"))
        if ns is None or ne is None:
            continue

        act_dur = max(0, ne - ns)
        if act_dur == 0:
            continue

        if i == 0:
            transit = (ns - trip_start_min) if trip_start_min is not None else None
        else:
            prev_ne = _parse(_t(nodes[i - 1], "endTime", "timeEnd"))
            transit = (ns - prev_ne) if prev_ne is not None else None

        if transit is not None and transit > 0:
            ratio = transit / (transit + act_dur)
            name = node.get("name", f"节点{i+1}")
            if ratio > 0.6:
                risks.append(
                    f"前往「{name}」的通勤({transit}分钟)远超停留({act_dur}分钟)，建议换更近的地点"
                )
            elif ratio > 0.5:
                risks.append(
                    f"前往「{name}」的通勤({transit}分钟)略超停留({act_dur}分钟)，可考虑调整"
                )

    # ── End time check ────────────────────────────────────────────────
    last_end = _t(nodes[-1], "endTime", "timeEnd")
    last_end_min = _parse(last_end)
    if has_child and last_end_min is not None and last_end_min > 20 * 60:
        risks.append(f"结束时间 {last_end} 超过20:00（有儿童场景）")

    # ── Time overlap check ────────────────────────────────────────────
    for i in range(len(nodes) - 1):
        cur_end = _parse(_t(nodes[i], "endTime", "timeEnd"))
        nxt_start = _parse(_t(nodes[i + 1], "startTime", "timeStart"))
        if cur_end is not None and nxt_start is not None and nxt_start < cur_end:
            risks.append(
                f"「{nodes[i].get('name', i+1)}」→「{nodes[i+1].get('name', i+2)}」时间重叠"
            )

    return risks
