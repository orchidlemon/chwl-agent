# Demo Flow — Complete User Walkthrough

> Based on code audit, not assumptions.
> Backend: `test_server.py` (FastAPI, port 8000)

---

## Flow 1: Family Scene — Happy Path (主演示)

**Purpose**: Show full lifecycle: plan → confirm → fulfill → done

### Step 1: Start Planning

| Item | Detail |
|------|--------|
| **User says** | "下午带老婆孩子出去玩，别太远" |
| **Frontend action** | `POST /api/orchestrator/plan` with `{"user_input": "下午带老婆孩子出去玩，别太远"}` |
| **Expected response** | `{"session_id": "sess_xxx", "status": "planning"}` |
| **Backend code** | `test_server.py:L116-L125` |
| **Evidence** | Returns immediately, Phase 1 runs async |

### Step 2: Subscribe to SSE

| Item | Detail |
|------|--------|
| **Frontend action** | `GET /api/events/{session_id}` |
| **Expected response** | `text/event-stream` |
| **Events received in 2-5s** | `status_update` (×3-5): "正在获取当前位置…" → "已找到 8 个活动和 12 个餐厅…" → "正在生成行程方案…" |
| **Backend code** | `test_server.py:L80-L88` |

### Step 3: Receive Plan

| Item | Detail |
|------|--------|
| **Event type** | `plan_complete` |
| **Data** | `{"itinerary": {"itinerary_id": "...", "nodes": [...], "total_duration_min": 215, "summary": "共 3 个活动…", "feasibility_check": {"passed": true}}}` |
| **Node structure** | 3 nodes: `main_activity` → `restaurant` → `optional_activity` with `scheduled_start`, `scheduled_end`, `poi_name`, `status` |
| **Backend code** | `orchestrator.py:L439-L441` |
| **Frontend renders** | 3 cards in timeline |

### Step 4: Confirm Itinerary

| Item | Detail |
|------|--------|
| **User clicks** | "一键安排" |
| **Frontend action** | `POST /api/orchestrator/confirm/{session_id}` |
| **Expected response** | `{"status": "executing", "session_id": "sess_xxx"}` |
| **Backend code** | `test_server.py:L140-L149`, `orchestrator.py:L206-L233` |

### Step 5: Watch Fulfillment Progress

| Item | Detail |
|------|--------|
| **Events received** | `booking_status_changed` (×3-6 over 10-15s) |
| **Status sequence** | Each node: `queued` → `processing` → `confirmed` |
| **Backend code** | `orchestrator.py:L630-L675` _monitor_booking |
| **Mock timing** | `mocks/__init__.py:L785-L798`: queued(0s) → processing(5s) → confirmed(10s) |

### Step 6: Fulfillment Complete

| Item | Detail |
|------|--------|
| **Event type** | `execution_complete` |
| **Expected response** | Frontend shows "全部预约成功" |
| **Backend code** | `orchestrator.py:L562-L568` |

---

## Flow 2: Family Scene — Queue Spike Replan

**Purpose**: Show anomaly detection + alternative selection + replan

### Steps 1-6: Same as Flow 1

At Step 5 (booking_status_changed), when restaurant node's queue exceeds threshold:

| Item | Detail |
|------|--------|
| **Event type** | `queue_too_long` then `node_failed` then `alternatives_ready` |
| **Alternatives data** | `{"recommended": [2 items], "more_options": [N items]}` |
| **Frontend shows** | Risk modal with 2 recommended + more options + "我自己看看" |
| **Backend code** | `background_watch.py:L236-L260` (queue check), `orchestrator.py:L813-L837` (alternatives) |

### User Selects Alternative

| Item | Detail |
|------|--------|
| **Frontend action** | `POST /api/orchestrator/resolve` with `{"request_id": "cfm_xxx", "approved": true, "modifications": {"value": "res_fam_003"}}` |
| **Event received** | `node_replaced` |
| **Backend code** | `orchestrator.py:L964-L1026` _on_alternative_response |

---

## Flow 3: Cancel with Resource Loss Warning

### Start

| Item | Detail |
|------|--------|
| **User clicks** | "取消行程" |
| **Frontend action** | `POST /api/orchestrator/cancel/{session_id}` |
| **Event received** | `resource_loss_warning` with `{"losses": [{"name": "乐高探索中心", "booking_ref": "BK-xxx", "type": "attraction"}]}` |
| **Frontend shows** | Loss warning dialog |
| **Backend code** | `orchestrator.py:L276-L294` |

### User Confirms Cancel

| Item | Detail |
|------|--------|
| **Frontend action** | `POST /api/orchestrator/cancel/{session_id}` again |
| **Event received** | `session_cancelled` |
| **Frontend clears** | Back to initial state |
| **Backend code** | `orchestrator.py:L296-L306` |

---

## Flow 4: User Sentiment → Replan (Fatigue)

### Precondition: Fulfillment done

| Item | Detail |
|------|--------|
| **User says** | "孩子累了" |
| **Frontend action** | `POST /api/orchestrator/sentiment/{session_id}` with `{"type": "tired", "description": "孩子累了"}` |
| **Expected response** | `{"status": "replanning"}` |
| **Backend code** | `test_server.py:L171-L186`, `orchestrator.py:L239-L256` |

---

## Backend Commands

### Start test server (mock mode, no LLM):
```
cd C:\Users\hgghhy\meituan-agent
venv\Scripts\activate
python scripts\test_server.py
```

### Start teammate mock API (optional):
```
cd C:\Users\hgghhy\meituan-agent\mock_api
python -B app.py
```

### Verify server is alive:
```
curl http://localhost:8000/api/health
```
→ `{"status": "ok", "service": "meituan-local-life-mock-api", "version": "llm-simulator-v2"}`
