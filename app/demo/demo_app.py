"""
LangChain Observability 데모 앱
================================
실제 LangChain 없이 "LangChain 파이프라인처럼 보이는" 스팬/메트릭/로그를
OpenTelemetry SDK로 생성하여 OTLP로 Alloy에 전송합니다.

트레이스 구조 (매 반복):
    langchain.run (root)
      ├── tool.retriever.search   (검색 단계)
      └── llm.openai.generate     (LLM 생성 단계, gen_ai.* 속성 포함)

메트릭:
    llm.tokens                    (counter, token_type=input/output)
    llm.operation.duration        (histogram, ms 단위)

로그:
    Python logging → OTLP 로그 (trace_id/span_id 자동 첨부)

환경변수:
    OTEL_EXPORTER_OTLP_ENDPOINT   OTLP 수신 주소 (기본: http://localhost:4317)
    OTEL_SERVICE_NAME             서비스 이름 (기본: demo-langchain)
    DEMO_INTERVAL_SECONDS         루프 간격 (기본: 3초)
"""
import logging
import os
import random
import socket
import time
import uuid

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
try:  # SDK 1.30+ 에서 logs exporter 경로 변경
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
except ImportError:
    from opentelemetry.exporter.otlp.proto.grpc.logs_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggingHandler, LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "demo-langchain")
INTERVAL = float(os.environ.get("DEMO_INTERVAL_SECONDS", "3"))

resource = Resource.create(
    {
        "service.name": SERVICE_NAME,
        "service.instance.id": f"{SERVICE_NAME}-{uuid.uuid4().hex[:8]}",
        "service.version": "0.1.0",
        "deployment.environment": "dev",
    }
)

# ---------------- Trace ----------------
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT))
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("demo.app", "0.1.0")

# ---------------- Metrics ----------------
metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint=OTLP_ENDPOINT), export_interval_millis=5000
)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("demo.app", "0.1.0")

token_counter = meter.create_counter(
    "llm.tokens", unit="1", description="LLM 사용 토큰 수 (input/output)"
)
duration_histogram = meter.create_histogram(
    "llm.operation.duration", unit="ms", description="LLM 단일 호출 소요 시간"
)

# ---------------- Logs ----------------
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT))
)
logging.getLogger().addHandler(
    LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
)
log = logging.getLogger("demo.app")
log.setLevel(logging.DEBUG)

MODELS = ["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"]
QUERIES = [
    "RAG 파이프라인이란?",
    "LangChain Agent란?",
    "벡터 DB 선택 기준",
    "프롬프트 최적화 방법",
]


def run_chain(run_id: int) -> None:
    model = random.choice(MODELS)
    query = random.choice(QUERIES)
    input_tokens = random.randint(120, 500)
    output_tokens = random.randint(60, 400)
    error = run_id % 11 == 0  # 대략 1/11 확률로 오류 재현

    with tracer.start_as_current_span("langchain.run") as root:
        root.set_attribute("gen_ai.operation.name", "run")
        root.set_attribute("user.query", query)
        root.set_attribute("run.id", run_id)

        # 1) 검색 (retriever)
        with tracer.start_as_current_span(
            "tool.retriever.search", kind=SpanKind.CLIENT
        ) as s:
            s.set_attribute("db.system", "chroma")
            s.set_attribute("db.collection.name", "langchain_docs")
            s.set_attribute("gen_ai.operation.name", "retrieve")
            log.info("searching chroma collection langchain_docs for %r (top_k=4)", query)
            time.sleep(random.uniform(0.01, 0.08))

        # 2) LLM 생성
        started = time.time()
        with tracer.start_as_current_span(
            "llm.openai.generate", kind=SpanKind.CLIENT
        ) as s:
            s.set_attribute("gen_ai.operation.name", "generate")
            s.set_attribute("gen_ai.provider.name", "openai")
            s.set_attribute("gen_ai.system", "openai")
            s.set_attribute("gen_ai.request.model", model)
            s.set_attribute("gen_ai.request.temperature", 0.7)
            s.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            s.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            if error:
                s.set_attribute("error.type", "RateLimitError")
                s.set_status(Status(StatusCode.ERROR, "rate limit exceeded: 429"))
                log.error("LLM call failed: RateLimitError 429 (model=%s)", model)
            else:
                log.info(
                    "LLM generate model=%s input_tokens=%d output_tokens=%d",
                    model, input_tokens, output_tokens,
                )
            time.sleep(random.uniform(0.08, 0.6))

        duration_ms = (time.time() - started) * 1000

        if error:
            root.set_status(Status(StatusCode.ERROR, "LLM generation failed"))
        else:
            duration_histogram.record(
                duration_ms,
                attributes={
                    "gen_ai.operation.name": "generate",
                    "gen_ai.request.model": model,
                    "gen_ai.provider.name": "openai",
                },
            )
            token_counter.add(
                input_tokens,
                attributes={"gen_ai.request.model": model, "token_type": "input"},
            )
            token_counter.add(
                output_tokens,
                attributes={"gen_ai.request.model": model, "token_type": "output"},
            )
            log.info("run completed model=%s duration_ms=%.1f", model, duration_ms)


def _wait_for_otlp(timeout: float = 90.0) -> None:
    """Alloy(OTLP 수신점)가 뜰 때까지 TCP 연결로 대기 (healthcheck 대체)."""
    from urllib.parse import urlparse

    parsed = urlparse(OTLP_ENDPOINT)
    host, port = parsed.hostname, parsed.port or 4317
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"[demo] OTLP endpoint ready: {OTLP_ENDPOINT}", flush=True)
                return
        except OSError:
            time.sleep(2)
    raise TimeoutError(f"OTLP endpoint not reachable after {timeout}s: {OTLP_ENDPOINT}")


def main() -> None:
    _wait_for_otlp()
    log.info("demo app started: endpoint=%s service=%s", OTLP_ENDPOINT, SERVICE_NAME)
    run_id = 0
    try:
        while True:
            run_id += 1
            run_chain(run_id)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        log.info("shutting down...")
    finally:
        tracer_provider.shutdown()
        meter_provider.shutdown()
        logger_provider.shutdown()


if __name__ == "__main__":
    main()