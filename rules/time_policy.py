"""
Time Policy Rule
触发场景: 已知出发时间和时长时调用，返回时间边界约束

LLM 调用时机:
- 用户确认出发时间后调用，传入 start_time 和 duration_hours
- 规划前调用以获取各节点时间窗口建议
"""
from __future__ import annotations


def check_time_policy(
    start_time: str | None = None,
    duration_hours: float | None = None,
    scenario: str | None = None,
    has_children: bool | None = None,
    has_elderly: bool | None = None,
    node_count_planned: int | None = None,
) -> dict:
    """
    根据出发时间和时长推算时间约束。

    Args:
        start_time: 出发时间 "HH:MM"
        duration_hours: 计划游玩时长（小时）
        scenario: family/friends/couple/solo
        has_children: 是否有儿童
        has_elderly: 是否有老人
        node_count_planned: 预计安排的节点数量

    Returns:
        constraints 包含时间边界；
        search_params_hint 指导 search_activities/restaurants 的 planned_time 参数。
    """
    result: dict = {
        "hard_blocks": [],
        "constraints": [],
        "recommendations": [],
        "time_windows": {},
        "search_params_hint": {},
        "missing_for_complete_check": [],
        "questions_to_ask_user": [],
    }

    if start_time is None:
        result["missing_for_complete_check"].append("start_time")
        result["questions_to_ask_user"].append({
            "field": "start_time",
            "question": "大概几点出发？",
            "priority": "high",
            "trigger_condition": "start_time 未知，无法推算时间窗口",
        })
        return result

    if duration_hours is None:
        result["missing_for_complete_check"].append("duration_hours")
        result["questions_to_ask_user"].append({
            "field": "duration_hours",
            "question": "大概玩多久？（小时）",
            "priority": "medium",
            "trigger_condition": "duration_hours 未知，无法推算结束时间",
        })
        return result

    # 解析时间
    def to_minutes(t: str) -> int:
        try:
            h, m = map(int, t.strip().split(":"))
            return h * 60 + m
        except Exception:
            return 14 * 60

    def from_minutes(mins: int) -> str:
        day_prefix = ""
        if mins >= 24 * 60:
            mins -= 24 * 60
            day_prefix = "次日 "
        return f"{day_prefix}{mins // 60:02d}:{mins % 60:02d}"

    start_min = to_minutes(start_time)
    duration_min = int(float(duration_hours) * 60)

    # 第一个节点开始时间（出发 + 20分钟交通）
    first_node_start = start_min + 20
    # 最晚结束时间
    latest_end = start_min + duration_min

    # 儿童约束：20:00 前结束
    if has_children:
        latest_end = min(latest_end, 20 * 60)
        result["constraints"].append("有儿童：行程最晚 20:00 结束")

    # 老人约束：活动节奏偏慢，留余量
    if has_elderly:
        result["constraints"].append("有老人：每个节点留足休息时间，不宜跑太多地方")

    result["time_windows"] = {
        "first_node_start": from_minutes(first_node_start),
        "latest_end": from_minutes(latest_end),
        "total_available_min": latest_end - first_node_start,
    }

    # 节点时间窗口推荐（按节点数平均分配）
    n = node_count_planned or 4
    avg_min = (latest_end - first_node_start) // n
    windows = []
    cursor = first_node_start
    for i in range(n):
        node_end = cursor + avg_min
        windows.append({
            "node_index": i + 1,
            "suggested_start": from_minutes(cursor),
            "suggested_end": from_minutes(min(node_end, latest_end)),
            "duration_min": avg_min,
        })
        cursor = node_end
    result["time_windows"]["node_windows"] = windows

    # 给 search 工具的参数提示
    result["search_params_hint"] = {
        "planned_time": from_minutes(first_node_start),
        "planned_end_time": from_minutes(latest_end),
    }

    result["constraints"].append(
        f"行程窗口：{from_minutes(first_node_start)} → {from_minutes(latest_end)}（共{latest_end - first_node_start}分钟）"
    )
    result["constraints"].append(
        "单段通勤时间 ≤ 目的地停留时长（不能因通勤长而压缩节点时间）"
    )
    result["constraints"].append(
        "不得安排连续两个餐厅节点"
    )

    return result
