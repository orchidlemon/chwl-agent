"""
内存 Mock Handler — 模拟 11 个 Mock API

不需要网络、不需要等待队友的 API 就绪。
支持：
- 正常路径（返回预设数据）
- 延迟模拟（delay_ms）
- 失败模拟（按 tool_name 配置错误码）
- 动态状态递进（booking: queued → processing → confirmed）
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Mock 数据池 ──────────────────────────────────────────────

MOCK_LOCATION = {
    "city": "北京",
    "district": "朝阳区",
    "business_area": "望京",
    "lat": 39.998,
    "lng": 116.481,
}

MOCK_WEATHER_SUNNY = {
    "weather": "晴",
    "temperature": 28,
    "wind_level": 2,
    "precipitation_probability": 10,
}

MOCK_WEATHER_RAINY = {
    "weather": "中雨",
    "temperature": 23,
    "wind_level": 3,
    "precipitation_probability": 80,
}

# 家庭场景候选活动
MOCK_ACTIVITIES_FAMILY = [
    {
        "poi_id": "act_fam_001",
        "name": "望京亲子探索乐园",
        "category": "indoor_playground",
        "open_time": "10:00-21:00",
        "ticket_available": True,
        "ticket_price": 128,
        "rating": 4.7,
        "distance_km": 2.4,
        "estimated_duration_min": 120,
        "tags": ["室内", "亲子", "儿童游乐", "5岁+"],
        "child_friendly": True,
        "address": "望京街道XX号 亲子广场3层",
    },
    {
        "poi_id": "act_fam_002",
        "name": "儿童科学体验馆",
        "category": "museum",
        "open_time": "09:30-18:00",
        "ticket_available": True,
        "ticket_price": 88,
        "rating": 4.6,
        "distance_km": 3.1,
        "estimated_duration_min": 100,
        "tags": ["室内", "科普", "亲子", "互动体验"],
        "child_friendly": True,
        "address": "望京科技园 C座 2层",
    },
    {
        "poi_id": "act_fam_003",
        "name": "乐高探索中心",
        "category": "indoor_playground",
        "open_time": "10:00-20:00",
        "ticket_available": True,
        "ticket_price": 158,
        "rating": 4.8,
        "distance_km": 1.8,
        "estimated_duration_min": 90,
        "tags": ["室内", "亲子", "创造力", "5-10岁"],
        "child_friendly": True,
        "address": "望京华彩商业中心 3层",
    },
    {
        "poi_id": "act_fam_004",
        "name": "中国电影博物馆",
        "category": "museum",
        "open_time": "09:00-17:00",
        "ticket_available": True,
        "ticket_price": 60,
        "rating": 4.6,
        "distance_km": 6.8,
        "estimated_duration_min": 120,
        "tags": ["室内", "科普", "电影", "亲子"],
        "child_friendly": True,
        "address": "朝阳区南影路9号",
    },
    {
        "poi_id": "act_fam_005",
        "name": "国家动物博物馆",
        "category": "museum",
        "open_time": "09:00-17:00",
        "ticket_available": True,
        "ticket_price": 40,
        "rating": 4.5,
        "distance_km": 5.2,
        "estimated_duration_min": 100,
        "tags": ["室内", "动物", "科普", "亲子"],
        "child_friendly": True,
        "address": "朝阳区北辰西路1号",
    },
    {
        "poi_id": "act_fam_006",
        "name": "泡泡玛特城市乐园",
        "category": "indoor_playground",
        "open_time": "10:00-20:00",
        "ticket_available": True,
        "ticket_price": 150,
        "rating": 4.4,
        "distance_km": 4.0,
        "estimated_duration_min": 90,
        "tags": ["室内", "潮玩", "亲子", "拍照"],
        "child_friendly": True,
        "address": "朝阳公园南路1号",
    },
    {
        "poi_id": "act_fam_007",
        "name": "北京天文馆",
        "category": "museum",
        "open_time": "09:00-16:30",
        "ticket_available": True,
        "ticket_price": 45,
        "rating": 4.7,
        "distance_km": 7.5,
        "estimated_duration_min": 110,
        "tags": ["室内", "天文", "科普", "亲子"],
        "child_friendly": True,
        "address": "西城区西直门外大街138号",
    },
    {
        "poi_id": "act_fam_008",
        "name": "悠贝亲子绘本馆",
        "category": "indoor_playground",
        "open_time": "09:30-19:00",
        "ticket_available": True,
        "ticket_price": 68,
        "rating": 4.3,
        "distance_km": 1.0,
        "estimated_duration_min": 70,
        "tags": ["室内", "阅读", "亲子", "安静"],
        "child_friendly": True,
        "address": "望京新世界百货 3层",
    },
]

# 朋友场景候选活动
MOCK_ACTIVITIES_FRIENDS = [
    {
        "poi_id": "act_frd_001",
        "name": "望京街区 Citywalk",
        "category": "outdoor_walk",
        "open_time": "全天",
        "ticket_available": True,
        "ticket_price": 0,
        "rating": 4.5,
        "distance_km": 0.5,
        "estimated_duration_min": 90,
        "tags": ["户外", "citywalk", "拍照", "网红打卡"],
        "child_friendly": True,
        "address": "望京SOHO 周边街区",
    },
    {
        "poi_id": "act_frd_002",
        "name": "沉浸式艺术展「幻境」",
        "category": "exhibition",
        "open_time": "10:00-22:00",
        "ticket_available": True,
        "ticket_price": 99,
        "rating": 4.7,
        "distance_km": 1.2,
        "estimated_duration_min": 75,
        "tags": ["室内", "艺术", "拍照", "沉浸式体验"],
        "child_friendly": False,
        "address": "望京艺术中心 B1",
    },
    {
        "poi_id": "act_frd_003",
        "name": "LiveHouse 音乐空间",
        "category": "entertainment",
        "open_time": "14:00-02:00",
        "ticket_available": True,
        "ticket_price": 168,
        "rating": 4.6,
        "distance_km": 2.0,
        "estimated_duration_min": 120,
        "tags": ["室内", "音乐", "社交", "氛围感"],
        "child_friendly": False,
        "address": "望京酒街 88号",
    },
    {
        "poi_id": "act_frd_004",
        "name": "桌游社交馆",
        "category": "indoor_game",
        "open_time": "12:00-24:00",
        "ticket_available": True,
        "ticket_price": 68,
        "rating": 4.4,
        "distance_km": 1.5,
        "estimated_duration_min": 120,
        "tags": ["室内", "社交", "桌游", "多人"],
        "child_friendly": False,
        "address": "望京商业街 3层",
    },
]

# 家庭场景餐厅候选
MOCK_RESTAURANTS_FAMILY = [
    {
        "poi_id": "res_fam_001",
        "category": "restaurant",
        "name": "轻食研究所",
        "open_time": "11:00-21:30",
        "queue_time_min": 90,
        "rating": 4.8,
        "distance_km": 2.1,
        "avg_price": 86,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["轻食", "低卡", "沙拉", "健康"],
        "cuisine": "轻食简餐",
        "address": "望京美食街 12号",
    },
    {
        "poi_id": "res_fam_002",
        "category": "restaurant",
        "name": "花园简餐 Bistro",
        "open_time": "10:30-22:00",
        "queue_time_min": 15,
        "rating": 4.6,
        "distance_km": 1.2,
        "avg_price": 92,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["简餐", "低卡", "商场内", "亲子友好"],
        "cuisine": "西式简餐",
        "address": "望京万象城 4层",
    },
    {
        "poi_id": "res_fam_003",
        "category": "restaurant",
        "name": "亲子轻食餐厅",
        "open_time": "11:00-21:00",
        "queue_time_min": 20,
        "rating": 4.5,
        "distance_km": 0.8,
        "avg_price": 78,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["轻食", "亲子", "低卡", "儿童餐"],
        "cuisine": "轻食简餐",
        "address": "望京亲子广场 2层",
    },
    {
        "poi_id": "res_fam_004",
        "category": "restaurant",
        "name": "西贝莜面村",
        "open_time": "11:00-21:30",
        "queue_time_min": 25,
        "rating": 4.5,
        "distance_km": 1.5,
        "avg_price": 95,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["西北菜", "亲子", "儿童餐", "面食"],
        "cuisine": "西北菜",
        "address": "望京万象城 5层",
    },
    {
        "poi_id": "res_fam_005",
        "category": "restaurant",
        "name": "云海肴云南菜",
        "open_time": "11:00-21:30",
        "queue_time_min": 20,
        "rating": 4.4,
        "distance_km": 1.8,
        "avg_price": 88,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["云南菜", "清淡", "米线", "亲子友好"],
        "cuisine": "云南菜",
        "address": "望京新世界百货 4层",
    },
    {
        "poi_id": "res_fam_006",
        "category": "restaurant",
        "name": "鼎泰丰",
        "open_time": "10:30-22:00",
        "queue_time_min": 35,
        "rating": 4.7,
        "distance_km": 2.0,
        "avg_price": 120,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["小笼包", "精致", "亲子", "清淡"],
        "cuisine": "中式面点",
        "address": "望京SOHO T1 2层",
    },
    {
        "poi_id": "res_fam_007",
        "category": "restaurant",
        "name": "日昌茶餐厅",
        "open_time": "11:00-22:00",
        "queue_time_min": 15,
        "rating": 4.3,
        "distance_km": 1.0,
        "avg_price": 72,
        "child_seat": True,
        "healthy_option": False,
        "tags": ["茶餐厅", "简餐", "亲子", "性价比"],
        "cuisine": "港式茶餐",
        "address": "望京商业街 B1",
    },
    {
        "poi_id": "res_fam_008",
        "category": "restaurant",
        "name": "素虎素食",
        "open_time": "11:00-20:30",
        "queue_time_min": 10,
        "rating": 4.4,
        "distance_km": 2.2,
        "avg_price": 68,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["素食", "健康", "清淡", "低卡"],
        "cuisine": "素食",
        "address": "望京花园西区 1层",
    },
    {
        "poi_id": "res_fam_009",
        "category": "restaurant",
        "name": "潮汕砂锅粥",
        "open_time": "11:00-23:00",
        "queue_time_min": 10,
        "rating": 4.2,
        "distance_km": 1.6,
        "avg_price": 65,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["粥", "清淡", "养生", "亲子"],
        "cuisine": "潮汕菜",
        "address": "望京西园四区 1层",
    },
    {
        "poi_id": "res_fam_010",
        "category": "restaurant",
        "name": "山茶云南菜",
        "open_time": "11:00-21:30",
        "queue_time_min": 5,
        "rating": 4.1,
        "distance_km": 0.9,
        "avg_price": 55,
        "child_seat": False,
        "healthy_option": True,
        "tags": ["云南菜", "简餐", "米线"],
        "cuisine": "云南菜",
        "address": "望京小街 8号",
    },
    {
        "poi_id": "res_fam_011",
        "category": "restaurant",
        "name": "和府捞面",
        "open_time": "10:00-22:00",
        "queue_time_min": 8,
        "rating": 4.3,
        "distance_km": 1.1,
        "avg_price": 48,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["面食", "简餐", "清淡", "快速"],
        "cuisine": "中式面食",
        "address": "望京凯德Mall B1",
    },
    {
        "poi_id": "res_fam_012",
        "category": "restaurant",
        "name": "金鼎轩",
        "open_time": "10:30-22:30",
        "queue_time_min": 30,
        "rating": 4.4,
        "distance_km": 2.5,
        "avg_price": 85,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["粤菜", "茶点", "亲子", "聚餐"],
        "cuisine": "粤菜",
        "address": "望京国际商业中心 3层",
    },
]

# 朋友场景餐厅候选
MOCK_RESTAURANTS_FRIENDS = [
    {
        "poi_id": "res_frd_001",
        "category": "restaurant",
        "name": "创意 Bistro 融合菜",
        "open_time": "11:00-23:00",
        "queue_time_min": 30,
        "rating": 4.7,
        "distance_km": 1.0,
        "avg_price": 138,
        "child_seat": False,
        "healthy_option": False,
        "tags": ["创意菜", "氛围感", "拍照", "约会聚会"],
        "cuisine": "融合菜",
        "address": "望京艺术区 7号",
    },
    {
        "poi_id": "res_frd_002",
        "category": "restaurant",
        "name": "热闹烧烤酒场",
        "open_time": "16:00-02:00",
        "queue_time_min": 45,
        "rating": 4.5,
        "distance_km": 1.8,
        "avg_price": 120,
        "child_seat": False,
        "healthy_option": False,
        "tags": ["烧烤", "热闹", "聚会", "酒水"],
        "cuisine": "烧烤",
        "address": "望京酒街 22号",
    },
    {
        "poi_id": "res_frd_003",
        "category": "restaurant",
        "name": "泰式轻食餐厅",
        "open_time": "11:00-22:00",
        "queue_time_min": 20,
        "rating": 4.6,
        "distance_km": 1.5,
        "avg_price": 105,
        "child_seat": True,
        "healthy_option": True,
        "tags": ["泰式", "轻食", "低卡", "异国风情"],
        "cuisine": "泰式简餐",
        "address": "望京美食街 5号",
    },
    {
        "poi_id": "res_frd_004",
        "category": "restaurant",
        "name": "精酿啤酒餐吧",
        "open_time": "17:00-01:00",
        "queue_time_min": 15,
        "rating": 4.4,
        "distance_km": 1.2,
        "avg_price": 110,
        "child_seat": False,
        "healthy_option": False,
        "tags": ["啤酒", "简餐", "聚会", "热闹"],
        "cuisine": "西式简餐",
        "address": "望京酒街 8号",
    },
    {
        "poi_id": "res_frd_005",
        "category": "restaurant",
        "name": "日式居酒屋",
        "open_time": "17:30-23:30",
        "queue_time_min": 25,
        "rating": 4.5,
        "distance_km": 1.6,
        "avg_price": 125,
        "child_seat": False,
        "healthy_option": False,
        "tags": ["日式", "居酒屋", "氛围感", "拍照"],
        "cuisine": "日式料理",
        "address": "望京商业街 B1层",
    },
    {
        "poi_id": "res_frd_006",
        "category": "restaurant",
        "name": "川渝火锅",
        "open_time": "11:00-23:00",
        "queue_time_min": 35,
        "rating": 4.6,
        "distance_km": 2.0,
        "avg_price": 130,
        "child_seat": False,
        "healthy_option": False,
        "tags": ["火锅", "热闹", "聚会", "麻辣"],
        "cuisine": "火锅",
        "address": "望京美食街 18号",
    },
]

MOCK_WALK_OPTIONS = [
    {
        "poi_id": "walk_001",
        "category": "shopping",
        "name": "商场轻松散步区",
        "category": "shopping",
        "open_time": "10:00-22:00",
        "rating": 4.3,
        "distance_km": 0.3,
        "estimated_duration_min": 40,
        "tags": ["室内", "散步", "商场"],
        "address": "望京万象城",
    },
    {
        "poi_id": "walk_002",
        "category": "shopping",
        "name": "滨河公园散步道",
        "category": "park",
        "open_time": "全天",
        "rating": 4.4,
        "distance_km": 0.8,
        "estimated_duration_min": 45,
        "tags": ["户外", "散步", "自然"],
        "address": "望京滨河路",
    },
    {
        "poi_id": "walk_003",
        "category": "shopping",
        "name": "夜市小吃街",
        "category": "street_food",
        "open_time": "17:00-23:00",
        "rating": 4.5,
        "distance_km": 1.0,
        "estimated_duration_min": 60,
        "tags": ["户外", "美食", "热闹"],
        "address": "望京夜市",
    },
]


# ─── Mock Backend ─────────────────────────────────────────────

class MockBackend:
    """
    内存 Mock 后端。

    用法:
        backend = MockBackend()
        backend.set_fail("weather", "timeout")   # 让天气接口返回超时
        backend.set_delay("booking_execute", 2000)  # booking 延迟 2 秒
        result = backend.handle("weather", {})
    """

    def __init__(self):
        self._fail_config: dict[str, str] = {}   # tool_name -> error_code
        self._delay_config: dict[str, int] = {}  # tool_name -> delay_ms
        self._bookings: dict[str, dict] = {}     # booking_ref -> state
        self._scene_override: Optional[str] = None  # force scene
        self._weather_override: Optional[str] = None  # sunny | rainy
        self._call_count: dict[str, int] = {}    # tool_name -> call_count

    # ── 配置 ──

    def set_fail(self, tool_name: str, error_code: str):
        """配置某个 tool 模拟失败"""
        self._fail_config[tool_name] = error_code

    def clear_fail(self, tool_name: str):
        self._fail_config.pop(tool_name, None)

    def clear_all_fails(self):
        self._fail_config.clear()

    def set_delay(self, tool_name: str, delay_ms: int):
        """配置模拟延迟"""
        self._delay_config[tool_name] = delay_ms

    def set_weather(self, weather: str):
        self._weather_override = weather

    def set_scene(self, scene: str):
        self._scene_override = scene

    def get_call_count(self, tool_name: str) -> int:
        return self._call_count.get(tool_name, 0)

    # ── 统一入口 ──

    async def handle(self, tool_name: str, payload: dict) -> dict:
        """调用对应的 handler（含延迟和失败模拟）"""
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1

        # 模拟延迟
        delay = self._delay_config.get(tool_name, 0)
        if delay > 0:
            await asyncio.sleep(delay / 1000)

        # 模拟失败
        error_code = self._fail_config.get(tool_name)
        if error_code:
            logger.warning("[Mock] %s 模拟失败: %s", tool_name, error_code)
            return {
                "success": False,
                "error": {
                    "code": error_code,
                    "message": f"Mock 模拟失败: {error_code}",
                    "is_retryable": error_code in ("timeout", "network_error", "rate_limited", "internal_error"),
                }
            }

        # 分发到具体 handler
        handler = getattr(self, f"handle_{tool_name.replace('/', '_')}", None)
        if handler:
            result = await handler(payload)
            return {"success": True, "data": result}
        else:
            return {
                "success": False,
                "error": {
                    "code": "internal_error",
                    "message": f"未知 tool: {tool_name}",
                    "is_retryable": False,
                }
            }

    # ── Handler 实现 ──

    async def handle_location(self, payload: dict) -> dict:
        return dict(MOCK_LOCATION)

    async def handle_user_context(self, payload: dict) -> dict:
        query = payload.get("input", "")
        text = query.lower()

        # 场景识别
        if any(k in text for k in ("朋友", "兄弟", "哥们", "闺蜜")):
            scene = "friends"
        elif any(k in text for k in ("老婆", "孩子", "娃", "亲子", "带娃")):
            scene = "family"
        else:
            scene = "family"  # 默认

        if self._scene_override:
            scene = self._scene_override

        # 特殊人员
        special = []
        if "减肥" in text or "减脂" in text:
            special.append("减肥中")
        if "孩子" in text or "娃" in text:
            special.append("5岁儿童")

        return {
            "scene": scene,
            "time_range": "afternoon",
            "distance_constraint": "nearby",
            "companions_text": payload.get("input", ""),
            "special_requirements": special,
            "missing_info": [] if payload.get("start_time") else ["start_time"],
            "intent_conflict": False,
            "mode": "full_managed" if scene == "family" else "light_managed",
            "start_time": payload.get("start_time", "14:00"),
        }

    async def handle_weather(self, payload: dict) -> dict:
        if self._weather_override == "rainy":
            return dict(MOCK_WEATHER_RAINY)
        return dict(MOCK_WEATHER_SUNNY)

    async def handle_activities_search(self, payload: dict) -> dict:
        scene = payload.get("scene", "family")
        if scene == "friends":
            return {"activities": MOCK_ACTIVITIES_FRIENDS}
        return {"activities": MOCK_ACTIVITIES_FAMILY}

    async def handle_restaurants_search(self, payload: dict) -> dict:
        scene = payload.get("scene", "family")
        party_size = payload.get("party_size", 3)
        if scene == "friends":
            restaurants = MOCK_RESTAURANTS_FRIENDS
        else:
            restaurants = MOCK_RESTAURANTS_FAMILY
        return {"restaurants": restaurants, "party_size": party_size}

    async def handle_route_check(self, payload: dict) -> dict:
        destinations = payload.get("destinations", [])
        segments = []
        total_time = 0

        prev = payload.get("origin", "current_location")
        for dest in destinations:
            seg = {
                "from": prev,
                "to": dest,
                "distance_km": round(1.0 + hash(dest) % 30 / 10, 1),
                "travel_time_min": 5 + hash(dest) % 15,
                "walk_time_min": 1 + hash(dest) % 5,
                "transport_mode": payload.get("transport_mode", "taxi"),
            }
            total_time += seg["travel_time_min"]
            segments.append(seg)
            prev = dest

        return {
            "total_travel_time_min": total_time,
            "segments": segments,
        }

    async def handle_candidates_score(self, payload: dict) -> dict:
        """模拟 LLM 评分（事实层不做主观判断，这里简化返回分数）"""
        candidates = payload.get("candidates", [])
        scored = []
        for c in candidates:
            # 简单打分逻辑：随机但可复现
            h = hash(c.get("poi_id", ""))
            score = 60 + abs(h) % 35

            # 排队惩罚
            queue = c.get("queue_time_min", 0)
            if queue > 60:
                score -= 20
            elif queue > 30:
                score -= 10

            scored.append({
                "poi_id": c["poi_id"],
                "name": c.get("name", ""),
                "score": max(0, min(100, score)),
                "score_detail": {
                    "distance_score": max(0, 30 - int(c.get("distance_km", 5) * 5)),
                    "queue_penalty": max(0, min(30, queue // 3)),
                    "preference_match": 20,
                    "comfort_score": 15,
                    "mode_adjustment": 5,
                    "popularity_price": 10,
                },
                "planner_reason": f"{'低卡匹配度高' if c.get('healthy_option') else '氛围感好'}，"
                                  f"{'排队' + str(queue) + '分钟' if queue > 0 else '无需排队'}",
            })
        return {"scored": scored}

    async def handle_itinerary_generate(self, payload: dict) -> dict:
        """生成行程（简化版，直接构造节点）"""
        selected = payload.get("selected_nodes", {})
        departure = payload.get("departure_time", "14:00")
        scene = payload.get("scene", "family")

        nodes = []
        time_slot = departure

        # 主活动
        main = selected.get("main_activity", {})
        if main:
            h = hash(main.get("poi_id", ""))
            dur = main.get("estimated_duration_min", 90)
            start = time_slot
            end = self._add_time(start, dur)
            nodes.append(self._make_node(
                node_id="node_001",
                poi=main,
                cat="main_activity",
                start=start, end=end,
            ))
            time_slot = self._add_time(end, 10)  # 10min 交通缓冲

        # 餐厅
        restaurant = selected.get("restaurant", {})
        if restaurant:
            dur = 70
            start = time_slot
            end = self._add_time(start, dur)
            nodes.append(self._make_node(
                node_id="node_002",
                poi=restaurant,
                cat="restaurant",
                start=start, end=end,
            ))
            time_slot = self._add_time(end, 5)

        # 可选轻活动
        optional = selected.get("optional_activity", {})
        if optional:
            dur = optional.get("estimated_duration_min", 40)
            start = time_slot
            end = self._add_time(start, dur)
            nodes.append(self._make_node(
                node_id="node_003",
                poi=optional,
                cat="optional_activity",
                start=start, end=end,
            ))

        total = self._calc_total_min(nodes)
        return {
            "itinerary_id": f"iti_{uuid.uuid4().hex[:8]}",
            "nodes": [n for n in nodes],
            "total_duration_min": total,
            "summary": f"共 {len(nodes)} 个活动，预计 {total} 分钟",
        }

    async def handle_booking_execute(self, payload: dict) -> dict:
        """执行履约，启动后台状态递进"""
        booking_ref = f"BK-{uuid.uuid4().hex[:8].upper()}"
        self._bookings[booking_ref] = {
            "status": "queued",
            "progress": 0,
            "estimated_wait_minutes": 15,
            "created_at": time.time(),
        }
        # 后台自动推进：queued(5s) -> processing(10s) -> confirmed(15s)
        asyncio.create_task(self._advance_booking(booking_ref))
        return {
            "booking_ref": booking_ref,
            "status": "queued",
            "estimated_wait_minutes": 15,
        }

    async def handle_booking_status(self, payload: dict) -> dict:
        booking_ref = payload.get("booking_ref", "")
        booking = self._bookings.get(booking_ref)
        if not booking:
            return {"status": "unknown", "error": "booking_ref not found"}
        return dict(booking)

    async def handle_itinerary_replan(self, payload: dict) -> dict:
        """动态重规划 - 模拟替换失败节点"""
        trigger = payload.get("trigger", {})
        affected = trigger.get("affected_node_id", "")

        changed_nodes = []
        if "res" in affected or "餐厅" in str(payload):
            changed_nodes.append({
                "old_node_id": affected,
                "old_poi_id": "res_001",
                "new_poi_id": "res_fam_003",
                "new_name": "亲子轻食餐厅",
                "new_scheduled_time": "17:00",
            })

        return {
            "replan_id": f"rep_{uuid.uuid4().hex[:8]}",
            "need_user_confirm": True,
            "changed_nodes": changed_nodes,
            "unchanged_nodes": [],
            "updated_route_required": True,
        }

    # ── 内部辅助 ──

    async def _advance_booking(self, ref: str):
        """queued(5s) -> processing(10s) -> confirmed(15s)"""
        await asyncio.sleep(5)
        if ref in self._bookings:
            self._bookings[ref]["status"] = "processing"
            self._bookings[ref]["progress"] = 0.5
            logger.debug("[Mock] Booking %s -> processing", ref)

        await asyncio.sleep(5)
        if ref in self._bookings:
            self._bookings[ref]["status"] = "confirmed"
            self._bookings[ref]["progress"] = 1.0
            self._bookings[ref]["estimated_wait_minutes"] = 0
            logger.debug("[Mock] Booking %s -> confirmed", ref)

    def _make_node(self, node_id: str, poi: dict, cat: str,
                   start: str, end: str) -> dict:
        return {
            "node_id": node_id,
            "poi_id": poi.get("poi_id", ""),
            "poi_name": poi.get("name", ""),
            "category": cat,
            "start_time": start,
            "end_time": end,
            "duration_min": poi.get("estimated_duration_min", 60),
            "status": "planned",
            "soft_locked": False,
            "completed_lock": False,
            "user_pinned": False,
            "address": poi.get("address", ""),
            "rating": poi.get("rating", 0),
            "tags": poi.get("tags", []),
        }

    @staticmethod
    def _add_time(time_str: str, add_min: int) -> str:
        h, m = map(int, time_str.split(":"))
        total = h * 60 + m + add_min
        return f"{total // 60:02d}:{total % 60:02d}"

    @staticmethod
    def _calc_total_min(nodes: list[dict]) -> int:
        if not nodes:
            return 0
        start_h, start_m = map(int, nodes[0]["start_time"].split(":"))
        end_h, end_m = map(int, nodes[-1]["end_time"].split(":"))
        return (end_h * 60 + end_m) - (start_h * 60 + start_m)
