"""
ai-service 로그 배선 — stdout(traceID/spanID 주입) 전용.

프로바이더(트레이스/메트릭/로그)와 fastapi/langchain 자동 계측은 `opentelemetry-instrument`
CLI가 env(`OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
`OTEL_EXPORTER_OTLP_PROTOCOL`, ...)로 자동 구성합니다.

로그는 OTLP(LoggingHandler)를 쓰지 않습니다. 원칙: **stdout으로 출력되는 것
(= docker logs 내용)이 곧 Loki 내용**입니다. 수집은 인프라가 담당합니다.
  - Docker Compose: Alloy `loki.source.docker`가 docker logs와 동일한 stdout을 Loki로 push
  - Kubernetes: 동일 개념으로 Alloy `loki.source.kubernetes`(→ kubectl logs 내용) 사용

이 파일이 하는 일은 단 하나: stdout 로그 라인에 현재 활성 스팬의
traceID/spanID를 주입해 "docker logs/Loki의 로그 → Tempo 트레이스" 상관관계를 만들어 주는 것입니다.
"""
import logging

from opentelemetry import trace

# uvicorn 기본 포맷과 비슷하게, 색상 없이 + traceID/spanID가 뒤에 붙는 형태
_TRACE_FORMAT = "%(levelname)s:     %(message)s"


def _current_trace_context_ids() -> tuple[str, str]:
    """현재 활성 스팬(요청 중)이면 trace_id/span_id 반환, 아니면 빈 문자열."""
    span = trace.get_current_span()
    if not span.is_recording():
        return "", ""
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


class _TraceContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        trace_id, span_id = _current_trace_context_ids()
        if not trace_id:
            return base
        return f"{base} | traceID={trace_id} | spanID={span_id}"


def enrich_console_logging() -> None:
    """
    stdout 로그 포맷을 traceID/spanID 주입 형태로 교체.

    ⚠️ uvicorn이 로깅을 구성한 뒤 호출해야 하므로 라이프사이클 startup에서 부른다
    (uvicorn flow: config.load() → ... → lifespan.startup).

    - 루트/uvicorn/uvicorn.error/uvicorn.access 의 StreamHandler formatter를 교체
    - 색상(ANSI) 제거 → docker logs 텍스트 == Loki 수집 텍스트가 정확히 일치
    - 요청 처리 중의 로그(앱 로그, uvicorn.access, httpx 등)는 활성 스팬의
      traceID/spanID가 붙어 Loki 로그 ↔ Tempo 트레이스 상관이 유지된다
    """
    fmt = _TraceContextFormatter(_TRACE_FORMAT)
    loggers = (
        logging.getLogger(),               # 루트 — 앱 로그/httpx 등이 전파됨
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    )
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setFormatter(fmt)