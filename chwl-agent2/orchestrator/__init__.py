"""
Orchestrator Layer — 规划与履约的中央协调器

对外暴露 API：
   start_session() → 开始规划（Phase 1 异步启动）
   get_status() → 查询会话状态
   modify_itinerary() → 用户编辑
   confirm_itinerary() → 一键安排（Phase 2 启动）
   handle_user_sentiment() → 情绪/异常上报（Phase 3 启动）
   resolve_confirmation() → 响应用户确认
"""
