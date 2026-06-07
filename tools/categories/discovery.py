"""
Discovery Tools — 资源发现
SearchActivity: 活动检索
SearchRestaurant: 餐厅检索
SearchAlternative: 备选资源检索
所有数据来自 Mock API，禁止编造 POI 信息。
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _poi_id(item: dict) -> str:
    return str(item.get("poi_id") or item.get("poiId") or "")


def _with_duration_bounds(item: dict, default_min: int, min_floor: int, max_ceiling: int) -> dict:
    out = dict(item or {})
    try:
        recommended = int(out.get("estimated_duration_min") or out.get("duration_min") or default_min)
    except (TypeError, ValueError):
        recommended = default_min
    min_duration = int(out.get("min_duration_min") or max(min_floor, int(recommended * 0.6)))
    max_duration = int(out.get("max_duration_min") or min(max_ceiling, max(recommended + 45, int(recommended * 1.5))))
    out["recommended_duration_min"] = recommended
    out["min_duration_min"] = min_duration
    out["max_duration_min"] = max_duration
    return out


def _filter_excluded(items: list[dict], exclude_poi_ids: list[str] | None) -> tuple[list[dict], int]:
    excluded = {str(x) for x in (exclude_poi_ids or []) if x}
    if not excluded:
        return list(items or []), 0
    kept = [item for item in (items or []) if _poi_id(item) not in excluded]
    return kept, len(items or []) - len(kept)


async def search_activity(
    scenario: str,
    categories: str | None = None,
    planned_time: str | None = None,
    planned_end_time: str | None = None,
    exclude_tags: list[str] | None = None,
    require_tags: list[str] | None = None,
    preferred_categories: list[str] | None = None,
    exclude_poi_ids: list[str] | None = None,
    duration_min: int | None = None,
) -> dict:
    """搜索活动 POI。exclude_tags/require_tags 由规则阶段提供，直接传入。"""
    from backend import tools as _tools

    cats = None
    if preferred_categories:
        cats = list(preferred_categories)
    if categories:
        extra = [c.strip() for c in categories.split(",") if c.strip()]
        cats = list(set((cats or []) + extra))

    items = await _tools.get_activities(
        scenario=scenario,
        categories=cats,
        planned_time=planned_time,
        planned_end_time=planned_end_time,
    )
    items, old_route_excluded_count = _filter_excluded(items, exclude_poi_ids)
    items = [_with_duration_bounds(item, 90, 45, 180) for item in items]

    exclude = set(exclude_tags or [])
    require = set(require_tags or [])

    def passes_full(poi: dict) -> bool:
        tags = set(poi.get("tags", []) or [])
        if exclude & tags:
            return False
        if require and not (require & tags):
            return False
        return True

    def passes_exclude_only(poi: dict) -> bool:
        tags = set(poi.get("tags", []) or [])
        return not bool(exclude & tags)

    filtered = [p for p in items if passes_full(p)]

    # require_tags 过于严格（过滤了 >80% 且结果为0）时自动降级：只保留 exclude_tags
    require_tags_dropped = False
    if require and items and not filtered:
        filtered = [p for p in items if passes_exclude_only(p)]
        require_tags_dropped = True

    # 检测用户请求的类别是否出现在结果中
    preference_gaps: list[dict] = []
    requested_cats: set[str] = set()
    if categories:
        requested_cats.update(c.strip().lower() for c in categories.split(",") if c.strip())
    if preferred_categories:
        requested_cats.update(c.lower() for c in preferred_categories if c)

    if requested_cats:
        found_cats: set[str] = set()
        for item in filtered:
            found_cats.update(t.lower() for t in item.get("tags", []))
            cat = item.get("category", "")
            if cat:
                found_cats.add(cat.lower())
        for req in requested_cats:
            if not any(req in fc for fc in found_cats):
                reason = "no_results" if not filtered else "not_in_results"
                preference_gaps.append({
                    "requested": req,
                    "reason": reason,
                    "detail": f"当前场景/时段无{req}类活动" if not filtered else f"搜索结果中无{req}类活动",
                })

    result: dict = {
        "items": filtered,
        "count": len(filtered),
        "excluded_count": len(items) - len(filtered),
        "old_route_excluded_count": old_route_excluded_count,
        "preference_gaps": preference_gaps,
    }
    if not filtered:
        result["no_plan_reason"] = "当前时间窗/偏好/旧路线排除后没有可用活动候选"
    if require_tags_dropped:
        result["warning"] = "餐厅偏好暂无匹配，已改为附近高评分餐厅"
    return result


# Alias for backward compatibility
search_activities = search_activity


async def search_restaurant(
    scenario: str,
    preferences: str | None = None,
    planned_time: str | None = None,
    planned_end_time: str | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    exclude_poi_ids: list[str] | None = None,
    duration_min: int | None = None,
) -> dict:
    """Search restaurants with in-domain fallback.

    Fallback order:
    1. exact preferences + required tags
    2. derived healthy/light preferences when health demand is involved
    3. ignore unsatisfied preference and return other high-rated restaurants
    """
    from backend import tools as _tools

    prefs = [p.strip() for p in (preferences or "").split(",") if p.strip()] or None
    exclude = set(exclude_tags or [])
    require = set(require_tags or [])

    def passes(poi: dict, active_require: set[str]) -> bool:
        tags = set(poi.get("tags", []) or [])
        if exclude & tags:
            return False
        if active_require and not (active_require & tags):
            return False
        return True

    def numeric(value, default=0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def sort_high_rated(items: list[dict]) -> list[dict]:
        return sorted(
            items,
            key=lambda item: (
                numeric(item.get("rating")),
                -numeric(item.get("avg_price") or item.get("price")),
            ),
            reverse=True,
        )

    async def fetch(active_prefs: list[str] | None) -> tuple[list[dict], int]:
        raw = await _tools.get_restaurants(
            scenario=scenario,
            preferences=active_prefs,
            planned_time=planned_time,
            planned_end_time=planned_end_time,
        )
        kept, excluded_count = _filter_excluded(raw, exclude_poi_ids)
        return [_with_duration_bounds(item, 60, 35, 120) for item in kept], excluded_count

    items, old_route_excluded_count = await fetch(prefs)
    filtered = [p for p in items if passes(p, require)]
    fallback_stage = "exact"
    relaxed_require_tags = False
    ignored_preferences: list[str] = []

    healthy_requested = (
        "healthy_options" in require
        or any(any(k in p for k in ("健康", "低卡", "减脂", "轻食", "沙拉", "清淡", "低油")) for p in (prefs or []))
    )
    derived_prefs = ["轻食", "沙拉", "健康", "低卡", "清淡"] if healthy_requested else []

    if not filtered and derived_prefs:
        derived_items, derived_excluded_count = await fetch(derived_prefs)
        old_route_excluded_count += derived_excluded_count
        filtered = [p for p in derived_items if passes(p, set())]
        if filtered:
            fallback_stage = "derived_preference"
            relaxed_require_tags = bool(require)

    if not filtered and (prefs or require):
        all_items, all_excluded_count = await fetch(None)
        old_route_excluded_count += all_excluded_count
        filtered = [p for p in all_items if passes(p, set())]
        if filtered:
            fallback_stage = "high_rated_any"
            relaxed_require_tags = bool(require)
            ignored_preferences = list(prefs or []) + list(require or [])

    filtered = sort_high_rated(filtered)

    preference_gaps: list[dict] = []
    requested_prefs = [p.strip() for p in (preferences or "").split(",") if p.strip()]
    if requested_prefs:
        found_texts: list[str] = []
        for item in filtered:
            found_texts.append(item.get("name", "").lower())
            found_texts.extend(t.lower() for t in item.get("tags", []))
            found_texts.append(item.get("category", "").lower())
        combined = " ".join(found_texts)
        for pref in requested_prefs:
            if pref.lower() not in combined:
                reason = "no_results" if not filtered else "not_in_results"
                preference_gaps.append({
                    "requested": pref,
                    "reason": reason,
                    "detail": f"当前时段无{pref}餐厅" if not filtered else f"附近餐厅中无{pref}选项",
                })

    result = {
        "items": filtered,
        "count": len(filtered),
        "old_route_excluded_count": old_route_excluded_count,
        "preference_gaps": preference_gaps,
    }
    if not filtered:
        result["no_plan_reason"] = "当前时间窗/偏好/旧路线排除后没有可用餐厅候选"
    if fallback_stage != "exact":
        result["fallback_stage"] = fallback_stage
    if relaxed_require_tags:
        result["relaxed_require_tags"] = True
    if ignored_preferences:
        result["ignored_preferences"] = ignored_preferences
        result["warning"] = "餐厅偏好暂无匹配，已改为附近高评分餐厅"
    return result


# Alias for backward compatibility
search_restaurants = search_restaurant


async def search_alternative(
    scenario: str,
    reason: str,
    affected_node_id: str,
    exclude_tags: list[str] | None = None,
) -> dict:
    """获取替代 POI，节点队列超长/预约满/天气不合适时调用。"""
    from backend import tools as _tools
    return await _tools.get_alternatives(
        scenario=scenario,
        reason=reason,
        affected_node_id=affected_node_id,
    )


# Alias for backward compatibility
get_alternatives = search_alternative
