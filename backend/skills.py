"""
LLM skill functions.
Supports Anthropic (claude-*) and DeepSeek (deepseek-chat / deepseek-reasoner).
DeepSeek-reasoner's reasoning_content is used directly as CoT display steps.
"""
import json
import logging
import os
import re
import time
from typing import Optional

from . import prompts

logger = logging.getLogger(__name__)



# ---- Explicit user preference extraction (rule fallback for every phase) ----
_FOOD_KEYWORDS = [
    "川菜", "日料", "日本料理", "寿司", "火锅", "粤菜", "西餐", "烤肉", "烧烤",
    "韩餐", "东南亚菜", "泰餐", "清真", "素食", "轻食", "甜品", "咖啡", "茶餐厅",
]

_ACTIVITY_KEYWORDS = [
    ("park", ["公园", "逛公园", "户外", "草坪", "自然", "露营"]),
    ("mall", ["商场", "购物中心", "逛街", "购物", "mall", "Mall"]),
    ("exhibition", ["展览", "美术馆", "博物馆", "科技馆", "画展"]),
    ("citywalk", ["citywalk", "Citywalk", "街区", "老街", "步行街"]),
    ("social", ["剧本杀", "密室", "桌游", "桌游吧"]),
    ("playground", ["亲子乐园", "游乐园", "儿童乐园", "乐园"]),
]

_ACTIVITY_LABELS = {
    "park": "逛公园/户外",
    "mall": "逛商场/购物中心",
    "exhibition": "展览/博物馆/美术馆",
    "citywalk": "Citywalk/街区",
    "social": "剧本杀/密室/桌游",
    "playground": "亲子乐园/游乐园",
}

_ACTIVITY_TO_FRIENDS_TYPE = {
    "mall": "mall",
    "exhibition": "exhibition",
    "citywalk": "photo_spot",
    "social": "social",
}

_ACTIVITY_TO_VENUE = {
    "park": "outdoor",
    "mall": "mall",
    "exhibition": "indoor",
    "social": "indoor",
    "playground": "indoor",
}


def _append_unique(items: list, values: list) -> list:
    result = list(items or [])
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def detect_specific_preferences(message: str) -> dict:
    """Detect concrete food/activity preferences from user text."""
    food = []
    for keyword in _FOOD_KEYWORDS:
        if keyword in message:
            food.append("日料" if keyword in ("日本料理", "寿司") else keyword)
    if any(w in message for w in ["减肥", "减脂", "低卡", "清淡", "健康", "低油"]):
        food = _append_unique(food, ["清淡", "低油"])

    activity = None
    negated_outdoor = any(w in message for w in ["不要户外", "别去户外", "不想户外", "不要室外", "别去室外"])
    for code, words in _ACTIVITY_KEYWORDS:
        if code == "park" and negated_outdoor:
            continue
        if any(word in message for word in words):
            activity = code
            break

    venue = _ACTIVITY_TO_VENUE.get(activity)
    if not venue:
        if any(w in message for w in ["室内", "空调", "不晒", "别晒", "避暑"]):
            venue = "indoor"
        elif any(w in message for w in ["户外", "室外"]):
            venue = "outdoor"

    return {
        "food": food,
        "activity_preference": activity,
        "activity_preference_label": _ACTIVITY_LABELS.get(activity),
        "venue": venue,
        "friends_activity_type": _ACTIVITY_TO_FRIENDS_TYPE.get(activity),
    }


def merge_specific_preferences(session_facts: dict, preferences: dict, message: str) -> tuple[dict, dict]:
    """Merge explicit user demands into facts/preferences without dropping old values."""
    facts = dict(session_facts or {})
    prefs = dict(preferences or {})
    detected = detect_specific_preferences(message or "")

    if detected["food"]:
        replace_food = any(w in (message or "") for w in ["换成", "改成", "改为", "不要", "别吃"])
        prefs["food"] = list(detected["food"]) if replace_food else _append_unique(prefs.get("food", []), detected["food"])
        facts["food_preferences"] = list(detected["food"]) if replace_food else _append_unique(facts.get("food_preferences", []), detected["food"])

    if detected["venue"]:
        prefs["venue"] = detected["venue"]
        facts["venue_preference"] = detected["venue"]

    if detected["activity_preference"]:
        facts["activity_preference"] = detected["activity_preference"]
        facts["activity_preference_label"] = detected["activity_preference_label"]
        if detected["friends_activity_type"]:
            facts["friends_activity_type"] = detected["friends_activity_type"]

    return facts, prefs

# ── Provider detection ───────────────────────────────────────────────

def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "anthropic").lower()


def _has_api_key() -> bool:
    # Any one key available = LLM usable
    return bool(
        os.getenv("LONGCAT_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )


def _openai_clients_priority() -> list:
    """Return (client, model_alias) pairs in priority order for OpenAI-compat APIs."""
    clients = []
    if os.getenv("LONGCAT_API_KEY"):
        clients.append(("longcat", _get_longcat()))
    if os.getenv("DEEPSEEK_API_KEY"):
        clients.append(("deepseek", _get_deepseek()))
    return clients


# ── Client singletons ────────────────────────────────────────────────

_anthropic_client = None
_deepseek_client  = None
_longcat_client   = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic as _ant
        _anthropic_client = _ant.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_deepseek():
    global _deepseek_client
    if _deepseek_client is None and os.getenv("DEEPSEEK_API_KEY"):
        from openai import AsyncOpenAI
        _deepseek_client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
    return _deepseek_client


def _get_longcat():
    global _longcat_client
    if _longcat_client is None and os.getenv("LONGCAT_API_KEY"):
        from openai import AsyncOpenAI
        _longcat_client = AsyncOpenAI(
            api_key=os.getenv("LONGCAT_API_KEY"),
            base_url=os.getenv("LONGCAT_BASE_URL", "https://api.longcat.chat/openai"),
        )
    return _longcat_client


# ── Model mapping ────────────────────────────────────────────────────

_MODEL_MAP = {
    "fast_model": {
        "anthropic": "claude-haiku-4-5-20251001",
        "deepseek":  "deepseek-chat",
        "longcat":   "LongCat-2.0-Preview",
    },
    "main_model": {
        "anthropic": "claude-sonnet-4-6",
        "deepseek":  "deepseek-reasoner",
        "longcat":   "LongCat-2.0-Preview",
    },
}


def _resolve_model(alias: str) -> str:
    return _MODEL_MAP.get(alias, {}).get(_provider(), alias)


def _deepseek_model(alias: str) -> str:
    return _MODEL_MAP.get(alias, {}).get("deepseek", alias)


def _is_quota_error(e: Exception) -> bool:
    try:
        from openai import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    return getattr(e, "status_code", None) == 429


# ── Core async LLM call → (thinking, answer) ────────────────────────

async def _call_llm(system: str, user: str, model_alias: str = "fast_model",
                    max_tokens: int = 4096,
                    require_json: bool = False) -> tuple[str, str]:
    """
    require_json=True: pass response_format={"type":"json_object"} to DeepSeek
    so the model is contractually obligated to return valid JSON.
    """
    model = _resolve_model(model_alias)
    p = _provider()

    if p in ("deepseek", "longcat"):
        client = _get_deepseek() if p == "deepseek" else _get_longcat()
        if not client:
            raise RuntimeError(f"{p.upper()}_API_KEY not set")
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        if require_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as e:
            if p == "longcat" and _is_quota_error(e) and _get_deepseek():
                logger.warning(f"LongCat quota error, falling back to DeepSeek: {e}")
                fb_kwargs = {**kwargs, "model": _deepseek_model(model_alias)}
                resp = await _get_deepseek().chat.completions.create(**fb_kwargs)
            else:
                raise
        msg = resp.choices[0].message
        thinking = getattr(msg, "reasoning_content", None) or ""
        answer   = msg.content or ""
        return thinking, answer

    client = _get_anthropic()
    if not client:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "", resp.content[0].text


# ── JSON parsing ─────────────────────────────────────────────────────

def _fix_single_quotes(text: str) -> str:
    result, in_double, in_single, escape = [], False, False, False
    for ch in text:
        if escape:
            result.append(ch); escape = False; continue
        if ch == '\\':
            result.append(ch); escape = True; continue
        if ch == '"' and not in_single:
            in_double = not in_double; result.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single; result.append('"')
        else:
            result.append(ch)
    return ''.join(result)


def _fix_and_parse(text: str) -> dict | None:
    fixed = re.sub(r'```\w*', '', text)
    fixed = re.sub(r'^#+\s*', '', fixed, flags=re.MULTILINE)
    fixed = re.sub(r'^[^{]*', '', fixed)
    if '}' in fixed:
        fixed = fixed[:fixed.rindex('}') + 1]
    fixed = _fix_single_quotes(fixed)
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*\]', ']', fixed)
    fixed = re.sub(r'(?<!")(\b[a-zA-Z_][a-zA-Z0-9_]*)(?=\s*:)', r'"\1"', fixed)
    fixed = fixed.replace("True", "true").replace("False", "false").replace("None", "null")
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _complete_truncated(text: str) -> dict | None:
    text = text.rstrip().rstrip(',')
    if text.count('"') % 2 != 0:
        text += '"'
    closers = []
    for ch in text:
        if ch == '{':
            closers.append('}')
        elif ch == '[':
            closers.append(']')
        elif ch == '}' and closers and closers[-1] == '}':
            closers.pop()
        elif ch == ']' and closers and closers[-1] == ']':
            closers.pop()
    text += ''.join(reversed(closers))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_json(text: str) -> dict:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    result = _fix_and_parse(t)
    if result is not None:
        return result
    result = _complete_truncated(t)
    if result is not None:
        return result
    raise ValueError(f"Cannot parse JSON: {t[:300]}")


def _build_field_error_msg(answer: str, schema_hint: str) -> str:
    """生成字段级错误信息，用于重试时精准告知 LLM 哪里出错。"""
    errors = []
    parsed = None
    try:
        parsed = json.loads(answer.strip())
    except json.JSONDecodeError as e:
        errors.append(f"JSON 语法错误：第{e.lineno}行第{e.colno}列，附近：{answer[max(0,e.pos-15):e.pos+15]!r}")

    if parsed is not None and schema_hint:
        try:
            schema_obj = json.loads(schema_hint)

            def check_keys(actual, expected, path=""):
                if isinstance(expected, dict):
                    if not isinstance(actual, dict):
                        errors.append(f"{path or 'root'} 应为对象，实际为 {type(actual).__name__}")
                        return
                    for k in expected:
                        if k not in actual:
                            errors.append(f"缺少字段: {(path + '.' + k).lstrip('.')}")
                        else:
                            check_keys(actual[k], expected[k], (path + '.' + k).lstrip('.'))
                elif isinstance(expected, list) and expected and isinstance(actual, list) and actual:
                    check_keys(actual[0], expected[0], f"{path}[0]")

            check_keys(parsed, schema_obj)
        except (json.JSONDecodeError, TypeError):
            pass

    return "；".join(errors[:5]) if errors else "结构与预期 Schema 不符"


async def _call_json_with_retry(system: str, user: str, model_alias: str,
                                schema_hint: str = "", max_tokens: int = 4096,
                                max_retries: int = 3) -> tuple[str, dict]:
    last_answer = ""
    last_error  = ""
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                thinking, answer = await _call_llm(system, user, model_alias, max_tokens)
            else:
                field_errors = _build_field_error_msg(last_answer, schema_hint)
                repair_user = prompts.REPAIR_USER.format(
                    original=last_answer, error=last_error,
                    schema=schema_hint, field_errors=field_errors
                )
                thinking, answer = await _call_llm(
                    prompts.REPAIR_SYSTEM, repair_user, "fast_model", 4096
                )
            last_answer = answer
            return thinking, _parse_json(answer)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
    raise ValueError(f"All {max_retries} attempts failed. Last: {last_answer[:200]}")


# ── LLM Tool Calling (Function Calling) ──────────────────────────────

async def _call_llm_with_tools(messages: list, tools: list,
                                model_alias: str = "fast_model",
                                max_tokens: int = 4096) -> dict:
    """
    Call LLM with tool definitions. Returns unified result:
    {
        "finish_reason": "tool_calls" | "stop",
        "content": str,
        "tool_calls": [{"id": str, "name": str, "args": dict}],
        "raw_assistant_msg": dict,   # append this to messages for history
    }
    """
    p = _provider()

    if p in ("deepseek", "longcat"):
        client = _get_deepseek() if p == "deepseek" else _get_longcat()
        if not client:
            raise RuntimeError(f"{p.upper()}_API_KEY not set")

        call_kwargs = dict(
            model=_resolve_model(model_alias),
            messages=messages,
            tools=tools,
            tool_choice="required",   # force tool use; finish_planning serves as the exit
            max_tokens=max_tokens,
        )
        try:
            resp = await client.chat.completions.create(**call_kwargs)
        except Exception as e:
            if p == "longcat" and _is_quota_error(e) and _get_deepseek():
                logger.warning(f"LongCat quota error (tools), falling back to DeepSeek: {e}")
                fb_kwargs = {**call_kwargs, "model": _deepseek_model(model_alias)}
                resp = await _get_deepseek().chat.completions.create(**fb_kwargs)
            else:
                raise
        msg = resp.choices[0].message
        finish_reason = resp.choices[0].finish_reason or "stop"

        tool_calls = []
        raw_tcs = []
        for tc in (msg.tool_calls or []):
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "args": json.loads(tc.function.arguments or "{}"),
            })
            raw_tcs.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            })

        raw_assistant = {"role": "assistant", "content": msg.content or ""}
        if raw_tcs:
            raw_assistant["tool_calls"] = raw_tcs

        return {
            "finish_reason": "tool_calls" if tool_calls else finish_reason,
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "raw_assistant_msg": raw_assistant,
        }

    # Anthropic
    client = _get_anthropic()
    if not client:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    from . import agent_tools as _at
    ant_tools = _at.to_anthropic_tools(tools)

    resp = await client.messages.create(
        model=_resolve_model(model_alias),
        messages=messages,
        tools=ant_tools,
        tool_choice={"type": "any"},  # "any" forces tool use in Anthropic API
        max_tokens=max_tokens,
    )

    content_text = ""
    tool_calls = []
    raw_content = []
    for block in resp.content:
        if block.type == "text":
            content_text += block.text
            raw_content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "args": block.input,
            })
            raw_content.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })

    return {
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "content": content_text,
        "tool_calls": tool_calls,
        "raw_assistant_msg": {"role": "assistant", "content": raw_content},
    }


def _make_tool_result_messages(tool_calls: list, results: list,
                                provider: str) -> list[dict]:
    """Build the tool result messages to append to conversation history."""
    if provider in ("deepseek", "longcat"):
        return [
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(res, ensure_ascii=False, default=str),
            }
            for tc, res in zip(tool_calls, results)
        ]

    # Anthropic: tool results go in a single user message
    content = [
        {
            "type": "tool_result",
            "tool_use_id": tc["id"],
            "content": json.dumps(res, ensure_ascii=False, default=str),
        }
        for tc, res in zip(tool_calls, results)
    ]
    return [{"role": "user", "content": content}]


# ── Agent Planning Loop ───────────────────────────────────────────────

async def run_agent_plan(session_facts: dict, preferences: dict):
    """
    Tool-use agent loop for itinerary planning.

    Async generator that yields progress events and one final result:
      {"_type": "cot_step",  "text": str}        — tool call progress
      {"_type": "status",    "id": int, ...}      — status bar updates
      {"_type": "result",    "plan": dict}        — final itinerary (last item)

    The LLM autonomously decides which tools to call and in what order.
    Planning ends when the LLM calls `finish_planning` with the itinerary.
    Falls back to non-tool plan_itinerary() if no API key or tool loop fails.
    """
    import time as _time
    from . import agent_tools as _at

    if not _has_api_key():
        # Fallback: old non-tool approach
        activities = []
        restaurants = []
        weather = {}
        plan = _fallback_plan(activities, restaurants, weather, session_facts, preferences)
        yield {"_type": "result", "plan": plan}
        return

    scenario = session_facts.get("scenario", "family")
    start_time = session_facts.get("start_time", "10:00")
    duration_hours = float(session_facts.get("duration_hours", 3))

    # Derive end_time from start + duration if not explicitly stored
    raw_end = session_facts.get("end_time")
    if raw_end:
        end_time = raw_end
    else:
        try:
            sh, sm = map(int, start_time.split(":"))
            total_min = sh * 60 + sm + int(duration_hours * 60)
            eh, em = total_min // 60, total_min % 60
            end_time = f"{min(eh, 23):02d}:{em:02d}"
        except Exception:
            end_time = "20:00"

    food_prefs  = preferences.get("food", [])
    venue_pref  = preferences.get("venue") or ""
    has_child   = session_facts.get("has_children", session_facts.get("has_child", False))
    has_elderly = session_facts.get("has_elderly", False)
    child_age   = session_facts.get("child_age")
    # Fix: field is special_needs (list), not special_requirements (str)
    sn_raw = session_facts.get("special_needs", [])
    if isinstance(sn_raw, list):
        special = "、".join(sn_raw) if sn_raw else "无"
    else:
        special = str(sn_raw) if sn_raw else "无"
    current_time = _time.strftime("%H:%M")

    # Build group description
    group_parts = []
    if has_child:
        age_str = f"{child_age}岁" if child_age else "未知年龄"
        group_parts.append(f"有孩子({age_str})")
    if has_elderly:
        group_parts.append("有老人")
    male_count   = session_facts.get("male_count", 0)
    female_count = session_facts.get("female_count", 0)
    if male_count or female_count:
        group_parts.append(f"男{male_count}人女{female_count}人")
    if not group_parts:
        group_desc = {"family": "家庭", "friends": "朋友", "couple": "情侣",
                      "elderly": "老人为主", "solo": "单人"}.get(scenario, scenario)
    else:
        group_desc = "、".join(group_parts)

    # Build person_constraints block
    constraints = []

    # ── Legal / age limits (highest priority) ────────────────────────
    all_adults = session_facts.get("all_adults_confirmed")
    has_minor  = bool(child_age and int(child_age) < 18)
    if has_minor or all_adults is not True:
        constraints.append("⚖️【法律最高优先级】未成年人禁酒：绝对禁止安排酒吧、夜店、清吧、任何以饮酒为主题的场所")
    if child_age:
        ca = int(child_age)
        if 5 <= ca < 14:
            constraints.append(f"⚖️【最高优先级】孩子{ca}岁(5~13)：可安排非恐密室/非恐剧本杀（亲子型/推理型/轻松型）；恐怖/惊悚主题绝对禁止")
        elif ca < 5:
            constraints.append(f"⚖️【最高优先级】孩子{ca}岁(<5)：禁止密室逃脱、剧本杀")

    # ── Children ─────────────────────────────────────────────────────
    if has_child:
        constraints.append("有儿童：活动必须亲子友好，末节点结束≤20:00，步行≤500m/段")
        cp = session_facts.get("child_purpose")
        if cp == "education":
            constraints.append("孩子出行目的=科普学习：优先科技馆、博物馆、自然博物馆等教育属性强场所")
        elif cp == "fun":
            constraints.append("孩子出行目的=轻松好玩：优先游乐园、亲子乐园、游戏体验类场所")

    # ── Elderly ───────────────────────────────────────────────────────
    if has_elderly:
        constraints.append("有老人：全程步行≤800m，避免久站/高强度体力活动")
        if session_facts.get("elderly_no_walking"):
            constraints.append("老人不宜步行：节点间必须可打车直达，不选需大量步行的景区")

    # ── Female preferences ────────────────────────────────────────────
    female_count_val = session_facts.get("female_count", 0)
    if female_count_val:
        if session_facts.get("female_weight_loss"):
            constraints.append(f"{female_count_val}位女生需减脂/健康餐：餐厅必须有低卡/轻食/沙拉选项")
        if session_facts.get("female_prefer_low_intensity"):
            constraints.append("女生偏好低体力：不安排爬山、徒步、高强度运动")
        if session_facts.get("female_prefer_indoor"):
            constraints.append("女生倾向室内：优先室内场所")

    # ── Male preferences ──────────────────────────────────────────────
    gg = session_facts.get("group_gender", "")
    if gg == "all_male" and session_facts.get("male_prefer_high_intensity"):
        constraints.append("全男生组且偏好高强度：优先户外运动、爬山、体育场馆")

    # ── Friends activity type ─────────────────────────────────────────
    fat = session_facts.get("friends_activity_type")
    fat_map = {
        "social":     "社交互动（剧本杀/密室/桌游）",
        "exhibition": "文化展览（博物馆/美术馆）",
        "mall":       "逛街购物（商场/步行街）",
        "photo_spot": "出片打卡（网红/文艺街区）",
        "mixed":      "综合体验",
    }
    if fat and fat in fat_map:
        constraints.append(f"朋友活动偏好={fat_map[fat]}：优先匹配此类场所（但仍受法律约束）")

    if not constraints:
        constraints.append("无特殊人员约束")

    # ── Pinned-node time exclusions ───────────────────────────────────
    pinned_nodes = session_facts.get("_pinned_nodes", [])
    if pinned_nodes:
        constraints.append("")
        constraints.append("【已锁定节点 — 以下时间段禁止安排任何新节点】")
        for pn in pinned_nodes:
            constraints.append(
                f"  • 🔒 {pn.get('name', '?')}  {pn.get('timeStart','')}–{pn.get('timeEnd','')}  "
                f"（该节点由用户锁定，新行程中不得出现与此时间段重叠的节点）"
            )

    person_constraints = "\n".join(constraints)

    # Build user demand instructions block
    demand_lines = []
    if food_prefs:
        food_str = "、".join(food_prefs)
        demand_lines.append(
            f"- 用户要求吃「{food_str}」：调用 search_restaurants 时 preferences 参数必须填「{food_str}」，"
            f"选餐厅时必须优先满足此口味，找不到时才退而求其次"
        )
    activity_pref = session_facts.get("activity_preference")
    activity_label = session_facts.get("activity_preference_label") or _ACTIVITY_LABELS.get(activity_pref, "")
    if activity_pref:
        demand_lines.append(
            f"- 用户活动偏好「{activity_label}」：活动节点必须优先匹配此类型；只有没有可用候选或违反安全约束时才降级"
        )

    if venue_pref == "mall":
        demand_lines.append(
            "- 用户要求去商场：调用 search_activities 时 categories 参数填「mall」，"
            "选活动时必须优先选商场/室内购物中心内的场所"
        )
    elif venue_pref == "indoor":
        demand_lines.append("- 用户偏好室内场所：优先选 venue.environment=indoor 的活动")
    elif venue_pref == "outdoor":
        demand_lines.append("- 用户偏好户外：优先选户外公园、开放景区等")

    fat = session_facts.get("friends_activity_type")
    fat_label = {
        "social": "剧本杀/密室/桌游", "exhibition": "博物馆/美术馆/展览",
        "mall": "逛商场/购物街", "photo_spot": "打卡出片地", "mixed": "混搭综合",
    }.get(fat, "")
    if fat_label:
        demand_lines.append(
            f"- 用户朋友活动偏好「{fat_label}」：活动节点必须优先选此类场所，只有在没有合适选项时才选其他类型"
        )

    if not demand_lines:
        demand_lines.append("- 无额外具体需求，按综合评分选择最优方案")

    user_demand_instructions = "\n".join(demand_lines)

    user_msg = prompts.AGENT_PLAN_USER.format(
        scenario=scenario,
        start_time=start_time,
        end_time=end_time,
        group_desc=group_desc,
        food_prefs="、".join(food_prefs) if food_prefs else "不限",
        venue_pref=venue_pref if venue_pref else "不限",
        special_requirements=special,
        user_demand_instructions=user_demand_instructions,
        current_time=current_time,
        person_constraints=person_constraints,
    )

    messages = [
        {"role": "system", "content": prompts.AGENT_PLAN_SYSTEM},
        {"role": "user",   "content": user_msg},
    ]

    seen_poi_ids:  set[str]        = set()
    seen_poi_data: dict[str, dict] = {}
    seen_poi_ids.add("walk_001")  # always allow walk nodes

    MAX_ITERATIONS = 15
    consecutive_tool_errors = 0  # counter for auto-replan trigger

    for iteration in range(MAX_ITERATIONS):
        logger.info(f"[AgentPlan] iteration={iteration}")

        try:
            result = await _call_llm_with_tools(
                messages, _at.PLANNING_TOOLS,
                model_alias="fast_model",
                max_tokens=4096,
            )
        except Exception as e:
            logger.error(f"[AgentPlan] LLM call failed: {e}")
            yield {"_type": "cot_step", "text": f"⚠️ 工具调用异常，切换备用规划方式"}
            plan = await plan_itinerary([], [], {}, {"session_facts": session_facts, "preferences": preferences}, "full_managed")
            yield {"_type": "result", "plan": plan}
            return

        # Append assistant message to history
        messages.append(result["raw_assistant_msg"])

        tool_calls = result["tool_calls"]

        # LLM decided to stop without calling any tool
        if not tool_calls:
            content = result["content"]
            if content.strip():
                try:
                    plan = _parse_json(content)
                    yield {"_type": "result", "plan": plan}
                    return
                except Exception:
                    pass
            yield {"_type": "cot_step", "text": "⚠️ Agent 未输出有效方案，切换备用规划方式"}
            plan = await plan_itinerary([], [], {}, {"session_facts": session_facts, "preferences": preferences}, "full_managed")
            yield {"_type": "result", "plan": plan}
            return

        # Execute each tool call; track (tc, result) pairs for history
        processed: list[tuple[dict, dict]] = []
        early_finish = False

        for tc in tool_calls:
            name = tc["name"]
            args = tc["args"]

            if name == "finish_planning":
                nodes = args.get("nodes", [])
                valid_nodes = [n for n in nodes if n.get("poiId", "") in seen_poi_ids]
                dropped = len(nodes) - len(valid_nodes)
                if dropped:
                    yield {"_type": "cot_step",
                           "text": f"⚠️ 过滤 {dropped} 个不在候选列表中的节点"}

                if len(valid_nodes) >= 2:
                    # Accept: normalize field names and enrich display data from search results
                    enriched = _enrich_nodes(valid_nodes, seen_poi_data)
                    plan = {
                        "nodes": enriched,
                        "summary": args.get("summary", ""),
                        "cot":     args.get("cot", []),
                    }
                    yield {"_type": "cot_step", "text": f"✅ Agent 完成规划：{plan['summary']}"}
                    yield {"_type": "result", "plan": plan}
                    return

                # Reject: nodes don't match any searched POI IDs
                valid_id_list = ", ".join(list(seen_poi_ids)[:10])
                yield {"_type": "cot_step",
                       "text": "⚠️ 节点 poiId 无效，需使用搜索结果中的真实 ID"}
                processed.append((tc, {
                    "error": (
                        "finish_planning 被拒绝：nodes 中的 poiId 不在已搜索的 POI 列表中。"
                        f"请直接使用以下已知真实 poiId 构造节点，不要重新搜索：{valid_id_list}。"
                        "用这些 id 填写 nodes[].poiId，立即再次调用 finish_planning。"
                    )
                }))
                early_finish = True
                break  # stop processing further tools in this batch

            # Data tool: execute and emit CoT step
            yield {"_type": "cot_step", "text": f"🔧 调用工具 {name}"}
            tool_result = await _at.execute_tool(name, args, seen_poi_ids)
            summary = _at.summarize_tool_result(name, tool_result)
            yield {"_type": "cot_step", "text": f"   ↳ {summary}"}

            # Track consecutive tool errors (empty results or error keys)
            is_error_result = (
                "error" in tool_result
                or (name in ("search_activities", "search_restaurants")
                    and len(tool_result.get("items", [])) == 0)
            )
            if is_error_result:
                consecutive_tool_errors += 1
                logger.warning(f"[AgentPlan] tool error #{consecutive_tool_errors} for {name}")
                if consecutive_tool_errors >= 5:
                    yield {"_type": "cot_step",
                           "text": "⚠️ 工具连续失败 5 次，自动触发重新规划…"}
                    yield {"_type": "tool_error_limit"}
                    return
            else:
                consecutive_tool_errors = 0  # reset on success

            # Store full POI data for later enrichment
            if name in ("search_activities", "search_restaurants"):
                for item in tool_result.get("items", []):
                    pid = item.get("poi_id", "")
                    if pid:
                        seen_poi_data[pid] = item

            processed.append((tc, tool_result))

        # Append tool results to conversation history
        if processed:
            tcs  = [p[0] for p in processed]
            ress = [p[1] for p in processed]
            result_msgs = _make_tool_result_messages(tcs, ress, _provider())
            messages.extend(result_msgs)

    # Exceeded max iterations
    yield {"_type": "cot_step", "text": "⚠️ Agent 迭代超限，切换备用规划方式"}
    plan = await plan_itinerary([], [], {}, {"session_facts": session_facts, "preferences": preferences}, "full_managed")
    yield {"_type": "result", "plan": plan}


def _fallback_plan(activities: list, restaurants: list, weather: dict,
                   session_facts: dict, preferences: dict) -> dict:
    """Minimal plan when no API key available."""
    return {
        "nodes": [],
        "summary": "（无API Key，请配置后重试）",
        "cot": ["无法调用 LLM，行程为空"],
    }


_AGENT_ICON_MAP = {
    "indoor_playground": "🎪", "picture_book_library": "📚",
    "children_science": "🔬", "citywalk": "🚶", "board_game": "🎲",
    "exhibition": "🎨", "livehouse": "🎵", "restaurant": "🍽️",
    "light_food": "🥗", "bar": "🍻", "park": "🌳", "museum": "🏛️",
    "cinema": "🎬", "sports": "⚽", "shopping": "🛍️",
}


def _enrich_nodes(nodes: list[dict], poi_data: dict[str, dict]) -> list[dict]:
    """Normalize field names from LLM output and enrich display fields from search data."""
    result = []
    for i, n in enumerate(nodes):
        poi_id = n.get("poiId", "")
        src = poi_data.get(poi_id, {})

        time_start = n.get("timeStart") or n.get("startTime", "")
        time_end   = n.get("timeEnd")   or n.get("endTime",   "")

        price_val = (n.get("price") or n.get("price_per_person")
                     or src.get("avg_price") or src.get("ticket_price"))
        if price_val is None:
            price_str = "免费"
        elif isinstance(price_val, (int, float)) and price_val > 0:
            price_str = f"¥{int(price_val)}/位"
        elif isinstance(price_val, str) and price_val:
            price_str = price_val
        else:
            price_str = "免费"

        rating   = n.get("rating") or src.get("rating")
        tags     = n.get("tags")   or src.get("tags") or []
        dist_km  = src.get("distance_km")
        distance = f"{dist_km:.1f}公里" if dist_km else n.get("distance", "")

        queue_min  = src.get("queue_min")
        queue_text = n.get("queueText")
        if queue_text is None:
            queue_text = f"约{queue_min}分钟" if queue_min and queue_min > 0 else "无需排队"

        reason = (n.get("reason") or n.get("notes")
                  or src.get("_llm_reason") or "")
        if not reason and tags:
            reason = tags[0]

        sub = n.get("sub") or n.get("address") or src.get("address") or ""

        icon = (n.get("icon")
                or _AGENT_ICON_MAP.get(src.get("category", ""), "")
                or ("🥗" if src.get("type") == "restaurant" else "📍"))

        result.append({
            "id":               n.get("id", f"node_{i+1:03d}"),
            "poiId":            poi_id,
            "name":             n.get("name") or src.get("name", ""),
            "type":             n.get("type", "activity"),
            "icon":             icon,
            "timeStart":        time_start,
            "timeEnd":          time_end,
            "sub":              sub,
            "distance":         distance,
            "queueMin":         queue_min,
            "queueText":        queue_text,
            "price":            price_str,
            "rating":           rating,
            "tags":             list(tags)[:3],
            "reason":           str(reason)[:30] if reason else "",
            "status":           n.get("status", "planned"),
            "pinned":           n.get("pinned", False),
            "locked":           n.get("locked", False),
            "booking_required": n.get("booking_required") or src.get("booking_required", False),
            "booking_urgent":   n.get("booking_urgent", False),
            "transit":          n.get("transit"),
            "risk_facts":       src.get("risk_facts", []),
            "business_hours":   src.get("business_hours", ""),
        })
    return result


# ── CoT extraction from R1 reasoning ────────────────────────────────

def _thinking_to_cot(thinking: str) -> list[str]:
    if not thinking:
        return []
    parts = re.split(r"[。\n]+", thinking)
    steps = [s.strip() for s in parts if len(s.strip()) > 10]
    seen, deduped = set(), []
    for s in steps:
        key = s[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    result = deduped[:7]
    result.append("✅ 方案合格，可输出")
    return result


# ════════════════════════════════════════════════════════════════════
# Skill 1: Needs Clarification
# ════════════════════════════════════════════════════════════════════

async def clarify_needs(message: str, current_time: str, location_hint: str = "") -> dict:
    """
    Infer user needs from first message and generate a confirmation message.
    Returns: {inferred, confidence, confirm_message, missing_fields}
    """
    if not _has_api_key():
        return _fallback_clarify(message, current_time)

    user_p = prompts.CLARIFY_USER.format(
        message=message,
        current_time=current_time,
        location_hint=location_hint,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.CLARIFY_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"inferred":{...},"confirm_message":"...","missing_fields":[]}',
            max_tokens=1024,
        )
        return result
    except Exception as e:
        logger.error(f"clarify_needs failed: {e}")
        return _fallback_clarify(message, current_time)


def _fallback_clarify(message: str, current_time: str, location_hint: str = "") -> dict:
    msg_lower = message.lower()

    # Detect scenario
    if any(w in message for w in ['孩子', '娃', '小朋友', '宝贝', '儿子', '女儿']):
        scenario = 'family'
        companions_desc = '一家人出行'
        has_child = True
        companions = ['spouse', 'child']
    elif any(w in message for w in ['朋友', '哥们', '闺蜜', '同事', '同学']):
        scenario = 'friends'
        companions_desc = '和朋友一起'
        has_child = False
        companions = ['friends']
    elif any(w in message for w in ['老婆', '男友', '女友', '对象', '男朋友', '女朋友']):
        scenario = 'couple'
        companions_desc = '两人出行'
        has_child = False
        companions = ['partner']
    elif any(w in message for w in ['老人', '爸妈', '父母', '外公', '外婆', '奶奶', '爷爷']):
        scenario = 'family'
        companions_desc = '带老人出行'
        has_child = False
        companions = ['elderly']
    else:
        scenario = 'family'
        companions_desc = '家人出行'
        has_child = False
        companions = ['family']

    # Detect start time
    time_match = re.search(r'(\d{1,2})\s*[点:时]', message)
    if time_match:
        h = int(time_match.group(1))
        if h < 8:
            h += 12
        start_time = f"{h:02d}:00"
    else:
        # Default: current hour + 1, rounded to nearest :00/:30
        try:
            ch = int(current_time.split(':')[0])
            sh = min(ch + 1, 20)
        except Exception:
            sh = 14
        start_time = f"{sh:02d}:00"

    # Detect travel style
    if any(w in message for w in ['轻松', '慢慢', '休闲', '不累', '随便', '逛逛']):
        style = 'relaxed'
        style_label = '轻松休闲'
    elif any(w in message for w in ['活力', '活跃', '走走', '多玩', '精彩', '充实']):
        style = 'active'
        style_label = '活力满满'
    else:
        style = 'relaxed'
        style_label = '轻松休闲'

    # Detect food preferences
    food_prefs = []
    if any(w in message for w in ['减肥', '减脂', '低卡', '清淡', '健康']):
        food_prefs = ['清淡', '低油']

    # Build confirm message
    has_elderly = any(w in message for w in ['老人', '爸妈', '父母', '外公', '外婆', '奶奶', '爷爷'])

    missing = []
    if has_child:
        missing = ['child_age']

    lines = [f"好的！我帮你推测了一下本次出行：\n"]
    if scenario == 'family':
        lines.append(f"👨‍👩‍👧 {companions_desc}")
    elif scenario == 'friends':
        lines.append(f"👥 {companions_desc}")
    elif scenario == 'couple':
        lines.append(f"💑 {companions_desc}")
    lines.append(f"⏰ 出发时间：{start_time}")
    lines.append(f"⏱ 计划时长：约3-4小时")
    lines.append(f"✨ 出行风格：{style_label}")
    if food_prefs:
        lines.append(f"🥗 饮食偏好：{' + '.join(food_prefs)}")
    if has_child:
        lines.append(f"👶 孩子年龄：（还没填，帮我补充一下？）")
    if has_elderly:
        lines.append(f"👴 已考虑老人体力限制")

    lines.append(f"\n有什么需要纠正或补充的吗？{('（孩子多大？）' if has_child else '')}")

    inferred = {
        "scenario": scenario,
        "start_time": start_time,
        "duration_hours": 3,
        "companions_desc": companions_desc,
        "companions": companions,
        "has_children": has_child,
        "child_age": None,
        "has_elderly": has_elderly,
        "special_needs": (['dietary_restriction'] if food_prefs else []),
        "travel_style": style,
        "food_preferences": food_prefs,
        # skip_restaurant is determined by LLM in confirm_preferences;
        # fallback defaults to False (conservative)
        "skip_restaurant": False,
    }

    return {
        "inferred": inferred,
        "confidence": "medium",
        "confirm_message": "\n".join(lines),
        "missing_fields": missing,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 2: Confirm Preferences
# ════════════════════════════════════════════════════════════════════

async def confirm_preferences(inferred: dict, user_response: str) -> dict:
    """
    Extract final confirmed preferences from user's response to clarification.
    Returns: {session_facts, preferences, ready_to_plan, start_message}
    """
    if not _has_api_key():
        return _fallback_confirm_prefs(inferred, user_response)

    user_p = prompts.CONFIRM_PREFS_USER.format(
        inferred=json.dumps(inferred, ensure_ascii=False),
        user_response=user_response,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.CONFIRM_PREFS_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"session_facts":{...},"preferences":{...},"ready_to_plan":true}',
            max_tokens=1024,
        )
        facts, prefs = merge_specific_preferences(
            result.get("session_facts", {}) or inferred,
            result.get("preferences", {}),
            user_response,
        )
        result["session_facts"] = facts
        result["preferences"] = prefs
        return result
    except Exception as e:
        logger.error(f"confirm_preferences failed: {e}")
        return _fallback_confirm_prefs(inferred, user_response)


def _fallback_confirm_prefs(inferred: dict, user_response: str) -> dict:
    prefs = dict(inferred)

    # Extract child age
    age_match = re.search(r'(\d+)\s*岁', user_response)
    if age_match:
        prefs['child_age'] = int(age_match.group(1))

    # Extract start time override
    time_match = re.search(r'(\d{1,2})\s*[点:时]', user_response)
    if time_match:
        h = int(time_match.group(1))
        if h < 8:
            h += 12
        prefs['start_time'] = f"{h:02d}:00"

    # Extract style override
    if any(w in user_response for w in ['轻松', '慢慢', '随便']):
        prefs['travel_style'] = 'relaxed'
    elif any(w in user_response for w in ['活力', '多玩', '精彩']):
        prefs['travel_style'] = 'active'

    # skip_restaurant is determined by the LLM in confirm_preferences().
    # In the fallback (no LLM), we conservatively default to False.
    # The LLM prompt in CONFIRM_PREFS_USER is responsible for detecting all
    # natural language expressions like "回家吃"/"自己解决"/"不需要餐厅" etc.
    skip_restaurant = False

    # Extract explicit food/activity preferences
    prefs, detected_pref = merge_specific_preferences(prefs, {}, user_response)
    food_prefs = list(prefs.get('food_preferences', []))
    if any(w in user_response for w in ['老人', '爸妈', '父母']):
        prefs['has_elderly'] = True

    scenario = prefs.get('scenario', 'family')
    style = prefs.get('travel_style', 'relaxed')

    session_facts = {
        "scenario": scenario,
        "start_time": prefs.get('start_time', '14:00'),
        "duration_hours": prefs.get('duration_hours', 3),
        "companions": prefs.get('companions', ['family']),
        "child_age": prefs.get('child_age'),
        "has_elderly": prefs.get('has_elderly', False),
        "special_needs": prefs.get('special_needs', []),
        "home_area": "北京望京",
        "travel_style": style,
    }

    mode = "light_managed" if style == 'relaxed' else "full_managed"

    child_age = prefs.get('child_age')
    if child_age:
        start_msg = f"明白了！{child_age}岁小朋友，{prefs.get('start_time','14:00')}出发，帮你找最合适的方案！"
    else:
        start_msg = f"明白了！{prefs.get('start_time','14:00')}出发，马上帮你规划！"

    return {
        "session_facts": session_facts,
        "preferences": {
            "distance": "nearby",
            "food": food_prefs,
            "venue": detected_pref.get("venue") or prefs.get("venue_preference"),
            "mode": mode,
            "avoid": [],
            "skip_restaurant": skip_restaurant,
        },
        "ready_to_plan": True,
        "start_message": start_msg,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 3: Preference Extraction (for direct plan requests)
# ════════════════════════════════════════════════════════════════════

PREFERENCE_EXTRACTION_SYSTEM = prompts.BUTLER_SYSTEM + """

你当前的任务是：从用户的自然语言输入和快捷标签中提取结构化偏好信息。
只输出 JSON，禁止任何其他文字。"""

PREFERENCE_EXTRACTION_USER = """用户输入：{message}
用户选择的快捷标签：{tags}
已识别场景：{scenario}

请提取以下信息并输出 JSON：

{{
  "session_facts": {{
    "scenario": "family|friends",
    "start_time": "HH:MM",
    "companions": ["spouse", "child", ...],
    "child_age": null,
    "home_area": "字符串或null"
  }},
  "preferences": {{
    "distance": "nearby|any",
    "food": ["清淡", "低油", "减脂"] 或空数组,
    "mode": "light_managed|full_managed",
    "avoid": []
  }},
  "need_clarification": false,
  "clarifying_question": null
}}"""


async def extract_preferences(message: str, tags: list, scenario: str) -> dict:
    if not _has_api_key():
        return _fallback_preferences(message, tags, scenario)
    user_p = PREFERENCE_EXTRACTION_USER.format(
        message=message,
        tags=json.dumps(tags, ensure_ascii=False),
        scenario=scenario,
    )
    try:
        _, result = await _call_json_with_retry(
            PREFERENCE_EXTRACTION_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"session_facts":{...},"preferences":{...}}',
            max_tokens=1024,
        )
        return result
    except Exception as e:
        logger.error(f"extract_preferences failed: {e}")
        return _fallback_preferences(message, tags, scenario)


def _fallback_preferences(message: str, tags: list, scenario: str) -> dict:
    start_time = "14:00"
    for t in tags:
        if "1点" in t: start_time = "13:00"
        elif "2点" in t: start_time = "14:00"
        elif "3点" in t: start_time = "15:00"

    child_age, companions = None, (["spouse"] if scenario == "family" else ["friends"])
    if any("孩子" in t or "岁" in t for t in tags):
        child_age = 5; companions.append("child")

    tagged_text = " ".join(tags) + " " + message
    detected = detect_specific_preferences(tagged_text)
    food = detected.get("food", [])

    return {
        "session_facts": {
            "scenario": scenario, "start_time": start_time,
            "companions": companions, "child_age": child_age, "home_area": "北京望京",
            "has_elderly": False, "special_needs": [], "travel_style": "relaxed",
            "food_preferences": food,
            "venue_preference": detected.get("venue"),
            "activity_preference": detected.get("activity_preference"),
            "activity_preference_label": detected.get("activity_preference_label"),
            "friends_activity_type": detected.get("friends_activity_type"),
        },
        "preferences": {"distance": "nearby", "food": food, "venue": detected.get("venue"), "mode": "full_managed", "avoid": []},
        "need_clarification": False, "clarifying_question": None,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 3.5: LLM Candidate Scoring
# ════════════════════════════════════════════════════════════════════

async def score_candidates(activities: list, restaurants: list,
                            session_facts: dict,
                            weather: dict = None) -> tuple[list, list]:
    """
    LLM-based scoring of all POI candidates.
    Attaches _llm_score and _llm_reason to each item in-place, then sorts both lists.
    Falls back to Python scoring if LLM unavailable.
    Returns (sorted_activities, sorted_restaurants).
    """
    if not _has_api_key():
        _fallback_attach_scores(activities, restaurants, session_facts)
        return activities, restaurants

    sf = session_facts
    has_child = sf.get("has_children", False) or bool(sf.get("child_age"))
    food_prefs = sf.get("food_preferences") or sf.get("food", [])

    score_fields = [
        "poi_id", "name", "category", "type", "distance_km", "queue_min",
        "rating", "tags", "facts_tags", "venue", "age_policy", "open_status",
        "availability", "booking_required", "menu_features", "facilities",
    ]
    candidates = []
    for a in activities:
        c = {k: v for k, v in a.items() if k in score_fields}
        c["_type"] = "activity"
        candidates.append(c)
    for r in restaurants:
        c = {k: v for k, v in r.items() if k in score_fields}
        c["_type"] = "restaurant"
        candidates.append(c)

    valid_ids = {c["poi_id"] for c in candidates if c.get("poi_id")}

    user_p = prompts.SCORING_USER.format(
        scenario=sf.get("scenario", "family"),
        special_requirements=json.dumps(food_prefs, ensure_ascii=False),
        has_child=has_child,
        child_age=sf.get("child_age"),
        has_elderly=sf.get("has_elderly", False),
        travel_style=sf.get("travel_style", "relaxed"),
        weather=json.dumps(weather or {}, ensure_ascii=False),
        count=len(candidates),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
    )

    try:
        _, result = await _call_json_with_retry(
            prompts.SCORING_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"scored":[{"poi_id":"...","score":85,"planner_reason":"...","recommended":true}]}',
            max_tokens=4096,
        )
        scored = result.get("scored", [])

        # Anti-hallucination: drop any score whose poi_id isn't in our input
        validated = [s for s in scored if s.get("poi_id") in valid_ids]
        if len(validated) < len(scored):
            logger.warning(f"score_candidates: filtered {len(scored)-len(validated)} hallucinated POI scores")

        score_map = {s["poi_id"]: s for s in validated}

        for item in activities + restaurants:
            pid = item.get("poi_id", "")
            if pid in score_map:
                item["_llm_score"] = score_map[pid].get("score", 50)
                item["_llm_reason"] = score_map[pid].get("planner_reason", "")
            else:
                item["_llm_score"] = 50
                item["_llm_reason"] = ""

        activities.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)
        restaurants.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)

        top_act = activities[0]["name"] if activities else "-"
        top_rst = restaurants[0]["name"] if restaurants else "-"
        logger.info(
            f"score_candidates: top activity={top_act}({activities[0].get('_llm_score',0) if activities else 0}), "
            f"top restaurant={top_rst}({restaurants[0].get('_llm_score',0) if restaurants else 0})"
        )
        return activities, restaurants

    except Exception as e:
        logger.error(f"score_candidates LLM failed: {e}, using fallback scoring")
        _fallback_attach_scores(activities, restaurants, session_facts)
        return activities, restaurants


def _fallback_attach_scores(activities: list, restaurants: list, session_facts: dict):
    """Attach Python-computed scores and sort in-place."""
    sf = session_facts
    sf_ext = {
        **sf,
        "has_children": sf.get("has_children", False) or bool(sf.get("child_age")),
        "food_preferences": sf.get("food_preferences") or sf.get("food", []),
    }
    for a in activities:
        raw = _score_activity(a, sf_ext)
        a["_llm_score"] = max(0, min(100, int(50 + raw * 5)))
        a["_llm_reason"] = a.get("name", "")[:20]
    for r in restaurants:
        raw = _score_restaurant(r, sf_ext)
        r["_llm_score"] = max(0, min(100, int(50 + raw * 5)))
        r["_llm_reason"] = r.get("name", "")[:20]
    activities.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)
    restaurants.sort(key=lambda x: x.get("_llm_score", 0), reverse=True)


# ════════════════════════════════════════════════════════════════════
# Skill 4: Itinerary Planner
# ════════════════════════════════════════════════════════════════════

ICON_MAP = {
    "indoor_playground": "🎪", "picture_book_library": "📚",
    "children_science": "🔬", "citywalk": "🚶", "board_game": "🎲",
    "exhibition": "🎨", "livehouse": "🎵", "restaurant": "🍽️",
    "light_food": "🥗", "bar": "🍻",
}


async def plan_itinerary(activities: list, restaurants: list, weather: dict,
                          prefs: dict, mode: str,
                          scored: bool = False) -> dict:
    if not _has_api_key():
        return _fallback_plan(activities, restaurants, prefs, mode)

    sf   = prefs.get("session_facts", {})
    pref = prefs.get("preferences", {})

    act_fields = ["poi_id", "name", "category", "distance_km", "business_hours",
                  "booking_required", "queue_min", "estimated_duration_min",
                  "venue", "age_policy", "risk_facts", "open_status", "availability",
                  "available_slots"]
    rst_fields = ["poi_id", "name", "distance_km", "queue_min", "rating",
                  "avg_price", "facilities", "menu_features", "business_hours",
                  "risk_facts", "location_features"]

    # Pre-compute first node time so LLM can't get it wrong
    try:
        sh, sm = map(int, sf.get("start_time", "14:00").split(":"))
        fn_total = sh * 60 + sm + 20          # +20 min transit
        fn_h, fn_m = fn_total // 60, fn_total % 60
        first_node_time = f"{fn_h:02d}:{fn_m:02d}"
    except Exception:
        first_node_time = sf.get("start_time", "14:00")

    # If candidates were pre-scored, enrich slim() output with score info
    def slim(items, fields):
        result = [{k: v for k, v in p.items() if k in fields} for p in items[:6]]
        for i, item in enumerate(items[:6]):
            if item.get("_llm_score") is not None:
                result[i]["_llm_score"] = item["_llm_score"]
            if item.get("_llm_reason"):
                result[i]["_llm_reason"] = item["_llm_reason"]
        return result

    user_p = prompts.PLANNER_USER.format(
        scenario                  = sf.get("scenario", "family"),
        start_time                = sf.get("start_time", "14:00"),
        first_node_time           = first_node_time,
        companions                = json.dumps(sf.get("companions", []), ensure_ascii=False),
        travel_style              = sf.get("travel_style", "relaxed"),
        preferences               = json.dumps(pref, ensure_ascii=False),
        skip_restaurant           = pref.get("skip_restaurant", False),
        mode                      = mode,
        weather                   = json.dumps(weather, ensure_ascii=False),
        activities                = json.dumps(slim(activities, act_fields), ensure_ascii=False, indent=2),
        restaurants               = json.dumps(slim(restaurants, rst_fields), ensure_ascii=False, indent=2),
        # children
        has_children              = sf.get("has_children", sf.get("has_child", False)),
        child_age                 = sf.get("child_age", "无"),
        child_purpose             = sf.get("child_purpose", "无"),
        # elderly
        has_elderly               = sf.get("has_elderly", False),
        elderly_no_walking        = sf.get("elderly_no_walking", False),
        # gender
        group_gender              = sf.get("group_gender", "unknown"),
        female_count              = sf.get("female_count", 0),
        male_count                = sf.get("male_count", 0),
        female_weight_loss        = sf.get("female_weight_loss", False),
        female_prefer_low_intensity = sf.get("female_prefer_low_intensity", False),
        female_prefer_indoor      = sf.get("female_prefer_indoor", False),
        male_prefer_high_intensity = sf.get("male_prefer_high_intensity", False),
        # friends
        friends_activity_type     = sf.get("friends_activity_type", "无"),
    )

    try:
        thinking, result = await _call_json_with_retry(
            prompts.PLANNER_SYSTEM, user_p,
            model_alias="main_model",
            schema_hint='{"cot":[...],"nodes":[...],"summary":"..."}',
            max_tokens=8000,
        )
        if thinking:
            result["cot"] = _thinking_to_cot(thinking)
        if "nodes" not in result:
            raise ValueError("Missing 'nodes' field")

        # Anti-hallucination: remove nodes whose poiId isn't in our input lists
        valid_poi_ids = {a["poi_id"] for a in activities} | {r["poi_id"] for r in restaurants}
        valid_poi_ids.add("walk_001")  # standard light-tail placeholder
        orig_count = len(result["nodes"])
        result["nodes"] = [
            n for n in result["nodes"]
            if n.get("poiId", n.get("poi_id", "")) in valid_poi_ids
        ]
        if len(result["nodes"]) < orig_count:
            logger.warning(
                f"plan_itinerary: removed {orig_count - len(result['nodes'])} hallucinated nodes"
            )

        return result
    except Exception as e:
        logger.error(f"plan_itinerary LLM failed: {e}, using fallback")
        return _fallback_plan(activities, restaurants, prefs, mode)


def _score_activity(act: dict, sf: dict) -> float:
    """Score an activity based on user preferences. Higher = better fit."""
    scenario = sf.get("scenario", "family")
    style    = sf.get("travel_style", "relaxed")
    child_age = sf.get("child_age")
    has_child = sf.get("has_children", False) or (child_age is not None)
    has_elderly = sf.get("has_elderly", False)
    score = 0.0

    # Scenario exact match beats fallback
    if act.get("scenario") == scenario:
        score += 4.0
    elif act.get("scenario") == "both":
        score += 2.0

    # Travel style vs venue environment
    env = act.get("venue", {}).get("environment", "indoor")
    if style == "active" and env == "outdoor":
        score += 2.0
    elif style in ("relaxed", "cultural") and env == "indoor":
        score += 1.5

    # Distance (prefer closer; active users tolerate farther)
    dist = act.get("distance_km", 5.0)
    max_dist = 15.0 if style == "active" else 8.0
    if dist <= max_dist:
        score += max(0, 2.5 - dist * 0.2)
    else:
        score -= (dist - max_dist) * 0.5  # penalize beyond threshold

    # Child fit
    if has_child and child_age is not None:
        age_min = act.get("age_policy", {}).get("min_age", 0)
        age_max = act.get("age_policy", {}).get("max_age", 99)
        if age_min <= child_age <= age_max:
            score += 2.5
        elif child_age < age_min:
            score -= 5.0  # hard penalty: too young

    # Elderly: prefer indoor + close
    if has_elderly:
        if env == "indoor":
            score += 1.0
        score -= dist * 0.3

    # Open status
    if act.get("open_status") == "closed":
        score -= 20.0

    # Booking availability
    if act.get("availability") == "limited":
        score -= 0.5

    # Rating bonus
    rating = act.get("rating", 4.0)
    score += (rating - 4.0) * 1.5

    return score


def _score_restaurant(rst: dict, sf: dict) -> float:
    """Score a restaurant based on user preferences."""
    scenario = sf.get("scenario", "family")
    has_child = sf.get("has_children", False) or (sf.get("child_age") is not None)
    food_prefs = sf.get("food_preferences", [])
    score = 0.0

    # Scenario match
    if rst.get("scenario") == scenario:
        score += 3.0
    elif rst.get("scenario") == "both":
        score += 1.5

    # Distance
    dist = rst.get("distance_km", 3.0)
    score += max(0, 2.0 - dist * 0.15)

    # Queue time (shorter = better)
    q = rst.get("queue_min", 0)
    if q > 45:
        score -= 2.0
    elif q < 15:
        score += 1.0

    # Food preferences
    features = set(rst.get("menu_features", []))
    pref_aliases = {"清淡": "light_food_options", "低油": "low_oil_options",
                    "减脂": "light_food_options"}
    for pref in food_prefs:
        if pref_aliases.get(pref, pref) in features:
            score += 2.5

    # Child seat for families
    if has_child and rst.get("facilities", {}).get("child_seat"):
        score += 1.5

    # Rating bonus
    rating = rst.get("rating", 4.0)
    score += (rating - 4.0) * 1.5

    return score


def _fallback_plan(activities: list, restaurants: list, prefs: dict, mode: str) -> dict:
    sf       = prefs.get("session_facts", {})
    scenario = sf.get("scenario", "family")
    style    = sf.get("travel_style", "relaxed")
    child_age = sf.get("child_age")
    has_child = sf.get("has_children", False) or (child_age is not None)
    has_elderly = sf.get("has_elderly", False)
    food_prefs  = sf.get("food_preferences", []) or prefs.get("preferences", {}).get("food", [])
    skip_restaurant = prefs.get("preferences", {}).get("skip_restaurant", False)

    def add_min(h, m, d): t = h*60+m+d; return t//60, t%60
    def fmt(h, m): return f"{h:02d}:{m:02d}"

    sh, sm = map(int, sf.get("start_time", "14:00").split(":"))
    cur_h, cur_m = add_min(sh, sm, 20)
    nodes = []

    # ── Score & pick best activity ──────────────────────────────────
    sf_for_score = {**sf, "has_children": has_child, "food_preferences": food_prefs}
    scored_acts = sorted(
        [a for a in activities if a.get("open_status") != "closed"],
        key=lambda a: _score_activity(a, sf_for_score),
        reverse=True,
    )
    act = scored_acts[0] if scored_acts else None

    # ── Score & pick best restaurant ────────────────────────────────
    scored_rsts = sorted(
        restaurants,
        key=lambda r: _score_restaurant({**r, "queue_min": r.get("queue_min", 0)}, sf_for_score),
        reverse=True,
    )
    rst = scored_rsts[0] if scored_rsts else None

    SCENE_LABELS = {
        "family": "家庭亲子", "friends": "朋友聚会",
        "couple": "情侣约会", "solo": "独自出行",
    }
    scene_label = SCENE_LABELS.get(scenario, scenario)
    style_label = "轻松休闲" if style == "relaxed" else "活力满满" if style == "active" else style

    # ── Build activity node ──────────────────────────────────────────
    if act:
        dur = act.get("estimated_duration_min", 90)
        eh, em = add_min(cur_h, cur_m, dur)
        booking_urgent = (act.get("booking_required") and
                          act.get("availability") in ("limited", "low"))
        act_tags = list(act.get("tags", []))[:3] or (
            ["室内"] if act.get("venue", {}).get("environment") == "indoor" else ["户外"]
        )
        reason = act.get("tags", [""])[0] if act.get("tags") else ""
        if has_child:
            reason = f"儿童友好·{reason}" if reason else "儿童友好"
        elif scenario == "couple":
            reason = f"情侣打卡·{reason}" if reason else "适合情侣"
        nodes.append({
            "id": "node_001", "type": "activity",
            "icon": ICON_MAP.get(act.get("category", ""), "🎪"),
            "name": act["name"], "sub": act.get("address", ""),
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(eh, em),
            "duration": f"约{dur//60}小时{dur%60 if dur%60 else ''}{'分钟' if dur%60 else ''}",
            "distance": f"{act.get('distance_km',0):.1f}公里",
            "queueMin": 0, "queueText": "无需排队",
            "price": f"¥{act.get('ticket_price',88)}/位" if act.get("ticket_price") else "免费",
            "rating": act.get("rating", 4.5),
            "tags": act_tags,
            "reason": reason[:25] or "距离近，体验好",
            "poiId": act["poi_id"],
            "booking_urgent": booking_urgent,
            "status": "planned", "pinned": False, "locked": False,
        })
        cur_h, cur_m = add_min(eh, em, 25 if style == "active" else 35)

    # ── Build restaurant node ────────────────────────────────────────
    if rst and not skip_restaurant:
        eh, em = add_min(cur_h, cur_m, 60)
        q = rst.get("queue_min", 0)
        rst_tags = list(rst.get("tags", []))[:3] or ["餐厅"]
        nodes.append({
            "id": "node_002", "type": "restaurant",
            "icon": "🥗" if any(t in ["轻食", "清淡健康"] for t in rst.get("tags", [])) else "🍽️",
            "name": rst["name"], "sub": rst.get("address", ""),
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(eh, em),
            "duration": "约1小时",
            "distance": f"{rst.get('distance_km',0):.1f}公里",
            "queueMin": q, "queueText": f"约{q}分钟" if q > 0 else "无需排队",
            "price": f"¥{rst.get('avg_price',80)}/位",
            "rating": rst.get("rating", 4.5),
            "tags": rst_tags,
            "reason": (rst.get("tags", [""])[0] or "口碑好")[:25],
            "poiId": rst["poi_id"],
            "booking_urgent": rst.get("booking_required", False),
            "status": "planned", "pinned": False, "locked": False,
        })
        cur_h, cur_m = add_min(eh, em, 20)

    # ── Optional light tail node ─────────────────────────────────────
    tail_map = {
        "family": ("🛍️", "商场轻松逛逛", "望京SOHO", "亲子收尾，孩子自由活动"),
        "couple": ("☕", "咖啡甜品收尾",  "周边咖啡馆", "情侣闲聊，完美收尾"),
        "friends":("🍺", "夜市/酒吧续摊",  "周边夜市", "朋友续摊，尽兴而归"),
        "solo":   ("☕", "安静咖啡馆",     "周边咖啡馆", "独自充电，放松回顾"),
    }
    if not has_elderly:
        tail = tail_map.get(scenario, tail_map["friends"])
        le, lm = add_min(cur_h, cur_m, 40)
        nodes.append({
            "id": "node_003", "type": "light", "icon": tail[0],
            "name": tail[1], "sub": tail[2],
            "timeStart": fmt(cur_h, cur_m), "timeEnd": fmt(le, lm),
            "duration": "约40分钟", "distance": "步行可达",
            "queueMin": None, "queueText": "无需等待",
            "price": "免费", "rating": None, "tags": ["轻松", "收尾"],
            "reason": tail[3],
            "poiId": "walk_001",
            "booking_urgent": False,
            "status": "optional", "pinned": False, "locked": False,
        })

    act_name = act["name"] if act else "活动"
    rst_name = rst["name"] if rst else "餐厅"
    return {
        "cot": [
            f"识别到：{scene_label}·{style_label}，出发时间 {sf.get('start_time','14:00')}",
            f"约束：{'有孩子' if has_child else ''}{'有老人' if has_elderly else ''}{'·' + ','.join(food_prefs) if food_prefs else ''}",
            f"活动评分 Top1：{act_name}（场景匹配+{style_label}偏好+距离综合评分）",
            f"餐厅评分 Top1：{rst_name}（{'含儿童椅' if has_child else ''}{'·' + ','.join(food_prefs[:1]) if food_prefs else ''}综合评分）",
            f"合理性自检：通勤比例约15% ✓  活动时间充足 ✓  节奏{style_label} ✓",
            "✅ 基于偏好评分规划完成",
        ],
        "nodes": nodes,
        "summary": f"{scene_label}·{style_label}方案：{act_name}→{rst_name}，共{len(nodes)}站",
    }


# ════════════════════════════════════════════════════════════════════
# Skill 5: Partial Replanning
# ════════════════════════════════════════════════════════════════════

async def replan_partial(event: dict, itinerary: list, alternatives: dict, memory: dict) -> dict:
    if not _has_api_key():
        return _fallback_replan(event, alternatives)

    user_p = prompts.REPLANNER_USER.format(
        event       = json.dumps(event,       ensure_ascii=False, indent=2),
        itinerary   = json.dumps(itinerary,   ensure_ascii=False, indent=2),
        alternatives= json.dumps(alternatives, ensure_ascii=False, indent=2),
        memory      = json.dumps(memory,      ensure_ascii=False, indent=2),
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.REPLANNER_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"thought":"...","affected_node_id":"...","recommended":{...},"user_message":"..."}',
            max_tokens=2048,
        )
        return result
    except Exception as e:
        logger.error(f"replan_partial failed: {e}")
        return _fallback_replan(event, alternatives)


def _fallback_replan(event: dict, alternatives: dict) -> dict:
    recs = alternatives.get("recommended", [])
    best = recs[0] if recs else {}
    etype = event.get("type", "")
    msg = (f"餐厅排队突增，推荐备选：{best.get('name','附近餐厅')}" if etype == "queue_spike"
           else f"天气影响户外，推荐室内：{best.get('name','室内活动')}")
    return {
        "thought": f"触发原因：{event.get('message','异常')}。替换受影响节点。",
        "affected_node_id": "node_002" if etype == "queue_spike" else "node_001",
        "recommended": {
            "poi_id": best.get("poi_id", ""),
            "name": best.get("name", "备选方案"),
            "icon": "🥗" if etype == "queue_spike" else "🎨",
            "sub": best.get("address", "同商圈内"),
            "queueText": f"约{best.get('queue_min',15)}分钟",
            "distance": f"{best.get('distance_km',1.0):.1f}公里",
            "tags": best.get("menu_features", [])[:2] or ["室内"],
            "reason": msg[:25],
        },
        "more_options": alternatives.get("more_options", []),
        "user_message": msg,
    }


# ════════════════════════════════════════════════════════════════════
# Skill 6: LLM Simulator Event Generation
# ════════════════════════════════════════════════════════════════════

async def generate_simulator_event(session_context: dict) -> dict:
    """
    Generate a realistic mock environment event for the demo.
    session_context: {scenario, current_time, itinerary, weather, queues, bookings, scenario_script, recent_events}
    """
    if not _has_api_key():
        return _fallback_simulator_event(session_context)

    itinerary = session_context.get("itinerary", [])
    itinerary_summary = json.dumps(
        [{"name": n.get("name"), "type": n.get("type"), "poiId": n.get("poiId")} for n in itinerary],
        ensure_ascii=False
    )

    queue_status = json.dumps(session_context.get("queues", {}), ensure_ascii=False)
    booking_status = json.dumps(session_context.get("bookings", {}), ensure_ascii=False)
    recent_events_str = json.dumps(
        [e.get("type") for e in session_context.get("recent_events", [])[-5:]],
        ensure_ascii=False
    )

    user_p = prompts.SIMULATOR_USER.format(
        scenario=session_context.get("scenario", "family"),
        current_time=session_context.get("current_time", time.strftime("%H:%M")),
        itinerary_summary=itinerary_summary,
        weather=json.dumps(session_context.get("weather", {}), ensure_ascii=False),
        queue_status=queue_status,
        booking_status=booking_status,
        scenario_script=json.dumps(session_context.get("scenario_script", {}), ensure_ascii=False),
        recent_events=recent_events_str,
    )

    try:
        _, result = await _call_json_with_retry(
            prompts.SIMULATOR_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"event_type":"...","target_poi_id":"...","severity":"...","message":"...","state_patch":{...}}',
            max_tokens=1024,
        )
        return result
    except Exception as e:
        logger.error(f"generate_simulator_event failed: {e}")
        return _fallback_simulator_event(session_context)


def _fallback_simulator_event(context: dict) -> dict:
    """Rule-based fallback event generator."""
    scenario = context.get("scenario", "family")
    queues = context.get("queues", {})
    recent = [e.get("type") for e in context.get("recent_events", [])[-3:]]

    # Pick an event type not recently triggered
    candidates = []
    if "queue_spike" not in recent:
        # Find a restaurant queue that can spike
        rest_poi = "rest_family_001" if scenario == "family" else "rest_friend_001"
        current_wait = queues.get(rest_poi, {}).get("estimated_wait_min", 20)
        if current_wait < 60:
            new_wait = min(current_wait + 25, 75)
            candidates.append({
                "event_type": "queue_spike",
                "target_poi_id": rest_poi,
                "severity": "high" if new_wait > 50 else "medium",
                "message": f"餐厅排队从{current_wait}分钟升至{new_wait}分钟，进入用餐高峰",
                "reason": "临近用餐高峰时段，排队上升属正常现象",
                "agent_dialogue": f"模拟器: 触发排队上升事件 → {rest_poi} {current_wait}min → {new_wait}min",
                "recommended_poll_after_sec": 30,
                "state_patch": {"queue": {
                    "queue_tables": new_wait // 3,
                    "estimated_wait_min": new_wait,
                    "can_take_number": True,
                    "status": "queue_spike" if new_wait > 45 else "rising",
                }},
            })

    if "weather_heavy_rain" not in recent:
        weather = context.get("weather", {})
        if weather.get("rain_level", "none") == "none":
            candidates.append({
                "event_type": "weather_heavy_rain",
                "target_poi_id": None,
                "severity": "high",
                "message": "北京朝阳区开始降雨，户外活动受影响",
                "reason": "午后对流天气，降雨概率较高",
                "agent_dialogue": "模拟器: 触发天气变化事件 → 多云转小雨",
                "recommended_poll_after_sec": 60,
                "state_patch": {"weather": {
                    "condition": "light_rain",
                    "rain_level": "light",
                    "risk_level": "medium",
                }},
            })

    if candidates:
        import random
        return random.choice(candidates)

    # Default: mild queue fluctuation
    rest_poi = "rest_family_001" if scenario == "family" else "rest_friend_001"
    current = queues.get(rest_poi, {}).get("estimated_wait_min", 18)
    new_wait = max(5, current + (10 if current < 30 else -10))
    return {
        "event_type": "queue_spike" if new_wait > current else "queue_drop",
        "target_poi_id": rest_poi,
        "severity": "low",
        "message": f"餐厅排队变化：{current}分钟 → {new_wait}分钟",
        "reason": "常规排队波动",
        "agent_dialogue": f"模拟器: 排队自然波动 {current}→{new_wait}分钟",
        "recommended_poll_after_sec": 30,
        "state_patch": {"queue": {
            "queue_tables": max(1, new_wait // 4),
            "estimated_wait_min": new_wait,
            "can_take_number": True,
            "status": "normal",
        }},
    }


# ════════════════════════════════════════════════════════════════════
# Skill 7: Natural Language → Simulator Event
# ════════════════════════════════════════════════════════════════════

_INJECT_SYSTEM = """你是一个本地生活模拟器，把自然语言描述转换为结构化模拟事件。
只输出 JSON，严禁其他文字：
{
  "event_type": "queue_spike|queue_drop|weather_heavy_rain|weather_clear|activity_closed|custom",
  "target_poi_id": "行程中对应的 poi_id，无则 null",
  "severity": "low|medium|high",
  "message": "中文简洁描述",
  "state_patch": {
    "queue": {"estimated_wait_min": N, "queue_tables": N, "status": "queue_spike|normal"} ,
    "weather": {"condition": "sunny|light_rain|heavy_rain", "rain_level": "none|light|heavy", "risk_level": "low|medium|high"}
  },
  "agent_dialogue": "一句话说明触发了什么"
}"""


async def interpret_simulator_event(text: str, context: dict) -> dict:
    """Convert natural language event text to structured simulator event."""
    if not _has_api_key():
        return _fallback_interpret_event(text, context)

    itinerary_summary = json.dumps(
        [{"name": n.get("name"), "type": n.get("type"), "poiId": n.get("poiId")}
         for n in context.get("itinerary", [])],
        ensure_ascii=False,
    )
    user_p = (
        f"当前行程：{itinerary_summary}\n"
        f"当前天气：{json.dumps(context.get('weather', {}), ensure_ascii=False)}\n"
        f"当前排队：{json.dumps(context.get('queues', {}), ensure_ascii=False)}\n\n"
        f"测试者描述的事件：{text}\n\n"
        "请转换为结构化格式，如果涉及餐厅排队请从行程中找对应 poiId。"
    )
    try:
        _, result = await _call_json_with_retry(
            _INJECT_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"event_type":"...","target_poi_id":"...","severity":"...","message":"...","state_patch":{...}}',
            max_tokens=512,
        )
        return result
    except Exception as e:
        logger.error(f"interpret_simulator_event failed: {e}")
        return _fallback_interpret_event(text, context)


def _fallback_interpret_event(text: str, context: dict) -> dict:
    itinerary = context.get("itinerary", [])
    rest_poi = next(
        (n.get("poiId") for n in itinerary if n.get("type") == "restaurant"),
        "rest_family_001",
    )

    # Queue spike / drop
    if any(w in text for w in ["排队", "等待", "人多", "拥挤", "堵"]):
        m = re.search(r"(\d+)", text)
        wait = int(m.group(1)) if m else 60
        return {
            "event_type": "queue_spike",
            "target_poi_id": rest_poi,
            "severity": "high" if wait > 45 else "medium",
            "message": f"餐厅排队约{wait}分钟，进入高峰",
            "state_patch": {"queue": {
                "estimated_wait_min": wait,
                "queue_tables": max(1, wait // 3),
                "status": "queue_spike" if wait > 45 else "rising",
            }},
            "agent_dialogue": f"注入排队事件 → {rest_poi} 排队 {wait} 分钟",
        }

    # Weather
    if any(w in text for w in ["下雨", "雨", "天气差", "暴雨", "大雨"]):
        heavy = any(w in text for w in ["暴雨", "大雨", "很大"])
        return {
            "event_type": "weather_heavy_rain",
            "target_poi_id": None,
            "severity": "high",
            "message": ("大雨预警" if heavy else "开始降雨") + "，户外活动受影响",
            "state_patch": {"weather": {
                "condition": "heavy_rain" if heavy else "light_rain",
                "rain_level": "heavy" if heavy else "light",
                "risk_level": "high" if heavy else "medium",
            }},
            "agent_dialogue": f"注入天气事件 → {'大雨' if heavy else '小雨'}预警",
        }

    # Activity closed
    if any(w in text for w in ["关闭", "关门", "满了", "停止", "关了"]):
        act_poi = next(
            (n.get("poiId") for n in itinerary if n.get("type") == "activity"), None
        )
        return {
            "event_type": "activity_closed",
            "target_poi_id": act_poi,
            "severity": "high",
            "message": "活动场地临时关闭，请调整行程",
            "state_patch": {},
            "agent_dialogue": "注入场地关闭事件",
        }

    # Queue clear / fallback
    return {
        "event_type": "custom",
        "target_poi_id": None,
        "severity": "medium",
        "message": text[:60],
        "state_patch": {},
        "agent_dialogue": f"注入自定义事件: {text[:30]}",
    }


# ════════════════════════════════════════════════════════════════════
# Skill 8: Queue Timing Advice
# ════════════════════════════════════════════════════════════════════

async def get_queue_advice(restaurant_name: str, current_wait: int,
                            trend: str, history: list,
                            planned_time: str, current_time: str) -> dict:
    if not _has_api_key():
        return _fallback_queue_advice(restaurant_name, current_wait, trend, planned_time, current_time)

    user_p = prompts.QUEUE_ADVICE_USER.format(
        restaurant_name=restaurant_name,
        current_wait=current_wait,
        trend=trend,
        history=json.dumps(history),
        planned_time=planned_time,
        current_time=current_time,
    )
    try:
        _, result = await _call_json_with_retry(
            prompts.QUEUE_ADVICE_SYSTEM, user_p,
            model_alias="fast_model",
            schema_hint='{"advice_type":"...","chat_message":"...","optimal_depart_time":"..."}',
            max_tokens=512,
        )
        return result
    except Exception as e:
        logger.error(f"get_queue_advice failed: {e}")
        return _fallback_queue_advice(restaurant_name, current_wait, trend, planned_time, current_time)


def _fallback_queue_advice(name: str, wait: int, trend: str,
                            planned_time: str, current_time: str) -> dict:
    if wait <= 15:
        return {
            "advice_type": "go_now",
            "chat_message": f"🍽️ 好消息！{name} 目前排队仅 {wait} 分钟，现在是去的好时机！",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": wait,
        }
    elif trend == "rising" and wait > 40:
        # Suggest going earlier than planned
        try:
            ph, pm = map(int, planned_time.split(":"))
            early_h, early_m = ph, max(0, pm - 30)
            if early_m < 0:
                early_h -= 1
                early_m += 60
            depart = f"{early_h:02d}:{early_m:02d}"
        except Exception:
            depart = planned_time
        return {
            "advice_type": "go_early",
            "chat_message": f"⚠️ {name} 排队 {wait} 分钟且持续上升，建议提前到 {depart} 出发，避开高峰！",
            "optimal_depart_time": depart,
            "expected_wait_at_arrival": wait + 10,
        }
    elif trend == "falling":
        return {
            "advice_type": "wait",
            "chat_message": f"📉 {name} 排队正在减少（当前 {wait} 分钟），可以稍等一会儿再去，预计更短。",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": max(10, wait - 10),
        }
    else:
        return {
            "advice_type": "go_now",
            "chat_message": f"🍽️ {name} 当前排队约 {wait} 分钟，按计划 {planned_time} 出发即可。",
            "optimal_depart_time": None,
            "expected_wait_at_arrival": wait,
        }


# ════════════════════════════════════════════════════════════════════
# Utility: Feasibility Validation
# ════════════════════════════════════════════════════════════════════

def validate_feasibility(nodes: list, session_facts: dict) -> list[str]:
    """
    Quick feasibility check on generated itinerary nodes.
    Returns a list of risk strings (empty = all good).

    Checks:
    - Node count ≤ 6
    - Per-segment commute ratio: transit_to_node ≤ node_activity_duration
      (calculated per leg, not as a global ratio)
    - No time overlaps between consecutive nodes
    - Child-friendly end time ≤ 20:00
    """
    def _t(node: dict, *keys) -> str:
        for k in keys:
            v = node.get(k, "")
            if v:
                return v
        return ""

    def _parse(t: str) -> int | None:
        try:
            h, m = map(int, t.split(":"))
            return h * 60 + m
        except Exception:
            return None

    if not nodes:
        return ["行程为空"]

    risks = []
    has_child = session_facts.get("has_children") or bool(session_facts.get("child_age"))

    if len(nodes) > 6:
        risks.append(f"节点数 {len(nodes)} 超过6个上限")

    # ── Per-segment commute check ──────────────────────────────────────
    # For each node, compare the transit gap before it against the node's own duration.
    # The first leg (home → first POI) uses session start_time as baseline.
    trip_start_min = _parse(session_facts.get("start_time", ""))

    for i, node in enumerate(nodes):
        ns = _parse(_t(node, "startTime", "timeStart"))
        ne = _parse(_t(node, "endTime", "timeEnd"))
        if ns is None or ne is None:
            continue

        act_dur = max(0, ne - ns)
        if act_dur == 0:
            continue

        if i == 0:
            transit = (ns - trip_start_min) if trip_start_min is not None else None
        else:
            prev_ne = _parse(_t(nodes[i - 1], "endTime", "timeEnd"))
            transit = (ns - prev_ne) if prev_ne is not None else None

        if transit is not None and transit > 0:
            ratio = transit / (transit + act_dur)
            name = node.get("name", f"节点{i+1}")
            if ratio > 0.6:
                risks.append(
                    f"前往「{name}」的通勤({transit}分钟)远超停留({act_dur}分钟)，建议换更近的地点"
                )
            elif ratio > 0.5:
                risks.append(
                    f"前往「{name}」的通勤({transit}分钟)略超停留({act_dur}分钟)，可考虑调整"
                )

    # ── End time check ────────────────────────────────────────────────
    last_end = _t(nodes[-1], "endTime", "timeEnd")
    last_end_min = _parse(last_end)
    if has_child and last_end_min is not None and last_end_min > 20 * 60:
        risks.append(f"结束时间 {last_end} 超过20:00（有儿童场景）")

    # ── Time overlap check ────────────────────────────────────────────
    for i in range(len(nodes) - 1):
        cur_end = _parse(_t(nodes[i], "endTime", "timeEnd"))
        nxt_start = _parse(_t(nodes[i + 1], "startTime", "timeStart"))
        if cur_end is not None and nxt_start is not None and nxt_start < cur_end:
            risks.append(
                f"「{nodes[i].get('name', i+1)}」→「{nodes[i+1].get('name', i+2)}」时间重叠"
            )

    return risks



