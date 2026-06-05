"""
MemoryStore 测试 — 三级记忆仓库
"""
import time
import pytest
from core.memory_store import MemoryStore, MEMORY_TIERS


class TestMemoryStore:
    """三级记忆仓库测试"""

    def setup_method(self):
        self.store = MemoryStore()
        self.sid = "test_session"

    def test_put_and_get_session_fact(self):
        """写入和读取会话事实"""
        self.store.put(self.sid, "session_facts", "child_age", 5)
        assert self.store.get(self.sid, "session_facts", "child_age") == 5

    def test_put_and_get_preference(self):
        """写入和读取偏好"""
        self.store.put(self.sid, "confirmed_preferences", "cuisine", "清淡",
                       source="user_confirm")
        assert self.store.get(self.sid, "confirmed_preferences", "cuisine") == "清淡"

    def test_get_default_on_missing(self):
        """不存在的 key 返回 default"""
        assert self.store.get(self.sid, "session_facts", "nonexistent", 42) == 42

    def test_get_all_returns_all_facts(self):
        """获取所有会话事实"""
        self.store.put_batch(self.sid, "session_facts", {
            "child_age": 5, "start_time": "14:00", "scene": "family"
        })
        facts = self.store.get_all(self.sid, "session_facts")
        assert facts["child_age"] == 5
        assert facts["start_time"] == "14:00"
        assert facts["scene"] == "family"

    def test_put_batch_with_source(self):
        """批量写入带来源"""
        self.store.put_batch(self.sid, "session_facts",
                             {"key1": "v1", "key2": "v2"},
                             source="user_input", confidence=0.9)
        entry1 = self.store.get_entry(self.sid, "session_facts", "key1")
        assert entry1.source == "user_input"
        assert entry1.confidence == 0.9

    def test_promote_derived_to_confirmed(self):
        """提升 derived → confirmed"""
        self.store.put(self.sid, "derived_preferences",
                       "cuisine", "清淡", confidence=0.6)
        self.store.promote(self.sid, "derived_preferences",
                           "confirmed_preferences", "cuisine")

        # 原层级已删除
        assert self.store.get(self.sid, "derived_preferences", "cuisine") is None
        # 新层级存在且置信度升至 1.0
        entry = self.store.get_entry(self.sid, "confirmed_preferences", "cuisine")
        assert entry is not None
        assert entry.value == "清淡"
        assert entry.confidence == 1.0

    def test_clear_session_removes_all(self):
        """清理会话 — 所有层级都被清除"""
        self.store.put(self.sid, "session_facts", "a", 1)
        self.store.put(self.sid, "confirmed_preferences", "b", 2)
        self.store.put(self.sid, "derived_preferences", "c", 3)

        self.store.clear_session(self.sid)

        assert self.store.get(self.sid, "session_facts", "a") is None
        assert self.store.get(self.sid, "confirmed_preferences", "b") is None
        assert self.store.get(self.sid, "derived_preferences", "c") is None

    def test_expired_entry(self):
        """过期条目自动失效"""
        self.store.put(self.sid, "session_facts", "temp", "value",
                       ttl_seconds=0)  # 立即过期
        time.sleep(0.01)
        assert self.store.get(self.sid, "session_facts", "temp") is None

    def test_not_expired_entry(self):
        """未过期条目正常返回"""
        self.store.put(self.sid, "session_facts", "temp", "value",
                       ttl_seconds=3600)
        assert self.store.get(self.sid, "session_facts", "temp") == "value"

    def test_clear_expired_removes_only_expired(self):
        """清理过期只删过期"""
        self.store.put(self.sid, "session_facts", "fresh", "stay",
                       ttl_seconds=3600)
        self.store.put(self.sid, "session_facts", "stale", "go",
                       ttl_seconds=0)
        time.sleep(0.01)
        self.store.clear_expired()
        facts = self.store.get_all(self.sid, "session_facts")
        assert "fresh" in facts
        assert "stale" not in facts

    def test_to_dict_for_llm_context(self):
        """序列化为 LLM 可用的上下文"""
        self.store.put(self.sid, "session_facts", "child_age", 5,
                       source="user_input")
        self.store.put(self.sid, "confirmed_preferences", "cuisine", "清淡",
                       source="user_confirm")

        context = self.store.to_dict(self.sid)
        assert "session_facts" in context
        assert "confirmed_preferences" in context
        assert context["session_facts"]["child_age"]["value"] == 5

    def test_summary_format(self):
        """摘要格式包含层级信息"""
        self.store.put(self.sid, "session_facts", "age", 5)
        summary = self.store.summary(self.sid)
        assert "session_facts" in summary
        assert "age=5" in summary

    def test_empty_summary(self):
        """空会话返回 (空)"""
        assert self.store.summary("nonexistent") == "(空)"

    def test_has_tier(self):
        """检查层级是否有数据"""
        assert not self.store.has_tier(self.sid, "session_facts")
        self.store.put(self.sid, "session_facts", "a", 1)
        assert self.store.has_tier(self.sid, "session_facts")

    def test_delete_removes_entry(self):
        """删除单条"""
        self.store.put(self.sid, "session_facts", "x", 1)
        assert self.store.get(self.sid, "session_facts", "x") == 1
        self.store.delete(self.sid, "session_facts", "x")
        assert self.store.get(self.sid, "session_facts", "x") is None

    def test_clear_tier(self):
        """清理特定层级"""
        self.store.put(self.sid, "session_facts", "a", 1)
        self.store.put(self.sid, "confirmed_preferences", "b", 2)
        self.store.clear_tier(self.sid, "session_facts")
        assert not self.store.has_tier(self.sid, "session_facts")
        assert self.store.has_tier(self.sid, "confirmed_preferences")

    def test_invalid_tier_raises(self):
        """无效层级名抛出异常"""
        with pytest.raises(ValueError, match="无效的记忆层级"):
            self.store.put(self.sid, "invalid_tier", "k", "v")

    def test_nonexistent_session_summary(self):
        """不存在的会话返回空摘要"""
        assert self.store.summary("no_such_session") == "(空)"

    def test_list_sessions(self):
        """列出所有会话"""
        self.store.put("s1", "session_facts", "a", 1)
        self.store.put("s2", "session_facts", "b", 2)
        sessions = self.store.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions
