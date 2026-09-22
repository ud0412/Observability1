"""
ai-service — LangChain Agent + SSE 스트리밍 API

POST /chat/stream
  body: {message, thread_id?, llm:{base_url, api_key, model, temperature, max_tokens, system_prompt}, stream_modes?}
  응답: text/event-stream (SSE)

SSE 이벤트:
  event: meta      {thread_id, run_id}            — 최초 1회
  event: messages  {content, tool_calls, metadata} — 토큰/메시지 청크
  event: updates   {node, messages:[{role,content}]} — 노드별 상태 갱신
  event: error     {message}
  event: done      {thread_id, run_id, content}   — 완성된 전체 답변

트레이스: 커스텀 스팬 없음. LangChain(LangchainInstrumentor) + FastAPI(FastAPIInstrumentor)
자동 계측만 사용하며, 둘 다 `opentelemetry-instrument uvicorn ...` CLI가 entry point로
활성화합니다 (Dockerfile CMD). CLI가 OTEL_* env로 프로바이더까지 구성하므로 코드에는
SDK 설정이 없습니다.

로그: SDK(OTLP)를 쓰지 않고 **그냥 stdout으로 출력**합니다. Alloy의
loki.source.docker가 docker logs와 동일한 내용을 Loki로 수집합니다(쿠버네티스에서는
loki.source.kubernetes → kubectl logs 동일). tracing.enrich_console_logging()이
stdout 라인에 traceID/spanID를 주입해 로그→트레이스 상관관계를 만들어 줍니다.
"""
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import tracing

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent import build_agent, build_model
from schemas import ChatRequest

log = logging.getLogger("ai-service")

CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB", "/data/checkpoints.sqlite")
STORE_DB = os.environ.get("STORE_DB", "/data/store.sqlite")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _msg_to_dict(msg) -> dict:
    role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(
        getattr(msg, "type", ""), getattr(msg, "type", "message")
    )
    out = {"role": role, "content": msg.content, "id": getattr(msg, "id", None)}
    if getattr(msg, "additional_kwargs", None):
        out["additional_kwargs"] = msg.additional_kwargs
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn이 로깅을 구성한 뒤의 시점 — stdout 포맷에 traceID/spanID 주입.
    # (docker logs 텍스트 == Loki 수집 텍스트를 맞추기 위해 색상도 제거)
    tracing.enrich_console_logging()
    async with (
        AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as saver,
        AsyncSqliteStore.from_conn_string(STORE_DB) as store,
    ):
        app.state.saver = saver
        app.state.store = store
        log.info("persistence ready: checkpoint=%s store=%s", CHECKPOINT_DB, STORE_DB)
        yield


app = FastAPI(title="ai-service", lifespan=lifespan)
# frontend가 서버 릴레이라 실질적으로 CORS 미필요 — 개발 편의상 전체 허용
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
# FastAPI 계측은 CLI가 FastAPI 클래스를 래핑해 자동 적용 (instrument_app 호출 불필요)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ai-service"}


async def _stream_events(req: ChatRequest, saver, store):
    thread_id = req.thread_id or uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    yield sse("meta", {"thread_id": thread_id, "run_id": run_id})

    model = build_model(req.llm)
    agent = build_agent(model, req.llm.system_prompt, saver, store)

    full_text = ""
    try:
        async for mode, payload in agent.astream(
            {"messages": [{"role": "user", "content": req.message}]},
            config=config,
            stream_mode=req.stream_modes or ["messages", "updates"],
        ):
            if mode == "messages":
                chunk, meta = payload
                content = getattr(chunk, "content", "") or ""
                if content:
                    full_text += content
                yield sse(
                    "messages",
                    {
                        "content": content,
                        "tool_calls": getattr(chunk, "tool_calls", None) or [],
                        "metadata": {
                            k: meta.get(k)
                            for k in ("langgraph_node", "langgraph_path", "langgraph_step")
                            if meta.get(k) is not None
                        },
                    },
                )
            elif mode == "updates":
                for node, upd in payload.items():
                    yield sse(
                        "updates",
                        {
                            "node": node,
                            "messages": [_msg_to_dict(m) for m in upd.get("messages", [])],
                        },
                    )
        yield sse("done", {"thread_id": thread_id, "run_id": run_id, "content": full_text})
    except Exception as exc:  # noqa: BLE001
        log.exception("stream failed (thread=%s)", thread_id)
        yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    saver = app.state.saver
    store = app.state.store

    async def gen():
        async for ev in _stream_events(req, saver, store):
            yield ev

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )