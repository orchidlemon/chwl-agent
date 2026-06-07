"""FastAPI Agent Backend — Port 8001"""
import json
import logging
import os
import sys

from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path, override=True)
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).info(
    f".env loaded from {_env_path} | provider={os.getenv('LLM_PROVIDER','not set')} | key={'SET' if os.getenv('DEEPSEEK_API_KEY') or os.getenv('ANTHROPIC_API_KEY') else 'NOT SET'}"
)

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.schemas import (
    ChatRequest, ConfirmationResolveRequest, ExceptionConfirmRequest, InjectEventTextRequest,
    MemoryUpdateRequest, NodeActionRequest, NodeCheckinRequest, NodeReplaceRequest, NodeUpdateRequest,
    PlanRequest, ReportRequest,
)
from backend.session import SessionManager
from backend.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="美团本地生活 Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = SessionManager()
orchestrator = Orchestrator(manager)


# ── SSE helper ───────────────────────────────────────────────────────

def sse_response(gen):
    async def stream():
        async for event in gen:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"stream_end\"}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Session ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "meituan-agent", "version": "2.0.0"}


@app.post("/agent/session")
async def create_session():
    sid = manager.create()
    logger.info(f"Session created: {sid[:8]}")
    return {"session_id": sid}


@app.delete("/agent/{session_id}/reset")
async def reset_session(session_id: str):
    await orchestrator.stop_background_watch(session_id)
    await orchestrator.cancel_confirmations(session_id)
    manager.reset(session_id)
    try:
        from backend.state_writer import clear_state
        clear_state(session_id)
    except Exception:
        logger.warning("Failed to clear frontend state for %s", session_id, exc_info=True)
    return {"status": "reset", "session_id": session_id}


# ── Memory ────────────────────────────────────────────────────────────

@app.get("/agent/{session_id}/memory")
async def get_memory(session_id: str):
    s = manager.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s["memory"]


@app.post("/agent/{session_id}/memory")
async def update_memory(session_id: str, req: MemoryUpdateRequest):
    sid, s = manager.get_or_create(session_id)
    if req.scope not in ("session_facts", "confirmed_preferences", "derived_preferences"):
        raise HTTPException(400, f"Invalid scope: {req.scope}")
    manager.update_memory(sid, req.scope, req.updates)
    return {"status": "updated", "memory": s["memory"]}


# ── Chat (phase-aware, primary entry point) ───────────────────────────

@app.post("/agent/{session_id}/chat")
async def chat(session_id: str, req: ChatRequest):
    """
    Phase-aware chat handler.
    phase_hint="start_plan" + original_request lets backend plan correctly
    even when session was reset (e.g. dev reload).
    """
    _, session = manager.get_or_create(session_id)
    if req.client_itinerary and (req.phase_hint == "route_replan" or not manager.get_itinerary(session_id)):
        manager.set_itinerary(session_id, req.client_itinerary)
        session["phase"] = "monitoring"
        manager.add_monitor_event(
            session_id,
            "main_agent",
            "Recovered visible itinerary from frontend request",
            "session_recovered",
        )
    logger.info(f"Chat: {session_id[:8]} phase={manager.get_phase(session_id)} "
                f"hint={req.phase_hint} msg={req.message[:30]}")
    return sse_response(
        orchestrator.run_chat(session_id, req.message,
                              phase_hint=req.phase_hint,
                              original_request=req.original_request)
    )


# ── Planning (SSE stream) - legacy ───────────────────────────────────

@app.post("/agent/{session_id}/plan")
async def plan(session_id: str, req: PlanRequest):
    manager.get_or_create(session_id)
    logger.info(f"Plan started: {session_id[:8]} scenario={req.scenario}")
    return sse_response(orchestrator.run_plan(session_id, req.dict()))


# ── Fulfillment (SSE stream) ──────────────────────────────────────────

@app.post("/agent/{session_id}/fulfill")
async def fulfill(session_id: str):
    manager.get_or_create(session_id)   # never 404 — recreates if lost after restart
    logger.info(f"Fulfill started: {session_id[:8]}")
    return sse_response(orchestrator.run_fulfill(session_id))


# ── Exception confirm (SSE stream) ───────────────────────────────────

@app.post("/agent/{session_id}/exception/confirm")
async def confirm_exception(session_id: str, req: ExceptionConfirmRequest):
    manager.get_or_create(session_id)
    return sse_response(orchestrator.run_exception_confirm(session_id, req.dict()))


@app.post("/agent/{session_id}/confirmation/resolve")
async def resolve_confirmation(session_id: str, req: ConfirmationResolveRequest):
    s = manager.get(session_id)
    if not s:
        return {"resolved": False, "request": None}
    resolved = await orchestrator.confirmation_gateway.resolve(
        session_id,
        req.request_id,
        req.approved,
        req.modifications or {},
        reason=req.reason or "",
    )
    return {"resolved": bool(resolved), "request": resolved.to_dict() if resolved else None}


# ── Node actions ──────────────────────────────────────────────────────

@app.post("/agent/{session_id}/node/action")
async def node_action(session_id: str, req: NodeActionRequest):
    s = manager.get(session_id)
    if not s:
        return {"error": "session_expired"}

    itinerary = manager.get_itinerary(session_id)
    target = next((n for n in itinerary if n["id"] == req.node_id), None)

    if target and req.action in ("delete", "replace"):
        if req.force:
            pending = (
                orchestrator.confirmation_gateway.get_pending(session_id, req.request_id)
                if req.request_id else
                orchestrator.confirmation_gateway.find_latest_pending(session_id, "node_action")
            )
            if pending and pending.get("context", {}).get("node_id") == req.node_id:
                await orchestrator.confirmation_gateway.resolve(
                    session_id,
                    pending["request_id"],
                    True,
                    {"action": req.action},
                )

        # Hard-block: completed_lock nodes are immutable
        if target.get("completed_lock"):
            return {"blocked": True, "reason": "该节点已完成打卡，无法修改", "nodes": itinerary}

        # Soft-lock warning: booked nodes need user confirmation first
        if target.get("soft_lock") and not req.force:
            node_type = target.get("type", "activity")
            node_name = target.get("name", "该节点")
            if node_type == "restaurant":
                reason = (f"「{node_name}」已为您取号排队，"
                          f"取消后排队资格将立即作废，需要重新排队才能入场。")
            else:
                reason = (f"「{node_name}」预约资格已锁定，"
                          f"取消后名额将立即释放，当前时段余量有限，再次预约成功率较低。")
            confirm_req = await orchestrator.confirmation_gateway.request(
                session_id,
                "node_action",
                {"node_id": req.node_id, "action": req.action},
                title="确认释放已锁定资源",
                description=reason,
                options=[
                    {"label": "确认继续", "value": "approve", "recommended": False},
                    {"label": "保留原方案", "value": "reject", "recommended": True},
                ],
                timeout_s=120,
            )
            return {
                "soft_lock_warning": True,
                "request_id": confirm_req.request_id,
                "reason":  reason,
                "node_id": req.node_id,
                "action":  req.action,
                "nodes":   itinerary,
            }

    nodes = manager.apply_node_action(session_id, req.node_id, req.action)
    return {"nodes": nodes}

@app.post("/agent/{session_id}/node/replace")
async def node_replace(session_id: str, req: NodeReplaceRequest):
    manager.get_or_create(session_id)
    return await orchestrator.run_node_replace(session_id, req.node_id, req.replacement)


# ── Taxi dispatch proxy ───────────────────────────────────────────────

@app.post("/agent/{session_id}/taxi/dispatch")
async def taxi_dispatch(session_id: str):
    from backend import tools as _t
    return await _t.dispatch_taxi()


# ── Route estimate proxy ──────────────────────────────────────────────

@app.get("/agent/{session_id}/route/estimate")
async def route_estimate(session_id: str, request: Request):
    from backend import tools as _t
    params = dict(request.query_params)
    return await _t.get_route(
        params.get("from", ""), params.get("to", ""), params.get("mode", "taxi")
    )


# ── Node checkin (user marks node as visited) ─────────────────────────

@app.post("/agent/{session_id}/node/update")
async def node_update(session_id: str, req: NodeUpdateRequest):
    manager.get_or_create(session_id)
    allowed = {"timeStart", "timeEnd", "transit"}
    updates = {k: v for k, v in req.updates.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No supported node updates")
    return await orchestrator.run_node_update(session_id, req.node_id, updates)

@app.post("/agent/{session_id}/node/checkin")
async def node_checkin(session_id: str, req: NodeCheckinRequest):
    s = manager.get(session_id)
    if not s:
        return {"message": "session_expired", "next_requires_taxi": False}
    result = await orchestrator.run_node_checkin(session_id, req.node_id)
    return result


# ── User report ───────────────────────────────────────────────────────

@app.post("/agent/{session_id}/report")
async def report_issue(session_id: str, req: ReportRequest):
    s = manager.get(session_id)
    if not s:
        return {"status": "ok"}
    result = await orchestrator.run_report(session_id, req.type)
    return result


# ── Queue advice ──────────────────────────────────────────────────────

@app.get("/agent/{session_id}/queue-advice")
async def queue_advice(session_id: str):
    s = manager.get(session_id)
    if not s:
        return {"advices": []}
    advices = await orchestrator.run_queue_advice(session_id)
    return {"advices": advices}


# ── Monitor state (reads live queue/weather from Mock API) ────────────

@app.get("/agent/{session_id}/monitor/state")
async def monitor_state(session_id: str):
    s = manager.get(session_id)
    if not s:
        return {"events": [], "itinerary": [], "pending_chat_event": None,
                "queue_history": {}, "weather": None}

    from backend import tools as _tools
    import asyncio as _asyncio

    # Fetch live data from Mock API in parallel
    itinerary = s.get("itinerary", [])
    rest_ids = [n.get("poiId", "") for n in itinerary if n.get("type") == "restaurant"]

    live_weather, *queue_results = await _asyncio.gather(
        _tools.get_weather(),
        *[_tools.get_queue_status(pid) for pid in rest_ids],
    )
    live_queues = {pid: q for pid, q in zip(rest_ids, queue_results)}

    # Update queue history with fresh data
    for poi_id, q in live_queues.items():
        wait = q.get("estimated_wait_min", 0)
        if wait > 0:
            manager.update_queue_history(session_id, poi_id, wait)

    state = manager.get_monitor_state(session_id,
                                      live_queues=live_queues,
                                      live_weather=live_weather)
    pending_msg = manager.pop_pending_monitor_msg(session_id)
    state["pending_chat_event"] = pending_msg
    return state


# ── Simulator advance ─────────────────────────────────────────────────

@app.post("/agent/{session_id}/simulator/advance")
async def simulator_advance(session_id: str):
    s = manager.get(session_id)
    if not s:
        return {"status": "no_session"}
    result = await orchestrator.run_simulator_advance(session_id)
    return result


# ── Simulator inject (natural language → event) ───────────────────────

@app.post("/agent/{session_id}/simulator/inject")
async def simulator_inject(session_id: str, req: InjectEventTextRequest):
    s = manager.get(session_id)
    if not s:
        return {"status": "no_session"}
    result = await orchestrator.run_simulator_inject(session_id, req.text)
    return result


# ── Frontend State (临时 JSON 文件，前端唯一数据源) ─────────────────────

@app.get("/agent/{session_id}/state")
async def get_state(session_id: str):
    """
    返回前端渲染所需的完整状态。
    前端不维护本地业务状态，所有渲染数据从此接口拉取。
    数据由 backend/state_writer.py 写入临时 JSON 文件，此处直接返回。
    """
    from backend.state_writer import read_state
    state = read_state(session_id)
    if not state.get("session_id"):
        manager.get_or_create(session_id)
        state["session_id"] = session_id
    return state


@app.get("/agent/{session_id}/state/itinerary")
async def get_state_itinerary(session_id: str):
    """只返回行程节点，用于局部刷新。"""
    from backend.state_writer import read_state
    state = read_state(session_id)
    return state.get("itinerary", {"summary": "", "cot": [], "nodes": []})


@app.get("/agent/{session_id}/state/monitor")
async def get_state_monitor(session_id: str):
    """返回监控面板数据（天气/队列/告警），支持前端定时轮询。"""
    from backend.state_writer import read_state, update_monitor
    from backend import tools as _tools
    import asyncio as _asyncio

    state = read_state(session_id)
    nodes = state.get("itinerary", {}).get("nodes", [])
    rest_ids = [n.get("poiId", "") for n in nodes if n.get("type") == "restaurant" and n.get("poiId")]

    live_weather, *queue_results = await _asyncio.gather(
        _tools.get_weather(),
        *[_tools.get_queue_status(pid) for pid in rest_ids],
    )
    live_queues = {pid: q for pid, q in zip(rest_ids, queue_results)}
    update_monitor(session_id, weather=live_weather, queue_trends=live_queues)
    return read_state(session_id).get("monitor", {})


@app.get("/agent/{session_id}/state/profile")
async def get_state_profile(session_id: str):
    """返回用户画像（LLM 从 NL 提取的信息）。"""
    from backend.state_writer import read_state
    state = read_state(session_id)
    return state.get("user_profile", {})


# ── Itinerary read ────────────────────────────────────────────────────

@app.get("/agent/{session_id}/itinerary")
async def get_itinerary(session_id: str):
    manager.get_or_create(session_id)
    return {"nodes": orchestrator._current_itinerary(session_id)}


# ── Sandbox / Demo controls ───────────────────────────────────────────

@app.post("/agent/{session_id}/sandbox/trigger")
async def trigger_event(session_id: str, event_type: str = "queue_spike"):
    s = manager.get(session_id)
    if not s:
        return {"status": "no_session"}
    if event_type == "queue_spike":
        event = manager.trigger_queue_spike(session_id)
    elif event_type == "weather_heavy_rain":
        event = manager.trigger_weather_rain(session_id)
    else:
        raise HTTPException(400, f"Unknown event type: {event_type}")
    return {"status": "triggered", "event": event}


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    logger.info(f"Starting Agent backend on port {port}")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
