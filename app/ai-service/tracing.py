"""
OpenTelemetry 설정: 트레이스/메트릭/로그 → OTLP(Alloy), LangChain + FastAPI 자동 계측.

커스텀 스팬은 만들지 않습니다. 수집되는 것:
  - 트레이스 : LangChain 실행 스팬(LangchainInstrumentor) + FastAPI HTTP 스팬(FastAPIInstrumentor)
  - 메트릭  : Alloy spanmetrics가 트레이스에서 파생 생성 (langchain_calls_total 등)
  - 로그    : Python logging → OTLP (trace_id/span_id 자동 첨부)

Env (docker-compose의 ai-service.environment에 선언):
  OTEL_EXPORTER_OTLP_ENDPOINT  Alloy OTLP gRPC 주소 (기본 http://localhost:4317)
  OTEL_SERVICE_NAME            리소스 service.name (기본 ai-service)
  OTEL_RESOURCE_ATTRIBUTES     추가 리소스 속성 "k1=v1,k2=v2" — SDK 표준 규칙대로 병합
  TRACELOOP_TRACE_CONTENT      OpenLLMetry의 프롬프트/응답 콘텐츠 캡처 여부 (기본 true;
                               인스트루먼터가 직접 읽으므로 코드에서 참조하지 않음)
"""
import logging
import os
import uuid

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
try:  # SDK 1.30+ 에서 경로 변경
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
except ImportError:
    from opentelemetry.exporter.otlp.proto.grpc.logs_exporter import OTLPLogExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.sdk._logs import LoggingHandler, LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "ai-service")

_providers: dict[str, object] = {}


def _resource() -> Resource:
    attrs = {
        "service.name": SERVICE_NAME,
        "service.instance.id": f"{SERVICE_NAME}-{uuid.uuid4().hex[:8]}",
        "service.version": "0.1.0",
        "deployment.environment": "dev",
    }
    # OTEL_RESOURCE_ATTRIBUTES="k1=v1,k2=v2" 병합 (SDK 표준 env 규칙)
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            if key.strip() and value.strip():
                attrs[key.strip()] = value.strip()
    return Resource.create(attrs)


def setup_otel() -> None:
    """전역 Tracer/Meter/LoggerProvider 구성 + LangChain 자동 계측 활성화."""
    resource = _resource()

    # 트레이스
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT))
    )
    trace.set_tracer_provider(tracer_provider)

    # 메트릭 (SDK 레벨; 앱 메트릭 없음 → 스팬메트릭이 실제 메트릭 차지)
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTLP_ENDPOINT), export_interval_millis=5000
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # 로그 (Python logging → OTLP)
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT))
    )
    otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
    # 루트 로거: 앱 로그 전파 + INFO 레벨 허용
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(otel_handler)
    # uvicorn 접근 로그는 propagate=False 라 직접 핸들러를 붙여야 Loki에 잡힌다
    logging.getLogger("uvicorn.access").addHandler(otel_handler)

    # LangChain 실헹을 자동으로 트레이스화 (커스텀 스팬 없음)
    LangchainInstrumentor().instrument()

    _providers["trace"] = tracer_provider
    _providers["metrics"] = meter_provider
    _providers["logs"] = logger_provider

    log = logging.getLogger(SERVICE_NAME)
    log.setLevel(logging.DEBUG)
    log.info("OTel ready: endpoint=%s service=%s LangchainInstrumentor=on", OTLP_ENDPOINT, SERVICE_NAME)


def shutdown_otel() -> None:
    for provider in _providers.values():
        try:
            provider.shutdown()  # type: ignore[attr-defined]
        except Exception:
            pass