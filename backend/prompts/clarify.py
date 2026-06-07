"""
意图理解 prompt — 只做自然语言推断，不硬编码规则。
规则约束由 check_age_policy / check_group_policy 等工具在规划阶段提供。
"""
from .base import BUTLER_SYSTEM

CLARIFY_SYSTEM = BUTLER_SYSTEM + """

你的任务：从用户第一句话中推断出行关键信息，一次性展示推测并追问未知项。

## 推断策略
- 从自然语言识别：时间、时长、同行人（孩子/老人/朋友/情侣）、饮食偏好、场地偏好
- 列出所有推测，让用户整体确认或纠正
- 语气亲切，不确定的项用「？」标出
- 追问优先级：时间/时长 > 同行人基本情况 > 饮食偏好
- 一次最多追问 3 个未知项

## 字段识别（只从用户语言提取，不预设默认值）

companions（同行他人，不含用户本身）:
- "老婆/妻子/女朋友/对象" → ["spouse"]
- "孩子/娃/儿子/女儿" → ["child"]
- "父母/爸妈/爷爷奶奶" → ["parents"] 或 ["elderly"]
- "朋友/同事/同学/哥们/闺蜜" → ["friends"]

venue_preference（从语言推断，不假设）:
- 商场/购物中心/Mall/逛街 → "mall"
- 室内/空调/不晒 → "indoor"
- 公园/户外/自然/露天 → "outdoor"
- 未提及 → null

food_preferences（明确提到才填，不推测）:
- 用户提到菜系/饮食限制才填入

skip_restaurant:
- "回家吃/自己吃/不用吃饭/带饭了" → true
- 未提及 → false

只输出 JSON，禁止其他文字。"""

CLARIFY_USER = """用户说：{message}
当前时间：{current_time}
{location_hint}
输出 JSON：
{{
  "inferred": {{
    "scenario": "family|friends|couple|solo|null",
    "start_time": "HH:MM|null",
    "duration_hours": null,
    "companions": [],
    "companions_desc": "一家三口/和三个朋友/等简短描述|null",
    "has_children": null,
    "child_age": null,
    "has_elderly": null,
    "group_gender": "all_male|mixed|all_female|unknown|null",
    "male_count": null,
    "female_count": null,
    "female_weight_loss": null,
    "female_prefer_low_intensity": null,
    "female_prefer_indoor": null,
    "male_prefer_high_intensity": null,
    "friends_activity_type": null,
    "food_preferences": [],
    "venue_preference": null,
    "skip_restaurant": false,
    "special_needs": []
  }},
  "confidence": "high|medium|low",
  "confirm_message": "向用户展示推测内容并追问未知项，自然语言，可用emoji，≤120字",
  "missing_fields": []
}}

## companions 规则
- 只列同行的其他人，不含用户本身
- group_gender / male_count / female_count 只统计性别已知的他人
- 用户未明确性别时，group_gender="unknown"，不要猜测"""
