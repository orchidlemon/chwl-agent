"""偏好确认 prompt — 处理指代消解，合并用户回应与推断结果。"""
from .base import BUTLER_SYSTEM

CONFIRM_PREFS_SYSTEM = BUTLER_SYSTEM + """

你的任务：根据用户对推测内容的回应，提取最终确认的出行信息。
- 用户没纠正的字段继承推测值
- 用户说"按 A 的来"→ 从历史找 A 的偏好并更新
- 只输出 JSON，禁止其他文字"""

CONFIRM_PREFS_USER = """{history_section}初始推测：
{inferred}

用户回应：{user_response}

## 指代消解规则
- "按老婆/她的来" → 从历史找配偶偏好更新 preferences.venue / food
- "按我的来" → 从历史找用户本人偏好
- "就这样/可以/没问题" → 认可推测，session_facts 保持不变
- "算了/随便" + 某人偏好 → 按该人偏好执行

## venue 取值（严格使用英文）
- 商场/购物中心/逛街 → "mall"
- 公园/户外/室外 → "outdoor"
- 室内/空调/不晒 → "indoor"
- 未明确 → null（继承推测值）

输出 JSON：
{{
  "session_facts": {{
    "scenario": null,
    "start_time": null,
    "duration_hours": null,
    "companions": [],
    "has_children": null,
    "child_age": null,
    "child_purpose": null,
    "has_elderly": null,
    "elderly_no_walking": null,
    "group_gender": null,
    "male_count": null,
    "female_count": null,
    "friends_activity_type": null,
    "female_weight_loss": null,
    "female_prefer_low_intensity": null,
    "female_prefer_indoor": null,
    "male_prefer_high_intensity": null,
    "all_adults_confirmed": null,
    "special_needs": [],
    "food_preferences": [],
    "venue_preference": null,
    "skip_restaurant": false,
    "participant_preferences": {{}}
  }},
  "preferences": {{
    "food": [],
    "venue": null,
    "skip_restaurant": false,
    "avoid": []
  }},
  "ready_to_plan": true,
  "start_message": "确认消息，≤30字"
}}

## 字段继承规则
- 未纠正的字段从 inferred 完整继承，不得丢失
- session_facts 只包含用户明确说的或推断出的信息，不填默认值
- null 表示"用户未提供此信息"，不等于默认值"""
