"""
Search tools — query activities and restaurants from mock_api.
These wrap backend/tools.py with rule-aware filtering.
"""
from __future__ import annotations
import sys
import os

# Allow imports from backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def search_activities(
    scenario: str,
    categories: str | None = None,
    planned_time: str | None = None,
    planned_end_time: str | None = None,
    exclude_tags: list[str] | None = None,
    require_tags: list[str] | None = None,
    preferred_categories: list[str] | None = None,
) -> dict:
    """
    搜索活动 POI。rule 工具返回的 search_filter_hints 应作为 exclude_tags/require_tags 传入。

    Args:
        scenario: family/friends/couple/solo
        categories: 逗号分隔的类别，如 "museum,park"
        planned_time: 计划到达时间 HH:MM
        planned_end_time: 计划离开时间 HH:MM
        exclude_tags: rule 工具返回的禁止标签，如 ["horror_theme", "bar"]
        require_tags: rule 工具返回的必须标签，如 ["family_friendly"]
        preferred_categories: rule 工具返回的优先类别，如 ["museum", "science_center"]
    """
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

    # Apply rule-driven post-filters
    exclude = set(exclude_tags or [])
    require = set(require_tags or [])

    def passes(poi: dict) -> bool:
        tags = set(poi.get("tags", []) or [])
        if exclude & tags:
            return False
        if require and not (require & tags):
            return False
        return True

    filtered = [p for p in items if passes(p)]
    return {"items": filtered, "count": len(filtered), "excluded_count": len(items) - len(filtered)}


async def search_restaurants(
    scenario: str,
    preferences: str | None = None,
    planned_time: str | None = None,
    planned_end_time: str | None = None,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> dict:
    """
    搜索餐厅。rule 工具返回的 search_params_hint.preferences 应作为 preferences 传入。

    Args:
        scenario: family/friends/couple/solo
        preferences: 逗号分隔的饮食偏好，如 "川菜,日料"
        planned_time: 计划到达时间 HH:MM
        planned_end_time: 计划离开时间 HH:MM
        require_tags: rule 返回的必须标签，如 ["healthy_options", "children_friendly"]
        exclude_tags: rule 返回的排除标签
    """
    from backend import tools as _tools

    prefs = [p.strip() for p in (preferences or "").split(",") if p.strip()] or None

    items = await _tools.get_restaurants(
        scenario=scenario,
        preferences=prefs,
        planned_time=planned_time,
        planned_end_time=planned_end_time,
    )

    exclude = set(exclude_tags or [])
    require = set(require_tags or [])

    def passes(poi: dict) -> bool:
        tags = set(poi.get("tags", []) or [])
        if exclude & tags:
            return False
        if require and not (require & tags):
            return False
        return True

    filtered = [p for p in items if passes(p)]
    return {"items": filtered, "count": len(filtered)}


async def get_alternatives(
    scenario: str,
    reason: str,
    affected_node_id: str,
    exclude_tags: list[str] | None = None,
) -> dict:
    """获取替代 POI，用于节点被替换时。"""
    from backend import tools as _tools
    return await _tools.get_alternatives(
        scenario=scenario,
        reason=reason,
        affected_node_id=affected_node_id,
    )
