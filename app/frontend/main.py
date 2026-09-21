"""
frontend — 채팅 UI + SQLite DB + ai-service SSE 릴레이

- 브라우저 → POST /api/chat → ai-service /chat/stream(SSE) → 브라우저(SSE 재전송)
  브라우저가 ai-service를 직접 호출하지 않으므로 CORS가 필요 없습니다.
- 모델 설정 / 세션 / 메시지는 전부 SQLite에 저장됩니다.
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
from pydantic import BaseModel

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


# ---------------- settings ----------------
class SettingsIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt: str = ""


@app.get("/api/settings")
async def get_settings() -> dict:
    return db.get_settings()


@app.put("/api/settings")
async def put_settings(body: SettingsIn) -> dict:
    db.update_settings(body.model_dump())
    log.info("settings updated: model=%s base_url=%s", body.model, body.base_url)
    return {"ok": True, "settings": db.get_settings()}


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
    message: str


@app.post("/api/chat")
async def chat(body: ChatIn):
    settings = db.get_settings()
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