# Risk Report — Top 10 Pre-Submission Risks

> Evidence-based. Each risk cites the code location where it lives.
> Severity: 🔴 blocking / 🟡 high / 🟢 medium
> Updated: 2026-06-05

---

## ✅ 已修复

| # | 风险 | 严重性 | 修复内容 |
|---|------|--------|---------|
| R1 | chat_demo.py state lock after fulfillment | 🔴 → ✅ | 增加多分支（查看方案/新偏好/LLM回复），不再所有输入返回相同文本 |
| R2 | `_phase1_task` / `_phase2_task` never assigned | 🔴 → ✅ | 移除未使用的变量和方法 |
| R5 | SSE events not schema-validated | 🟡 → 待确认 | OutputValidator 存在但 test_server.py 未集成（需前端联调时确认） |
| R8 | Friend restaurant data only 3 items | 🟢 → ✅ | 已扩展到 6 家 |

---

## R1 — chat_demo.py State Lock After Fulfillment ✅ 已修复

| Field | Value |
|-------|-------|
| **Fix** | Increased branches: cancel, new preference, view plan, LLM fallback |
| **Commit** | `2cbca2e` |

---

## R2 — `_phase1_task` / `_phase2_task` Never Assigned ✅ 已修复

| Field | Value |
|-------|-------|
| **Fix** | Removed unused variables and `_cancel_tasks()` method |
| **Commit** | `2cbca2e` |

---

## R3 — test_server.py Uses Hash-Based Scoring, Not LLM 🟡

| Field | Value |
|-------|-------|
| **Description** | `test_server.py` registers `MockBackend` handlers, including `handle_candidates_score` which uses `hash(poi_id) % 35 + 60` to generate scores |
| **Impact** | Demo shows deterministic, non-LLM results |
| **Code** | `scripts/test_server.py:L35-L44`, `mocks/__init__.py:L644-L675` |
| **Status** | 🟡 By design for test server. LLM scoring available via `chat_demo.py` |

---

## R4 — No Conversation History Passed to LLM ✅ 已修复

| Field | Value |
|-------|-------|
| **Fix** | `handle_message` now appends to `conversation_history`, passes last 3 rounds to `generate_response` |
| **Commit** | `2cbca2e` |

---

## R5 — SSE Events Not Schema-Validated 🟡

| Field | Value |
|-------|-------|
| **Description** | `test_server.py` forwards raw EventBus events without validating against `schemas/__init__.py` models |
| **Code** | `scripts/test_server.py:L80-L88` |
| **Status** | 🟡 Low priority — frontend team can validate on client side. OutputValidator exists as fallback. |

---

## R6 — `needs_replan` State Has No Recovery Path ✅ 已修复

| Field | Value |
|-------|-------|
| **Fix** | `needs_replan` branch now shows adjusted plan and asks for confirmation |
| **Commit** | `2cbca2e` |

---

## R7 — DeepSeek API May Be Unreachable 🟡

| Field | Value |
|-------|-------|
| **Description** | `LLMClient.chat()` raises on network failure. `test_server.py` avoids this by using hash-based mock, but `chat_demo.py` depends on LLM. |
| **Code** | `core/llm_client.py:L76-L103` |
| **Status** | 🟡 Known. Demo should use `test_server.py` (hash-based) to avoid dependency |

---

## R8 — Friend Restaurant Data Only 3 Items ✅ 已修复

| Field | Value |
|-------|-------|
| **Fix** | Added 3 more restaurants (精酿啤酒餐吧, 日式居酒屋, 川渝火锅) |
| **Commit** | `2cbca2e` |

---

## R9 — Feasibility Check Blocks AFTER Generation 🟢

| Field | Value |
|-------|-------|
| **Description** | `_validate_feasibility()` is called after `itinerary_generate` has already run |
| **Code** | `orchestrator/orchestrator.py:L437-L443` |
| **Status** | 🟢 Low impact. Generation is fast (mock). For LLM mode this wastes an API call |

---

## R10 — ShareMessageSchema Has Zero Backend Implementation 🟢

| Field | Value |
|-------|-------|
| **Description** | `ShareMessageSchema` defined but no backend generates share messages |
| **Code** | `schemas/__init__.py:L275-L285` |
| **Status** | 🟢 P2 feature, not demo-critical |
