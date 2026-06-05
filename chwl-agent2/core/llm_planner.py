"""
LLM Planner — 用 DeepSeek 替换所有 mock handler

职责:
1. 意图理解：解析用户自然语言 → 结构化场景/约束/偏好
2. POI 评分：基于用户偏好对候选 POI 做推理评分
3. 行程生成：编排时间线，生成可执行行程
4. 重规划：异常时全量重算行程
5. 对话回复：管家身份的自然语言回复生成
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from core.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ─── System Prompts ──────────────────────────────────────────

PREFERENCE_EXTRACTION_SYSTEM = """
你是一个信息提取器。从用户的口语输入中提取结构化出行信息。

【输出 JSON 格式】
{
    "scene": "family|friends|couple|solo",
    "start_time": "14:00" 或 null,
    "companions": [
        {
            "type": "adult_male|adult_female|child|elder",
            "age": 6 或 null
        }
    ],
    "special_requirements": ["清淡", "少走路"] 或 [],
    "preferences": {
        "indoor": true/false/null,
        "less_queue": true/false,
        "less_walk": true/false,
        "budget": "low|mid|high|null"
    },
    "missing_info": ["start_time"] 或 []
}

【提取规则】
- "我老婆和6岁小孩" → companions: [{type:"adult_female"}, {type:"child", age:6}]
- "3个人" 但没说具体 → 仅记录数量，type 填 null
- 有任何 12岁以下儿童 → scene 强制为 "family"，preferences.less_queue = true
- 有老人（60岁以上）→ preferences.less_walk = true，preferences.indoor = true
- 用户没提出发时间 → missing_info 必须包含 "start_time"
- 不确定的字段填 null，禁止猜测
"""

SCORING_SYSTEM = """
你是一个 POI 评分器。根据用户场景对候选地点打分，输出每个 POI 的评分和推荐理由。

【硬性约束——违反则该 POI 得 0 分】
- 只对输入 candidates 列表中的 POI 打分，严禁编造
- 每个 poi_id 必须来自输入列表

【输出 JSON 格式】
{
    "scored": [
        {
            "poi_id": "string",
            "score": 0-100,
            "tags_matched": ["child_friendly", "indoor"],
            "planner_reason": "一句话，不超过15字",
            "recommended": true/false,
            "disqualified_reason": null 或 "排队超60分钟"
        }
    ]
}

【评分规则——按场景分支执行】

家庭场景（scene=family 或 companions 含 12岁以下儿童）：
- child_friendly 标签：+25 分
- 室内场所：+15 分
- 排队 > 30 分钟：-25 分
- 排队 > 60 分钟：直接 disqualified
- 单次体验时长 > 120 分钟的高强度活动：-20 分

情侣场景（scene=couple）：
- 氛围感标签（romantic/scenic/cozy）：+20 分
- 评分 < 4.0 的餐厅：-15 分

朋友场景（scene=friends）：
- 社交属性标签（lively/bar/activity）：+15 分

通用规则：
- 距离 > 5km：-10 分
- 距离 > 10km：-25 分
- 有 special_requirements "清淡" → healthy_option 标签 +15 分，重油重辣标签 -20 分
"""

ITINERARY_GENERATION_SYSTEM = """
你是一个行程编排师。根据 POI 和用户约束，生成时间合理的行程。

【第一步：出发时间 → 确定用餐模式（必须先执行这一步）】

读取输入中的 departure_time，执行以下判断：

IF departure_time >= "13:00":
    → 跳过午餐
    → 餐厅节点安排在 17:30–19:30 之间（晚餐模式）

ELSE IF departure_time <= "11:00":
    → 第一个节点安排午餐（11:30–13:00）
    → 之后再安排活动

ELSE（11:00–13:00 出发）:
    → 先安排活动，午餐安排在 12:30–13:30

【第二步：同伴约束（有儿童时强制执行）】

IF companions 中存在 age <= 12 的儿童:
    → 单次活动时长 ≤ 90 分钟
    → 每个活动节点之间缓冲时间 ≥ 10 分钟
    → 18:30 之后不安排户外或高强度活动
    → 优先选择 indoor=true 的 POI

【第三步：交通缓冲（所有场景强制执行）】
- 相邻节点之间必须有 ≥ 15 分钟交通缓冲
- 缓冲时间不得被压缩用于延长活动时长

【第四步：user_feedback 处理（feedback 不为空时必须执行）】
- "太早" → 对应节点 start_time 推迟 ≥ 60 分钟
- "太晚" → 对应节点 start_time 提前 ≥ 60 分钟
- "太赶" → 所有节点间缓冲增加到 ≥ 20 分钟
- "时间太短" → 对应节点 duration_min 增加 ≥ 30 分钟

如果处理 feedback 后，新方案与旧方案时间完全相同 → 视为无效，必须重新生成。

【第五步：输出前自检（每项都要核对，不通过则重新生成）】
□ 餐厅节点的 start_time 是否在合法用餐窗口内？
□ departure_time >= 13:00 时，是否没有午餐节点？
□ 有儿童时，每个活动 duration_min 是否 ≤ 90？
□ 相邻节点时间差是否 ≥ 活动时长 + 15 分钟缓冲？
□ 所有 poi_id 是否来自 valid_poi_ids？

任意一项不通过 → 丢弃当前方案，重新生成，禁止输出不合格方案。

【输出 JSON 格式】
{
    "summary": "一句话描述，不超过30字",
    "total_duration_min": 0,
    "meal_mode": "lunch|dinner|skip",
    "nodes": [
        {
            "node_id": "",
            "poi_id": "",
            "poi_name": "",
            "category": "",
            "start_time": "HH:MM",
            "end_time": "HH:MM",
            "duration_min": 0,
            "buffer_before_min": 0,
            "tags": [],
            "feasibility_note": ""
        }
    ]
}
"""

REPLAN_SYSTEM = """
你是行程重规划器。收到结构化约束后，全量重算行程。

【强制执行规则】
1. 丢弃旧方案所有时间分配，从出发时间开始重新排布
2. 保护状态为 completed_lock 或 user_pinned 的节点（时间不变）
3. 未保护的节点全部重新计算

【约束处理——按类型分支】

meal_time_constraint（用餐时间约束）:
- min_time: 餐厅节点 start_time 不得早于此时间
- preferred_window: 尽量安排在此窗口内
- 示例输入: {"type": "meal_time_constraint", "min_time": "17:30", "preferred_window": "17:30-19:00"}

duration_constraint（活动时长约束）:
- target_node: 哪个节点
- min_duration_min / max_duration_min: 时长范围

buffer_constraint（缓冲时间约束）:
- min_buffer_min: 所有节点间最小缓冲

pace_constraint（节奏约束）:
- "relaxed" → 所有缓冲 ≥ 20 分钟，活动不超过 3 个
- "tight" → 缓冲压到 10 分钟，可增加活动数

【输出 JSON 格式】
{
    "replan_summary": "本次调整原因，不超过20字",
    "meal_mode": "lunch|dinner|skip",
    "need_user_confirm": true/false,
    "nodes": [
        {
            "node_id": "",
            "poi_id": "",
            "poi_name": "",
            "start_time": "HH:MM",
            "end_time": "HH:MM",
            "duration_min": 0,
            "buffer_before_min": 0,
            "lock_status": "free|user_pinned|completed_lock",
            "change_reason": "时间调整原因或 null"
        }
    ]
}
"""

RESPONSE_SYSTEM = """
你是本地生活管家「小美」。根据系统状态生成口语化回复。

【回复规则】
- 不超过 2 句话
- 不用敬语，不用表情符号，不用 Markdown
- 直接说结论，不铺垫

【按系统状态分支回复】

state = "planning"（规划中）:
→ 告知正在找地方，预计几秒内出结果
→ 示例："正在帮你找，稍等几秒。"

state = "plan_ready"（方案已生成）:
→ 说出行程亮点（活动名 + 大概时间），邀请确认
→ 示例："帮你排了乐高+烤肉，下午2点出发，要这个方案吗？"

state = "replan_done"（重新规划完成）:
→ 说明改了什么，确认是否满意
→ 示例："把吃饭推到6点了，其他没变，这样行吗？"

state = "confirmed"（已确认）:
→ 告知下一步行动（出发时间 + 第一个目的地）
→ 示例："好，1点出发去乐高，打车约20分钟。"

state = "error"（规划失败）:
→ 说明失败原因（一句），提供重试选项
→ 示例："附近没找到合适的亲子活动，换个区域试试？"

【禁止】
- 不重复用户说的话
- 不说"好的我明白了""没问题"等废话开头
- 不在回复中列出完整行程（那是 show_plan 做的事）
"""


class LLMPlanner:
    """
    LLM 规划器 — 使用 DeepSeek 执行规划推理。

    所有方法返回格式与原来的 mock handler 完全一致，
    可以直接注册到 ToolRegistry。
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    # ── 意图理解 ──

    async def handle_user_context(self, payload: dict) -> dict:
        """解析用户输入 → 结构化上下文"""
        user_input = payload.get("input", "")
        try:
            result = await self.llm.chat_json(
                system=PREFERENCE_EXTRACTION_SYSTEM,
                messages=[{"role": "user", "content": user_input}],
            )
            scene = result.get("scene")
            if scene not in ("family", "friends", "couple", "solo"):
                scene = "family"
            # 检测儿童（从 companions 中）
            companions = result.get("companions", [])
            has_child = any(
                c.get("type") == "child"
                for c in companions
            )
            mode = "full_managed" if has_child else "light_managed"
            return {"success": True, "data": {
                "scene": scene,
                "time_range": "afternoon",
                "distance_constraint": "nearby",
                "companions_text": user_input,
                "companions": companions,
                "special_requirements": result.get("special_requirements", []),
                "missing_info": result.get("missing_info", []),
                "intent_conflict": False,
                "mode": mode,
                "start_time": result.get("start_time", "14:00"),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] user_context 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── POI 评分 ──

    async def handle_candidates_score(self, payload: dict) -> dict:
        """对候选 POI 进行 LLM 推理评分 + POI 合法性校验"""
        candidates = payload.get("candidates", [])
        user_context = payload.get("user_context", {})
        weather = payload.get("weather", {})

        valid_ids = {c.get("poi_id") for c in candidates if c.get("poi_id")}

        try:
            result = await self.llm.chat_json(
                system=SCORING_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "candidates": candidates,
                    "user_scenario": user_context.get("scene", "family"),
                    "special_requirements": user_context.get("special_requirements", []),
                    "weather": weather.get("condition", "晴"),
                    "temperature": weather.get("temperature_c", 25),
                }, ensure_ascii=False)}],
            )
            scored = result.get("scored", [])
            validated = [s for s in scored if s.get("poi_id") in valid_ids]
            if len(validated) < len(scored):
                logger.warning("[LLMPlanner] 过滤 %d 个编造 POI 评分",
                               len(scored) - len(validated))
            return {"success": True, "data": {"scored": validated}}
        except Exception as e:
            logger.error("[LLMPlanner] scoring 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 行程生成 ──

    async def handle_itinerary_generate(self, payload: dict) -> dict:
        """编排行程 + POI 合法性校验"""
        selected = payload.get("selected_nodes", {})
        departure = payload.get("departure_time", "14:00")
        scene = payload.get("scene", "family")
        user_feedback = payload.get("user_feedback", "")

        valid_poi_ids = set()
        poi_category_map = {}
        for key in ("main_activity", "restaurant", "optional_activity"):
            node = selected.get(key, {})
            if isinstance(node, dict) and node.get("poi_id"):
                pid = node["poi_id"]
                valid_poi_ids.add(pid)
                poi_category_map[pid] = key

        try:
            result = await self.llm.chat_json(
                system=ITINERARY_GENERATION_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "departure_time": departure,
                    "scene": scene,
                    "valid_poi_ids": list(valid_poi_ids),
                    "main_activity": selected.get("main_activity", {}),
                    "restaurant": selected.get("restaurant", {}),
                    "optional_activity": selected.get("optional_activity", {}),
                    "user_feedback": user_feedback,
                    "companions": payload.get("companions", []),
                }, ensure_ascii=False)}],
            )

            raw_nodes = result.get("nodes", [])
            validated_nodes = []
            strict_fail = []
            for node in raw_nodes:
                poi_id = node.get("poi_id", "")
                category = node.get("category", "")
                node_name = node.get("poi_name", "")
                if poi_id not in valid_poi_ids:
                    logger.warning("[LLMPlanner] 过滤编造 POI: %s (%s)", node_name, poi_id)
                    continue
                expected_cat = poi_category_map.get(poi_id, "")
                if expected_cat and category and expected_cat != category:
                    strict_fail.append(node)
                    continue
                validated_nodes.append(node)
            if len(validated_nodes) < 2 and strict_fail:
                logger.warning("[LLMPlanner] 严格过滤后节点不足，降级为仅 ID 校验")
                validated_nodes = [n for n in raw_nodes if n.get("poi_id") in valid_poi_ids]

            # 确保有 restaurant 节点
            has_restaurant = any(n.get("category") == "restaurant" for n in validated_nodes)
            if not has_restaurant:
                rest_data = selected.get("restaurant", {})
                if rest_data and rest_data.get("poi_id"):
                    logger.warning("[LLMPlanner] LLM 未生成餐厅节点，自动补入")
                    validated_nodes.append({
                        "node_id": f"node_{uuid.uuid4()}",
                        "poi_id": rest_data.get("poi_id", ""),
                        "poi_name": rest_data.get("name", "餐厅"),
                        "category": "restaurant",
                        "start_time": "",
                        "end_time": "",
                        "duration_min": 60,
                        "tags": [],
                    })

            import uuid
            return {"success": True, "data": {
                "itinerary_id": f"iti_{uuid.uuid4().hex[:8]}",
                "nodes": validated_nodes,
                "total_duration_min": result.get("total_duration_min", 180),
                "summary": result.get("summary", ""),
                "meal_mode": result.get("meal_mode", ""),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] generate 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 重规划 ──

    async def handle_itinerary_replan(self, payload: dict) -> dict:
        """约束驱动全量重算"""
        try:
            trigger = payload.get("trigger", {})
            user_feedback = payload.get("user_feedback", trigger.get("description", ""))
            result = await self.llm.chat_json(
                system=REPLAN_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "trigger_reason": trigger.get("type", "unknown"),
                    "user_feedback": user_feedback,
                    "locked_nodes": payload.get("policy", {}).get("locked_nodes", []),
                    "pending_nodes": payload.get("policy", {}).get("pending_nodes", []),
                }, ensure_ascii=False)}],
            )
            import uuid
            nodes = result.get("nodes", [])
            return {"success": True, "data": {
                "replan_id": f"rep_{uuid.uuid4().hex[:8]}",
                "need_user_confirm": result.get("need_user_confirm", True),
                "nodes": nodes,
                "updated_route_required": True,
            }}
        except Exception as e:
            logger.error("[LLMPlanner] replan 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 对话回复 ──

    async def generate_response(self, state: str, user_message: str) -> str:
        """生成管家回复（根据系统状态）"""
        try:
            text = await self.llm.chat(
                system=RESPONSE_SYSTEM,
                messages=[
                    {"role": "user", "content": json.dumps({
                        "state": state,
                        "user_said": user_message,
                    }, ensure_ascii=False)},
                ],
            )
            return text
        except Exception as e:
            return f"好的，我看看（系统处理中）"
