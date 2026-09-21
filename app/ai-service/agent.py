"""
LangChain 에이전트 팩토리.

- langchain.agents.create_agent (구: langgraph.prebuilt.create_react_agent)
- tools=[] (도구 미사용), middleware 없음, system_prompt만 지정
- checkpoint/store 모두 SQLite:
    AsyncSqliteSaver / AsyncSqliteStore (from_conn_string, async context manager)
- 스트리밍: agent.astream(..., stream_mode=["messages", "updates"])
"""
import logging
import os

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from schemas import ModelConfig

log = logging.getLogger("ai-service")

CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB", "/data/checkpoints.sqlite")
STORE_DB = os.environ.get("STORE_DB", "/data/store.sqlite")


def build_model(cfg: ModelConfig) -> ChatOpenAI:
    """OpenAI 호환 엔드포인트(OpenAI 실제/vLLM/mock-llm)용 ChatOpenAI."""
    kwargs: dict = {
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "temperature": cfg.temperature,
        "streaming": True,  # astream 메시지 청크 수신용
        "timeout": cfg.timeout,
        "max_retries": 1,
    }
    if cfg.max_tokens:
        kwargs["max_tokens"] = cfg.max_tokens
    return ChatOpenAI(**kwargs)


def build_agent(model, system_prompt: str | None, checkpointer, store):
    """기본 create_agent 그대로 (도구/미들웨어 없음)."""
    kwargs: dict = {
        "model": model,
        "tools": [],  # 도구 미사용 — 새 create_agent는 tools가 필수 인자
        "checkpointer": checkpointer,
        "store": store,
    }
    if system_prompt:
        kwargs["system_prompt"] = system_prompt
    return create_agent(**kwargs)