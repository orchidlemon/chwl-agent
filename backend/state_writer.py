"""
State Writer — 管理每个 session 的临时 JSON 状态文件。

设计原则:
  - 每个 session 对应一个 JSON 文件，路径由 session_id 决定
  - 文件内容是前端渲染所需的完整状态，后端确认后前端 GET /state 拉取
  - 用户画像（user_profile）只包含 LLM 从自然语言中提取的字段，无预设默认值
  - Tool fallback 默认值不写入此文件

文件格式:
{
  "session_id": str,
  "phase": str,
  "updated_at": float,
  "itinerary": {
    "summary": str,
    "cot": [str],
    "nodes": [ItineraryNode]
  },
  "user_profile": {
    "session_facts": dict,       # 仅 LLM 从 NL 提取的字段
    "confirmed_preferences": dict,
    "confirmed_lines": [str],    # 人类可读的确认摘要
    "applied_rules": [str]       # 本次规划调用了哪些 rule 工具
  },
  "monitor": {
    "weather": dict,
    "alerts": [dict],
    "queue_trends": dict
  },
  "planning_log": {
    "rule_calls": [{tool, args_summary, result_summary}],
    "cot_steps": [str]
  }
}
"""
from __future__ import annotations
import json
import logging
import os
import re
import tempfile
import time

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "meituan_state")


def _state_path(session_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(session_id))[:80]
    return os.path.join(_CACHE_DIR, f"state_{safe}.json")


def _ensure_dir() -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)


# ── 读写 ──────────────────────────────────────────────────────────────

def read_state(session_id: str) -> dict:
    """读取当前 session 状态，不存在返回空骨架。"""
    try:
        with open(_state_path(session_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return _empty_state(session_id)
    except Exception as e:
        logger.warning(f"[StateWriter] read failed for {session_id}: {e}")
        return _empty_state(session_id)


def write_state(session_id: str, patch: dict) -> dict:
    """
    合并写入状态。patch 中存在的 key 覆盖，不存在的 key 保留原值。
    写入完成后返回完整 state。
    """
    _ensure_dir()
    current = read_state(session_id)
    merged = _deep_merge(current, patch)
    merged["updated_at"] = time.time()
    merged["session_id"] = session_id

    path = _state_path(session_id)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        logger.error(f"[StateWriter] write failed for {session_id}: {e}")
    return merged


def update_phase(session_id: str, phase: str) -> None:
    write_state(session_id, {"phase": phase})


def update_itinerary(session_id: str, nodes: list[dict],
                     summary: str = "", cot: list[str] | None = None) -> None:
    write_state(session_id, {
        "itinerary": {
            "summary": summary,
            "cot": cot or [],
            "nodes": nodes,
        }
    })


def update_user_profile(session_id: str,
                        session_facts: dict,
                        confirmed_preferences: dict,
                        confirmed_lines: list[str] | None = None,
                        applied_rules: list[str] | None = None) -> None:
    """
    更新用户画像。
    session_facts 只传 LLM 从自然语言中提取的字段，无默认填充。
    """
    write_state(session_id, {
        "user_profile": {
            "session_facts": session_facts,
            "confirmed_preferences": confirmed_preferences,
            "confirmed_lines": confirmed_lines or [],
            "applied_rules": applied_rules or [],
        }
    })


def update_monitor(session_id: str, weather: dict | None = None,
                   alerts: list[dict] | None = None,
                   queue_trends: dict | None = None) -> None:
    current = read_state(session_id)
    mon = dict(current.get("monitor") or {})
    if weather is not None:
        mon["weather"] = weather
    if alerts is not None:
        mon["alerts"] = alerts
    if queue_trends is not None:
        mon["queue_trends"] = queue_trends
    write_state(session_id, {"monitor": mon})


def append_rule_call(session_id: str, tool_name: str,
                     args_summary: str, result_summary: str) -> None:
    """记录 rule 工具调用日志，供前端 planning_log 展示。"""
    current = read_state(session_id)
    log = dict(current.get("planning_log") or {})
    calls = list(log.get("rule_calls") or [])
    calls.append({
        "tool": tool_name,
        "args_summary": args_summary,
        "result_summary": result_summary,
        "ts": time.time(),
    })
    log["rule_calls"] = calls[-20:]  # 最多保留20条
    write_state(session_id, {"planning_log": log})


def append_cot_step(session_id: str, step: str) -> None:
    current = read_state(session_id)
    log = dict(current.get("planning_log") or {})
    steps = list(log.get("cot_steps") or [])
    steps.append(step)
    log["cot_steps"] = steps[-30:]
    write_state(session_id, {"planning_log": log})


def clear_state(session_id: str) -> None:
    """重置 session 状态（用于 /reset 接口）。"""
    path = _state_path(session_id)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    write_state(session_id, _empty_state(session_id))


# ── 内部工具 ──────────────────────────────────────────────────────────

def _empty_state(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "phase": "gathering",
        "updated_at": time.time(),
        "itinerary": {
            "summary": "",
            "cot": [],
            "nodes": [],
        },
        "user_profile": {
            "session_facts": {},
            "confirmed_preferences": {},
            "confirmed_lines": [],
            "applied_rules": [],
        },
        "monitor": {
            "weather": {},
            "alerts": [],
            "queue_trends": {},
        },
        "planning_log": {
            "rule_calls": [],
            "cot_steps": [],
        },
    }


def _deep_merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
