# Risk Report — Top 10 Pre-Submission Risks

> Evidence-based. Each risk cites the code location where it lives.
> Severity: 🔴 blocking / 🟡 high / 🟢 medium

---

## R1 — chat_demo.py State Lock After Fulfillment 🔴

| Field | Value |
|-------|-------|
| **Description** | After fulfillment completes (`state == "completed"`), ALL user input that does not contain keywords "累"/"困"/"改" returns the same hardcoded response. |
| **Impact** | User cannot query status, change preferences, or interact after booking. Demo halts. |
| **Code** | `scripts/chat_demo.py:L202-L208` |
| **Evidence** | `elif state == "completed":` branch. Only 3 keywords matched. Everything else: `return "全部预约好了！您按计划出发就行，路上遇到问题随时找我。"` |
| **Fix** | Replace keyword matching with LLM intent routing, or at minimum pass through to a general handler |

---

## R2 — `_phase1_task` / `_phase2_task` Never Assigned 🔴

| Field | Value |
|-------|-------|
| **Description** | `chat_demo.py` declares `self._phase1_task` and `self._phase2_task` as instance variables but never assigns them to the actual `asyncio.create_task()` results. |
| **Impact** | `_cancel_tasks()` (L293-L298) iterates over `[None, None]` and cancels nothing. Race conditions on session state are not prevented. |
| **Code** | `scripts/chat_demo.py:L71-L72` |
| **Evidence** | `self._phase1_task: asyncio.Task \| None = None` and `self._phase2_task: asyncio.Task \| None = None`. Neither is assigned elsewhere in the class. |
| **Fix** | Assign: `self._phase1_task = asyncio.create_task(self.orchestrator._phase1_plan(ctx))` |

---

## R3 — test_server.py Uses Hash-Based Scoring, Not LLM 🟡

| Field | Value |
|-------|-------|
| **Description** | `test_server.py` registers `MockBackend` handlers, including `handle_candidates_score` which computes `hash(poi_id) % 35 + 60` to generate scores. |
| **Impact** | Demo shows deterministic, non-LLM results. No reasoning, no preference matching. |
| **Code** | `scripts/test_server.py:L35-L44` registers all mock handlers. `mocks/__init__.py:L644-L675` hash-based scoring. |
| **Evidence** | `orcestrator.py:L386-L399`: calls `candidates_score` tool → returns hash scores. No LLM invoked by default. |
| **Fix** | Either explicitly show hash-based mode as "Mock Mode" or inject LLM by default |

---

## R4 — No Conversation History Passed to LLM 🟡

| Field | Value |
|-------|-------|
| **Description** | `chat_demo.py` declares `self.conversation_history` but `handle_message()` never appends to it or passes it to LLM calls. |
| **Impact** | Multi-turn dialogue has no context. Second message does not reference first. |
| **Code** | `scripts/chat_demo.py:L48` declaration. L163-L218: `handle_message` never reads or writes `self.conversation_history`. |
| **Evidence** | Compare: `_do_plan()` L234 calls `orchestrator.start_session(user_input)` — but does not include prior history. |
| **Fix** | Append `(user_input, response)` to `conversation_history` after each turn, include in next LLM call |

---

## R5 — SSE Events Not Schema-Validated 🟡

| Field | Value |
|-------|-------|
| **Description** | `test_server.py` forwards raw EventBus events to SSE clients without validating against `schemas/__init__.py` Pydantic models. |
| **Impact** | Frontend receives events with potentially missing or mis-typed fields. `OutputValidator` exists in `core/` but is never called. |
| **Code** | `test_server.py:L80-L88`: forwards raw event dict. `schemas/__init__.py:L292-L311`: 6 schemas defined with strict fields. |
| **Evidence** | `plan_complete` event carries `itinerary.model_dump()`, not `ItineraryPlanSchema(...).model_dump()`. |
| **Fix** | Register schema-specific serializers for each event type |

---

## R6 — `needs_replan` State Has No Recovery Path 🟡

| Field | Value |
|-------|-------|
| **Description** | When itinerary enters `needs_replan` state, the only options in `chat_demo.py` are to cancel+restart. No replan confirmation flow is exposed. |
| **Impact** | Replanning triggered (e.g., by `execution_partial_failure`) has no user-facing recovery. Demo gets stuck. |
| **Code** | `scripts/chat_demo.py:L210-L216` |
| **Evidence** | `elif state == "needs_replan":` → only matches "好"/"确认"/"行"/"换" → cancels and restarts. No option to review or accept replan. |
| **Fix** | Add branch to fetch `replan_ready` event data and display alternatives |

---

## R7 — DeepSeek API May Be Unreachable 🟡

| Field | Value |
|-------|-------|
| **Description** | `LLMClient.chat()` raises exception on network failure. No retry, no fallback strategy in `llm_planner.py` handlers. |
| **Impact** | If DeepSeek is unreachable, all LLM-dependent planning fails. `test_server.py` avoids this by using hash-based mock by default, but `chat_demo.py` depends on LLM. |
| **Code** | `core/llm_client.py:L76-L103`: error → raise. `core/llm_planner.py:L186-L248`: each handler catches Exception and returns error dict. |
| **Evidence** | `test_llm_planner.py` 8 tests connect to real DeepSeek — no mock mode. |
| **Fix** | Add retry with fallback to mock in LLMPlanner |

---

## R8 — Friend Restaurant Data Only 3 Items 🟢

| Field | Value |
|-------|-------|
| **Description** | Friend-scene restaurant pool has only 3 entries. PRD v3 implies >= 6 for comfortable selection. |
| **Impact** | Friend demo scenario shows limited options. |
| **Code** | `mocks/__init__.py:L394-L436`: `MOCK_RESTAURANTS_FRIENDS` has 3 items. |
| **Evidence** | Compare: `MOCK_ACTIVITIES_FRIENDS` has 4 items (L162-L220), `MOCK_RESTAURANTS_FAMILY` has 12 items (L222-L392). |
| **Fix** | Add 3+ more friend-scene restaurants |

---

## R9 — Feasibility Check Blocks AFTER Generation 🟢

| Field | Value |
|-------|-------|
| **Description** | `_validate_feasibility()` is called after `itinerary_generate` has already completed. If check fails, the Exception blocks output but the LLM/mock work was already done. |
| **Impact** | Wasted API calls when feasibility fails. |
| **Code** | `orchestrator/orchestrator.py:L437-L443`: generate runs at L437, feasibility check at L443. |
| **Evidence** | Order: generate → validate → transition. If validation fails, generate was already executed. |
| **Fix** | Move feasibility constraints into the generate prompt as pre-conditions |

---

## R10 — ShareMessageSchema Has Zero Backend Implementation 🟢

| Field | Value |
|-------|-------|
| **Description** | `ShareMessageSchema` is defined in `schemas/__init__.py` but no code anywhere in the backend generates a share message, exports a long-image, or creates a share URL. |
| **Impact** | Social sharing feature cannot be demonstrated. P0 per PRD v3. |
| **Code** | `schemas/__init__.py:L275-L285`: schema only. |
| **Evidence** | `grep -r "ShareMessage" .` returns only the schema definition file. No generator, no endpoint, no integration. |
| **Fix** | Build share generator when frontend team confirms format requirements |
