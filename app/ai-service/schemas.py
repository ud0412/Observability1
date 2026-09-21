"""ai-service 요청/응답 스키마."""
from pydantic import BaseModel, Field

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class ModelConfig(BaseModel):
    """frontend에서 넘어오는 모델 설정 (SQLite에 저장되어 변경 가능)."""

    base_url: str = "http://mock-llm:8100/v1"
    api_key: str = "mock-key"
    model: str = "mock-gpt-4o"
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    timeout: float = 120.0


class ChatRequest(BaseModel):
    """POST /chat/stream 요청 본문."""

    message: str = Field(..., min_length=1)
    thread_id: str = ""  # 비면 새 스레드를 만든다 (checkpoint 키)
    stream_modes: list[str] = Field(default_factory=lambda: ["messages", "updates"])
    llm: ModelConfig = Field(default_factory=ModelConfig)