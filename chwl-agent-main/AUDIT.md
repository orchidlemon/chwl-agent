# Independent Audit Report

> Date: 2026-06-05
> Method: Read every file in the repo. No assumptions from prior conversations.
> Rule: Every claim must cite file path + line number. No evidence = UNKNOWN.

---

## 1. Final Demo Should Run — Full Flow

Based on PRD (mvp_prd_v3.md) and architecture design (architecture_design.md), the demo must complete:

```
User types request → Backend plans itinerary → Frontend shows cards
→ User clicks confirm → Backend fulfills bookings → Frontend shows progress
→ Anomaly triggers → Backend replans → User confirms → Frontend updates
```

### Demostrable capabilities required:
1. Natural language input → structured itinerary cards
2. One-click fulfillment (parallel booking)
3. Background monitoring + anomaly-triggered replanning
4. Cancellation with resource loss warning

---

## 2. Current Implementation Reality

### 2.1 What Actually Exists (Verified in Code)

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| Itinerary FSM (7 states) | `orchestrator/orchestrator.py` + `core/state_machine.py` | L27-L42 | ✅ EXISTS |
| Node FSM (10 states) | `core/state_machine.py` | L31-L44 | ✅ EXISTS |
| Tool registry (11 tools) | `core/tool_registry.py` | L106-L125 | ✅ EXISTS |
| Parallel data fetch | `orchestrator/orchestrator.py` | L335-L342 | ✅ EXISTS |
| MemoryStore 3-tier | `core/memory_store.py` | L55-L302 | ✅ EXISTS |
| EventBus pub/sub | `orchestrator/event_bus.py` | L18-L94 | ✅ EXISTS |
| ConfirmationGateway | `orchestrator/confirmation_gateway.py` | L38-L145 | ✅ EXISTS |
| BackgroundWatch | `orchestrator/background_watch.py` | L110-L371 | ✅ EXISTS |
| FallbackRepair | `core/fallback_repair.py` | L24-L186 | ✅ EXISTS |
| OutputValidator | `core/output_validator.py` | L52-L276 | ✅ EXISTS |
| LLMClient (DeepSeek) | `core/llm_client.py` | L20-L116 | ✅ EXISTS |
| LLMPlanner (5 handlers) | `core/llm_planner.py` | L173-L330 | ✅ EXISTS |
| 6 frontend JSON schemas | `schemas/__init__.py` | L49-L311 | ✅ EXISTS |
| FastAPI server (8 endpoints + SSE) | `scripts/test_server.py` | L31-L225 | ✅ EXISTS |
| CLI chat demo | `scripts/chat_demo.py` | L57-L332 | ✅ EXISTS |
| CLI debug demo | `scripts/cli_demo.py` | FULL | ✅ EXISTS |
| MockBackend (all 11 handlers) | `mocks/__init__.py` | L478-L828 | ✅ EXISTS |
| Mock data: family activities | `mocks/__init__.py` | L46-L158 | ✅ 8 items |
| Mock data: family restaurants | `mocks/__init__.py` | L222-L392 | ✅ 12 items |
| Mock data: friend activities | `mocks/__init__.py` | L162-L220 | ✅ 4 items |
| Mock data: friend restaurants | `mocks/__init__.py` | L394-L436 | ✅ 3 items |
| EnvSimulator (3 presets) | `mocks/env_simulator.py` | L53-L285 | ✅ EXISTS |
| Teammate API adapter | `mocks/teammate_adapter.py` | L31-L210 | ✅ EXISTS |
| 110 tests | `tests/` | ALL | ✅ EXISTS |
| Mock API server (zero-dep) | `mock_api/app.py` | L247-L530 | ✅ EXISTS |

### 2.2 What Does NOT Exist (Verified Missing)

| Feature | Expected | Evidence |
|---------|----------|----------|
| Clarification round | PRD requires 1-round structured clarification before planning | No code found. chat_demo.py L158-L160 immediately starts session. |
| Draft-stage operations | Replace/delete/insert/pin exposed to user | `ItineraryModification` model exists (`core/models.py` L209-L217`), but no endpoint in `chat_demo.py` or `test_server.py` exposes these actions via UI. |
| Real-time CoT streaming | PRD requires streaming LLM thought process | `chat_demo.py` L116-L152 event handlers show static status lines, not streaming LLM thought. |
| Social sharing | P0 feature: export long-image, share to WeChat | `ShareMessageSchema` defined (`schemas/__init__.py` L275-L285`) but no generation logic exists anywhere. |
| User-reported conflict flow | Multi-select reasons → NL input → recovery plans | UNKNOWN — no code found |
| Feasibility check blocking | PRD v3 L41: "unqualified plans must not be output" | `orchestrator/orchestrator.py` L482-L496 has a check that raises ValueError, but it runs AFTER itinerary_generate has already been called. The check can fail but the plan was already generated. |

### 2.3 Key Behaviors Verified from Code

| Behavior | Evidence |
|----------|----------|
| Feasibility check runs 6 rules | `orchestrator/orchestrator.py` L1233-L1328 `_validate_feasibility` — checks commute ratio, activity time, transport match, node count, buffer time, child curfew, weather |
| Feasibility check can block output | `orchestrator/orchestrator.py` L490-L496 — raises ValueError if `fc.passed` is False |
| LLM hallucination filtered | `core/llm_planner.py` L268-L280 — validates `poi_id` against known set |
| 5-level fallback | `orchestrator/orchestrator.py` L841-L931 `_handle_node_failure` |
| Background watch auto-starts | `orchestrator/orchestrator.py` L537-L540 `_build_watch_configs` called in `_phase2_execute` |
| Resource loss warning emitted | `orchestrator/orchestrator.py` L276-L294 — cancel_session emits `resource_loss_warning` |
| MemoryStore integrated | `orchestrator/orchestrator.py` L358-L372 — Phase 1 writes session_facts |

---

## 3. Frontend-Backend Missing Connections

| Gap | Details | Evidence |
|-----|---------|----------|
| SSE event format mismatch | `test_server.py` L83-L88 forwards raw EventBus events. EventBus events have `id`/`type`/`timestamp`/`session_id`/`data` (`event_bus.py` L66-L72`). Frontend schemas (`schemas/` L303-L311`) define `SCHEMA_TYPE_MAP` but no code converts EventBus events to schema-validated outputs. | No `OutputValidator` call in test_server.py |
| `plan_complete` event → `ItineraryPlanSchema` | Event carries raw `ItineraryData` model dump, not schema-validated `ItineraryPlanSchema` | `orchestrator.py` L439-L441 emits `itinerary.model_dump()`, not validated via OutputValidator |
| Fulfillment progress → `FulfillmentStatusSchema` | No code generates `FulfillmentStatusSchema`-compliant events | No `FulfillmentStatusSchema` creation in orchestrator or test_server |
| Risk modal → `RiskModalSchema` | `queue_too_long` event carries raw data, not schema-validated modal | `background_watch.py` L346-L371 emits watch_alert, not RiskModalSchema |
| Alternative nodes → `AlternativeNodesSchema` | `alternatives_ready` event carries raw list, not schema-validated | `orchestrator.py` L828-L834 emits raw alternatives dict |

---

## 4. Backend-Agent Workflow Missing Connections

| Gap | Details | Evidence |
|-----|---------|----------|
| `conversation_history` not passed to LLM | `chat_demo.py` L163-218 `handle_message` never reads `self.conversation_history` | Variable declared L48 but never used |
| `_phase1_task` and `_phase2_task` never assigned | `chat_demo.py` L71-L72 initialized to None, never set to `asyncio.create_task()` | `_cancel_tasks()` L293-L298 always cancels None |
| `chat_demo.py` completed state lock | `handle_message` L203-L208 — completed state only matches "累"/"困"/"改", everything else returns hardcoded string | User input "老婆在减肥" or "看看方案" after completion returns "全部预约好了" |
| `chat_demo.py` `needs_replan` state doesn't trigger replanning | L210-L216 — needs_replan only offers cancel+restart, not actual replan confirmation | ConfirmationGateway events are never checked |
| `test_server.py` not subscribed to watch_alert events | `orchestrator.py` L104 subscribes `_on_watch_alert` inside Orchestrator, but test_server creates its own Orchestrator | L46 creates `Orchestrator(tools)` — watch_alert subscription should work if Orchestrator init handles it |

---

## 5. Top 10 Risk Points (Pre-Submission)

| # | Risk | Impact | Evidence |
|---|------|--------|----------|
| **R1** | `chat_demo.py` state lock after fulfillment | Demo halts — user cannot interact after booking | L203-L208: completed branch only matches 3 keywords |
| **R2** | `_phase1_task`/`_phase2_task` never assigned | `_cancel_tasks()` has no effect | L71-L72: initialized None, L293-L298: cancels None |
| **R3** | Server uses hash-based scoring (not LLM) | Demo shows deterministic, non-LLM results | `test_server.py` uses MockBackend handle_candidates_score which uses hash (`mocks/__init__.py` L644-L675`) |
| **R4** | No conversation history → no context | Multi-turn dialogue broken | `chat_demo.py` L48: variable declared, never used |
| **R5** | SSE events not schema-validated | Frontend cannot rely on field presence | No `OutputValidator` call in test_server.py |
| **R6** | `needs_replan` state has no recovery path | Replan triggered but user sees no options | `chat_demo.py` L210-L216: only cancel+restart |
| **R7** | DeepSeek API may be unreachable | Demo depends on external API | `llm_client.py` L76-103: network error → exception |
| **R8** | Friend restaurant data insufficient | Friend scenario has only 3 options | `mocks/__init__.py` L394-L436: 3 items |
| **R9** | Feasibility check blocks AFTER generation | Plan is partially generated before validation fails | `orchestrator.py` L437-L443: itinerary_generate called BEFORE feasibility check |
| **R10** | `ShareMessageSchema` has no backend | Social share feature = 0% | No generator function anywhere |

---

## 6. Completion Estimate (Percentage)

Measured against PRD v3 P0+P1 requirements and demo-readiness.

| Category | Weight | Estimate | Method |
|----------|--------|----------|--------|
| Core engine (FSM, tools, events) | 25% | 90% | All 110 tests exist and cover this |
| Data models + schemas | 10% | 85% | All models defined, 6 schemas defined |
| Mock data (volume) | 10% | 60% | Family 8+12 ✅, Friends 3+4 ❌, Routes 4 ❌ |
| LLM integration | 15% | 70% | Client+Planner exist, default mode is hash mock |
| Frontend-ready API | 15% | 50% | REST+SSE exist, schema validation missing |
| Interactive demo flow | 15% | 30% | chat_demo has 4 state logic bugs |
| Test completeness | 5% | 60% | No E2E test, no LLM mock test |
| Documentation | 5% | 80% | PRDs + audit + contract exist |

**Weighted total: 65%**

### Not counted (deliberately excluded):
- UI implementation (frontend team's work)
- User management (P2)
- Map integration (P2)
- Evaluation benchmark (P2)

### Verdict: Demo-ready with caveats

The test_server.py FastAPI can serve a basic demo flow:
```
plan → get_status → confirm → SSE events
```

But the chat_demo.py (LLM mode) has 4 blocking logic bugs that prevent fluid interaction. The default hash-based scoring means LLM value is not demonstrated unless DeepSeek is manually configured and reachable.

**To ship a demo:** Use `test_server.py` (FastAPI port 8000) with frontend consuming SSE + REST. Do NOT rely on `chat_demo.py` as-is.
