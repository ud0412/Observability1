"""
mock-llm — OpenAI 호환 API를 흉내내는 가상 LLM 서버 (테스트용)

실제 OpenAI/vLLM을 연결하기 전까지 ai-service(ChatOpenAI)의 대상으로 사용됩니다.

지원:
  POST /v1/chat/completions   stream=True/False 모두 지원 (OpenAI 스펙 준수)
  GET  /v1/models             모델 목록
  GET  /health                헬스체크

환경변수:
  MOCK_LLM_STREAM_DELAY  "토큰"(단어)당 스트리밍 지연 초 (기본: 0.03)
  MOCK_LLM_MODELS        쉼표 구분 모델 목록 (기본: mock-gpt-4o,mock-gpt-4o-mini,mock-llama-3.1-8b)
  MOCK_LLM_RESPONSE      응답 텍스트 직접 지정 (기본: 내장 템플릿)
"""
import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mock-llm")

STREAM_DELAY = float(os.environ.get("MOCK_LLM_STREAM_DELAY", "0.03"))
MODELS = [
    m.strip()
    for m in os.environ.get("MOCK_LLM_MODELS", "mock-gpt-4o,mock-gpt-4o-mini,mock-llama-3.1-8b").split(",")
    if m.strip()
]

app = FastAPI(title="mock-llm")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = MODELS[0]
    messages: list[ChatMessage]
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = None
    stream: bool = False
    stop: str | list[str] | None = None
    stream_options: dict | None = None


def build_reply(user_text: str, model: str) -> str:
    """사용자 입력을 반영한 결정적(deterministic) 가짜 응답."""
    custom = os.environ.get("MOCK_LLM_RESPONSE")
    if custom:
        return custom
    snippet = user_text.strip()[:80] or "(빈 메시지)"
    return (
        "안녕하세요! 저는 mock-llm — OpenAI 호환 API로 동작하는 가상 LLM입니다. 🤖\n\n"
        f'방금 받은 메시지: "{snippet}"\n\n'
        "이 응답은 \"토큰\" 단위로 스트리밍되고 있습니다. 실제 흐름은:\n"
        "  브라우저 → frontend(/api/chat SSE 릴레이) → ai-service(ChatOpenAI)\n"
        "         → POST /v1/chat/completions (SSE) → 여기(mock-llm)\n\n"
        "이 요청 전체는 OTel 트레이스로 기록되어 Tempo에서 확인할 수 있습니다.\n"
        "지금까지 출력된 텍스트는 전부 이 서버가 만든 가짜 응답입니다.\n"
        "나중에 frontend 설정에서 base_url/api_key/model을 실제 값으로 바꾸면\n"
        "같은 파이프라인으로 실제 LLM 호출이 전달됩니다.\n\n"
        f"— mock-llm({model})"
    )


def tokenize(text: str) -> list[str]:
    """라인/단어 단위로 나눠 토큰 스트리밍을 흉내낸다."""
    tokens = []
    for line in text.splitlines() or [""]:
        if not line.strip():
            tokens.append("\n")
            continue
        for word in line.split(" "):
            tokens.append(word + " ")
    return tokens


def chunk_id() -> str:
    return f"chatcmpl-mock-{uuid.uuid4().hex[:16]}"


def make_chunk(model: str, content: str | None, finish_reason: str | None) -> dict:
    return {
        "id": chunk_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }


def make_completion(model: str, content: str, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "id": chunk_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    model = req.model if req.model in MODELS else MODELS[0]
    last_user = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    content = build_reply(last_user, model)
    if req.max_tokens:
        content = content[: req.max_tokens]

    tokens = tokenize(content)
    prompt_tokens = sum(max(1, len(m.content) // 4) for m in req.messages)
    completion_tokens = len(tokens)

    if not req.stream:
        log.info("non-stream model=%s prompt_tokens=%d completion_tokens=%d", model, prompt_tokens, completion_tokens)
        await asyncio.sleep(0.05)
        return make_completion(model, content, prompt_tokens, completion_tokens)

    async def event_stream():
        for tok in tokens:
            yield f"data: {json.dumps(make_chunk(model, tok, None), ensure_ascii=False)}\n\n"
            await asyncio.sleep(STREAM_DELAY)
        yield f"data: {json.dumps(make_chunk(model, None, 'stop'), ensure_ascii=False)}\n\n"
        if req.stream_options and req.stream_options.get("include_usage"):
            usage = {
                "id": chunk_id(),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
            yield f"data: {json.dumps(usage, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        log.info("stream done model=%s tokens=%d", model, len(tokens))

    log.info("stream start model=%s tokens=%d delay=%.3fs", model, len(tokens), STREAM_DELAY)
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/models")
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": 0, "owned_by": "mock-llm"} for m in MODELS],
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mock-llm", "models": MODELS}