"""
ai-service 로그 배선 (트레이스/메트릭 프로바이더는 여기서 만들지 않음).

프로바이더(트레이스/메트릭/로그)는 이 파일이 아니라 `opentelemetry-instrument`
CLI가 env(`OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
`OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_RESOURCE_ATTRIBUTES`, ...)로 자동 구성합니다.
CLI는 fastapi/langchain 인스트루먼터도 entry point로 자동 계측합니다.

CLI가 하지 않는 단 한 가지 — Python logging → OTLP(LoggingHandler) 부착만 여기서 처리합니다.
  - 앱/uvicorn.access 로그에 trace_id/span_id가 자동 첨부 → Loki에서 트레이스로 점프 가능
"""
import logging

from opentelemetry.sdk._logs import LoggingHandler


def attach_logging() -> None:
    """전역 LoggerProvider(CLI가 구성)에 LoggingHandler를 부착해 로그를 OTLP로 전송."""
    handler = LoggingHandler(level=logging.DEBUG)
    # 루트 로거: 앱 로그 전파 + INFO 레벨 허용
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    # uvicorn 접근 로그는 propagate=False 라 직접 핸들러를 붙여야 Loki에 잡힌다
    logging.getLogger("uvicorn.access").addHandler(handler)