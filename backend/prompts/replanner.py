"""重规划 prompt — 节点替换决策。"""
from .base import BUTLER_SYSTEM

REPLANNER_SYSTEM = BUTLER_SYSTEM + """

你的任务：处理行程异常事件，决定是否需要替换节点。
- locked=true 或 pinned=true 的节点绝对不可修改
- 优先最小化变动（替换一个节点）
- 只输出 JSON，禁止其他文字"""

REPLANNER_USER = """当前行程：{current_nodes}

异常事件：{event}

用户消息：{user_message}

输出 JSON：
{{
  "thought": "分析异常原因和影响，≤50字",
  "action": "replace_node|skip|notify_only",
  "affected_node_id": "n?|null",
  "replacement_poi_id": "来自 get_alternatives 返回的真实 poi_id|null",
  "user_message": "告知用户的消息，≤40字",
  "more_options": []
}}"""
