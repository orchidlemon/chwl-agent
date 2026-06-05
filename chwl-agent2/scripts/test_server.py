"""
独立 FastAPI 测试服务器

供前端开发联调使用。
暴露 REST 接口 + SSE 事件流。

用法:
    python scripts/test_server.py
    # 访问 http://localhost:8000/docs 查看 Swagger 文档
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

sys.path.insert(0, "..")

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.tool_registry import ToolRegistry
from mocks import MockBackend
from orchestrator.orchestrator import Orchestrator, _d

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Meituan Agent - Test Server", version="0.1.0")

# ─── 全局依赖 ────────────────────────────────────────────────

backend = MockBackend()
tools = ToolRegistry()

# 注册所有 mock handler
for name in ["location", "user_context", "weather", "activities_search",
             "restaurants_search", "route_check", "candidates_score",
             "itinerary_generate", "booking_execute", "booking_status",
             "itinerary_replan"]:
    handler = getattr(backend, f"handle_{name}")
    tools.register_mock(name, handler)

orchestrator = Orchestrator(tools)

# SSE 订阅者
sse_queues: dict[str, asyncio.Queue] = {}


# ─── Request/Response Models ─────────────────────────────────

class StartPlanRequest(BaseModel):
    user_input: str
    session_id: str = ""


class ModifyRequest(BaseModel):
    type: str = ""         # replace | delete | insert
    node_id: str = ""
    target_node_id: str = ""
    new_resource: dict = {}


class SentimentRequest(BaseModel):
    type: str = ""
    description: str = ""
    node_id: str = ""


class ConfirmRequest(BaseModel):
    request_id: str
    approved: bool
    modifications: dict = {}


# ─── SSE 事件流 ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    # 订阅所有事件，推送到 SSE 队列
    @orchestrator.event_bus.subscribe("*")
    async def push_to_sse(ctx, event):
        # 找到所有匹配的 SSE 队列
        session_id = event.get("session_id", "")
        if session_id in sse_queues:
            await sse_queues[session_id].put(event)


@app.get("/api/events/{session_id}")
async def event_stream(session_id: str):
    """SSE 事件流"""
    if session_id not in sse_queues:
        sse_queues[session_id] = asyncio.Queue()

    queue = sse_queues[session_id]

    async def generate():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if session_id in sse_queues:
                del sse_queues[session_id]

    return StreamingResponse(generate(), media_type="text/event-stream")


# ─── REST 接口 ──────────────────────────────────────────────


@app.post("/api/orchestrator/plan")
async def start_plan(req: StartPlanRequest):
    """开始规划"""
    try:
        session_id = await orchestrator.start_session(
            req.user_input, req.session_id or ""
        )
        return {"session_id": session_id, "status": "planning"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orchestrator/session/{session_id}")
async def get_session(session_id: str):
    """查询会话状态"""
    try:
        status = await orchestrator.get_status(session_id)
        return _d(status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/confirm/{session_id}")
async def confirm(session_id: str):
    """确认行程，一键安排"""
    try:
        result = await orchestrator.confirm_itinerary(session_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/modify/{session_id}")
async def modify(session_id: str, req: ModifyRequest):
    """修改行程"""
    from core.models import ItineraryModification
    try:
        mod = ItineraryModification(
            type=req.type,
            node_id=req.node_id,
            target_node_id=req.target_node_id,
            new_resource=req.new_resource,
        )
        itinerary = await orchestrator.modify_itinerary(session_id, mod)
        return _d(itinerary)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/sentiment/{session_id}")
async def report_sentiment(session_id: str, req: SentimentRequest):
    """上报情绪/异常"""
    from core.models import UserSentiment
    try:
        sentiment = UserSentiment(
            type=req.type,
            description=req.description,
            node_id=req.node_id,
        )
        result = await orchestrator.handle_user_sentiment(session_id, sentiment)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/resolve")
async def resolve(req: ConfirmRequest):
    """响应用户确认"""
    try:
        result = await orchestrator.resolve_confirmation(
            req.request_id, req.approved, req.modifications
        )
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/cancel/{session_id}")
async def cancel(session_id: str):
    """取消会话"""
    try:
        result = await orchestrator.cancel_session(session_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Mock API 透传 ──────────────────────────────────────────

@app.get("/api/mock/{tool_name}")
@app.post("/api/mock/{tool_name}")
async def mock_api(tool_name: str, payload: dict = {}):
    """透传调用 mock backend（调试用）"""
    result = await backend.handle(tool_name, payload)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
