"""Environment simulator prompts."""
from .base import BUTLER_SYSTEM

SIMULATOR_SYSTEM = BUTLER_SYSTEM + """

你是环境模拟器，根据当前时间、行程和 Mock API 状态生成真实感的本地生活事件。
- 每次只生成一个事件
- 事件变化要符合现实场景，不要凭空影响无关 POI
- 只能影响当前行程中的 POI；天气事件可以 target_poi_id=null
- 只输出 JSON，禁止其他文字
"""

SIMULATOR_USER = """当前场景：
- 场景类型：{scenario}
- 当前时间：{current_time}
- 已选行程节点：{itinerary_summary}

当前状态：
- 天气：{weather}
- 餐厅排队：{queue_status}
- 预约状态：{booking_status}

场景脚本规则：
{scenario_script}

最近已触发的事件（避免重复）：
{recent_events}

请生成一个合理的环境变化事件。只能影响已选行程节点中的 POI；如果没有合适节点，target_poi_id 填 null。

输出 JSON：
{{
  "event_type": "queue_spike|weather_heavy_rain|booking_full|traffic_delay|activity_capacity_low",
  "target_poi_id": "string or null",
  "severity": "low|medium|high",
  "message": "一句话说明发生了什么事实变化（≤50字）",
  "reason": "为什么此时触发该事件（≤40字）",
  "agent_dialogue": "模拟器Agent对主Agent说的话（≤50字）",
  "recommended_poll_after_sec": 30,
  "state_patch": {{
    "queue": {{
      "queue_tables": 24,
      "estimated_wait_min": 65,
      "can_take_number": true,
      "status": "queue_spike"
    }}
  }}
}}

只填写与事件相关的 state_patch 字段。"""
