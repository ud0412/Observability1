"""
frontend — 채팅 UI + SQLite DB + ai-service SSE 릴레이

- 브라우저 → POST /api/chat → ai-service /chat/stream(SSE) → 브라우저(SSE 재전송)
  브라우저가 ai-service를 직접 호출하지 않으므로 CORS가 필요 없습니다.
- 모델 설정은 여러 개를 저장하고 활성 모델을 선택할 수 있습니다 (SQLite).
- 세션 / 메시지도 SQLite에 저장됩니다.

API:
  GET    /api/models                       저장된 모델 목록 (is_active 포함)
  POST   /api/models                       새 모델 추가 (활성으로 전환됨)
  PUT    /api/models/{id}                  모델 수정
  DELETE /api/models/{id}                  모델 삭제 (활성이면 첫 번째 모델로 전환)
  PUT    /api/active-model                 {model_id} 활성 모델 선택
  GET    /api/sessions                     대화 목록
  POST   /api/sessions                     새 대화
  GET    /api/sessions/{id}/messages       대화 메시지
  POST   /api/chat                         채팅 (SSE 릴레이)
"""
import json
import logging
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("frontend")

AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "http://localhost:8000")
STATIC_DIR = Path(__file__).parent / "static"

db.init_db()

app = FastAPI(title="langchain-chat-frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------- models ----------------
class ModelIn(BaseModel):
    name: str = ""
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt: str = ""


class ActiveModelIn(BaseModel):
    model_id: int


@app.get("/api/models")
async def list_models() -> list[dict]:
    return db.list_models()


@app.post("/api/models", status_code=201)
async def create_model(body: ModelIn) -> dict:
    try:
        new_id = db.create_model(
            body.name, body.base_url, body.api_key, body.model,
            body.temperature, body.max_tokens, body.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = db.get_model_by_id(new_id)
    db.set_active_model(new_id)  # 방금 만든 모델을 활성으로
    log.info("model created + activated: %s (%s @ %s)", model["name"], model["model"], model["base_url"])
    return model


@app.put("/api/models/{model_id}")
async def update_model(model_id: int, body: ModelIn) -> dict:
    try:
        db.update_model(
            model_id, body.name, body.base_url, body.api_key, body.model,
            body.temperature, body.max_tokens, body.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = db.get_model_by_id(model_id)
    return model or HTTPException(status_code=404, detail="model not found")


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: int) -> dict:
    db.delete_model(model_id)
    return {"ok": True}


@app.put("/api/active-model")
async def set_active_model(body: ActiveModelIn) -> dict:
    try:
        return db.set_active_model(body.model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------- sessions ----------------
@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    return db.list_sessions()


@app.post("/api/sessions")
async def create_session() -> dict:
    sid = db.create_session()
    return {"id": sid, "title": "새 대화", "created_at": "", "message_count": 0}


@app.get("/api/sessions/{session_id}/messages")
async def list_messages(session_id: str):
    if db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return db.list_messages(session_id)


# ---------------- chat (SSE relay) ----------------
class ChatIn(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1)


@app.post("/api/chat")
async def chat(body: ChatIn):
    settings = db.get_active_model()
    sid = body.session_id or uuid.uuid4().hex
    if db.get_session(sid) is None:
        db.create_session(sid, body.message.strip()[:40] or "새 대화")
    db.add_message(sid, "user", body.message)

    payload = {
        "message": body.message,
        "thread_id": sid,
        "llm": {
            "base_url": settings["base_url"],
            "api_key": settings["api_key"],
            "model": settings["model"],
            "temperature": settings["temperature"],
            "max_tokens": settings["max_tokens"],
            "system_prompt": settings["system_prompt"],
        },
        "stream_modes": ["messages", "updates"],
    }

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{AI_SERVICE_URL}/chat/stream", json=payload) as resp:
                    if resp.status_code != 200:
                        text = (await resp.aread()).decode(errors="replace")
                        yield sse("error", {"message": f"ai-service {resp.status_code}: {text[:300]}"})
                        return
                    event = ""
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if line.startswith("event:"):
                            event = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data = line[len("data:"):].strip()
                            try:
                                parsed = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if event == "meta":
                                yield sse("meta", parsed)
                            elif event == "messages":
                                yield sse("messages", parsed)
                            elif event == "updates":
                                yield sse("updates", parsed)
                            elif event == "done":
                                db.add_message(sid, "assistant", parsed.get("content", ""))
                                yield sse("done", parsed)
                            elif event == "error":
                                yield sse("error", parsed)
        except httpx.HTTPError as exc:
            log.exception("ai-service call failed")
            yield sse("error", {"message": f"ai-service 연결 실패: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )