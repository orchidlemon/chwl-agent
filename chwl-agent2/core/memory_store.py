"""
MemoryStore — 三级记忆仓库

设计依据（赛题补充设计 2.2 节）：
用户输入中的信息必须分三级存储，不能混为一谈：

- session_facts：本次会话事实
  - 来源：用户输入、前端询问回答
  - 特征：高置信度，会话结束后可丢弃
  - 示例：{"child_age": 5, "start_time": "14:00", "wife_on_diet": true}

- confirmed_preferences：用户授权保存的长期偏好
  - 来源：用户显式确认 "记住我的偏好"
  - 特征：跨会话持久化
  - 示例：{"cuisine": "清淡", "transport": "taxi", "avoid_queue": true}

- derived_preferences：Agent 从事件中提取的临时倾向
  - 来源：用户行为观察、历史模式
  - 特征：低置信度，需要用户确认后才升级到 confirmed
  - 示例：{"might_like_indoor": 0.7, "seems_to_prefer_light_food": 0.6}

核心原则（赛题补充设计 "核心边界" 节）：
"记忆来自用户输入、前端询问、用户确认或事件提取，不能凭空预置。"
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

MEMORY_TIERS = ("session_facts", "confirmed_preferences", "derived_preferences")


@dataclass
class MemoryEntry:
    """一条记忆条目"""
    key: str
    value: Any
    confidence: float = 1.0          # 置信度 0-1
    source: str = ""                 # 来源: "user_input" | "clarification" | "inference" | "user_confirm"
    timestamp: float = 0.0
    ttl_seconds: Optional[int] = None  # None = 不过期

    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        return time.time() - self.timestamp > self.ttl_seconds


class MemoryStore:
    """
    三级记忆仓库。

    用法:
        store = MemoryStore()

        # 写入会话事实
        store.put("session_001", "session_facts", "child_age", 5)
        store.put("session_001", "session_facts", "start_time", "14:00",
                  source="user_clarification")
        store.put("session_001", "session_facts", "wife_on_diet", True,
                  source="user_input")

        # 批量写入（从 LLM 解析结果）
        facts = {"child_age": 5, "start_time": "14:00", "scene": "family"}
        store.put_batch("session_001", "session_facts", facts,
                        source="preference_extraction")

        # 读取
        age = store.get("session_001", "session_facts", "child_age")
        all_facts = store.get_all("session_001", "session_facts")

        # 提升置信度（derived → confirmed 需用户确认）
        store.promote("session_001", "derived_preferences",
                      "confirmed_preferences", "cuisine_preference")

        # 清理会话
        store.clear_session("session_001")
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._store: dict[str, dict[str, dict[str, MemoryEntry]]] = {
            # {session_id: {tier: {key: MemoryEntry}}}
        }
        self._persist_path = persist_path
        if persist_path and os.path.exists(persist_path):
            self._load()

    # ── 写入 ──

    def put(self, session_id: str, tier: str, key: str, value: Any,
            confidence: float = 1.0, source: str = "",
            ttl_seconds: Optional[int] = None):
        """写入一条记忆"""
        if tier not in MEMORY_TIERS:
            raise ValueError(f"无效的记忆层级: {tier}, 可选: {MEMORY_TIERS}")

        if session_id not in self._store:
            self._store[session_id] = {t: {} for t in MEMORY_TIERS}
        if tier not in self._store[session_id]:
            self._store[session_id][tier] = {}

        self._store[session_id][tier][key] = MemoryEntry(
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            timestamp=time.time(),
            ttl_seconds=ttl_seconds,
        )
        logger.debug("[Memory] %s/%s/%s = %s (%.1f)",
                     session_id[:12], tier, key, value, confidence)

    def put_batch(self, session_id: str, tier: str,
                  facts: dict[str, Any], source: str = "",
                  confidence: float = 1.0):
        """批量写入多条记忆"""
        for key, value in facts.items():
            self.put(session_id, tier, key, value,
                     confidence=confidence, source=source)

    # ── 读取 ──

    def get(self, session_id: str, tier: str, key: str,
            default: Any = None) -> Any:
        """读取单条记忆值，过期或不存在返回 default"""
        entry = self._get_entry(session_id, tier, key)
        if entry is None or entry.is_expired():
            return default
        return entry.value

    def get_entry(self, session_id: str, tier: str, key: str
                  ) -> Optional[MemoryEntry]:
        """读取完整的 MemoryEntry"""
        entry = self._get_entry(session_id, tier, key)
        if entry and entry.is_expired():
            return None
        return entry

    def _get_entry(self, session_id: str, tier: str, key: str
                   ) -> Optional[MemoryEntry]:
        return self._store.get(session_id, {}).get(tier, {}).get(key)

    def get_all(self, session_id: str, tier: str) -> dict[str, Any]:
        """读取某个层级的所有记忆（自动过滤过期）"""
        entries = self._store.get(session_id, {}).get(tier, {})
        result = {}
        for key, entry in entries.items():
            if not entry.is_expired():
                result[key] = entry.value
        return result

    def get_all_entries(self, session_id: str, tier: str
                        ) -> dict[str, MemoryEntry]:
        """读取完整的 MemoryEntry 列表"""
        entries = self._store.get(session_id, {}).get(tier, {})
        return {k: v for k, v in entries.items() if not v.is_expired()}

    # ── 查询 ──

    def exists(self, session_id: str, tier: str, key: str) -> bool:
        return self.get(session_id, tier, key) is not None

    def has_tier(self, session_id: str, tier: str) -> bool:
        return len(self.get_all(session_id, tier)) > 0

    def list_sessions(self) -> list[str]:
        return list(self._store.keys())

    # ── 层级提升 ──

    def promote(self, session_id: str, from_tier: str, to_tier: str, key: str):
        """
        将一条记忆从低置信层级提升到高置信层级。

        典型场景：
        - derived → confirmed：用户确认了某个临时偏好
        - session_facts → confirmed：用户说 "记住这个"
        """
        entry = self._get_entry(session_id, from_tier, key)
        if entry is None:
            raise KeyError(f"记忆不存在: {session_id}/{from_tier}/{key}")

        self.put(session_id, to_tier, key, entry.value,
                 confidence=1.0,
                 source=f"promoted_from_{from_tier}",
                 ttl_seconds=entry.ttl_seconds)
        # 从原层级删除
        self.delete(session_id, from_tier, key)
        logger.info("[Memory] 提升 %s/%s -> %s (key=%s)",
                    session_id[:12], from_tier, to_tier, key)

    def delete(self, session_id: str, tier: str, key: str):
        """删除一条记忆"""
        self._store.get(session_id, {}).get(tier, {}).pop(key, None)

    # ── 清理 ──

    def clear_session(self, session_id: str):
        """清理会话事实（会话结束调用）"""
        self._store.pop(session_id, None)
        logger.debug("[Memory] 清理会话: %s", session_id[:12])

    def clear_tier(self, session_id: str, tier: str):
        """清理某个层级"""
        self._store.get(session_id, {}).pop(tier, None)

    def clear_expired(self):
        """清理所有过期记忆"""
        now = time.time()
        for sid in list(self._store.keys()):
            for tier in MEMORY_TIERS:
                entries = self._store[sid].get(tier, {})
                expired = [k for k, v in entries.items()
                           if v.ttl_seconds and (now - v.timestamp) > v.ttl_seconds]
                for k in expired:
                    del entries[k]

    # ── 序列化 ──

    def to_dict(self, session_id: str) -> dict:
        """将会话的所有记忆序列化为 dict（用于 LLM 上下文注入）"""
        result = {}
        for tier in MEMORY_TIERS:
            entries = self.get_all_entries(session_id, tier)
            if entries:
                result[tier] = {
                    k: {
                        "value": v.value,
                        "confidence": v.confidence,
                        "source": v.source,
                    }
                    for k, v in entries.items()
                }
        return result

    def summary(self, session_id: str) -> str:
        """生成人类可读的记忆摘要（用于 Prompt 注入）"""
        parts = []
        for tier in MEMORY_TIERS:
            entries = self.get_all(session_id, tier)
            if entries:
                items = [f"{k}={v}" for k, v in entries.items()]
                parts.append(f"[{tier}] {', '.join(items)}")
        return " | ".join(parts) if parts else "(空)"

    # ── 持久化 ──

    def _serialize(self) -> dict:
        """序列化所有记忆（用于持久化）"""
        result = {}
        for sid, tiers in self._store.items():
            result[sid] = {}
            for tier, entries in tiers.items():
                result[sid][tier] = {
                    k: {
                        "value": v.value,
                        "confidence": v.confidence,
                        "source": v.source,
                        "timestamp": v.timestamp,
                        "ttl_seconds": v.ttl_seconds,
                    }
                    for k, v in entries.items()
                }
        return result

    @classmethod
    def _deserialize(cls, data: dict) -> dict:
        """反序列化"""
        store = {}
        for sid, tiers in data.items():
            store[sid] = {}
            for tier, entries in tiers.items():
                store[sid][tier] = {
                    k: MemoryEntry(
                        key=k,
                        value=v["value"],
                        confidence=v.get("confidence", 1.0),
                        source=v.get("source", ""),
                        timestamp=v.get("timestamp", 0),
                        ttl_seconds=v.get("ttl_seconds"),
                    )
                    for k, v in entries.items()
                }
        return store

    def save(self, path: Optional[str] = None):
        """持久化到磁盘"""
        target = path or self._persist_path
        if not target:
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self._serialize(), f, ensure_ascii=False, indent=2)
        logger.info("[Memory] 已持久化到 %s", target)

    def _load(self):
        """从磁盘加载"""
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._store = self._deserialize(data)
            logger.info("[Memory] 已从 %s 加载 %d 个会话",
                        self._persist_path, len(self._store))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("[Memory] 加载失败: %s", e)
            self._store = {}
