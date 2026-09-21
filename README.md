# LangChain Observability Stack (공부용)

WSL2 환경에서 **LangChain AI 서비스의 실행을 관찰**하기 위한 셀프호스팅 observability 인프라입니다.
앱의 모든 텔레메트리(트레이스/메트릭/로그)는 **Grafana Alloy**를 단일 진입점으로 수집되어
**Tempo**(트레이스), **Prometheus**(메트릭), **Loki**(로그)에 저장되고, **Grafana**에서 통합 분석합니다.

## 아키텍처

```
                     ┌────────────────────────────────────────────┐
                     │              Grafana Alloy                 │
                     │           (통합 OTLP 수집기)                │
   LangChain 앱 ──►  │  OTLP gRPC:4317 / HTTP:4318               │
   (OTel SDK)  │    │                                             │
               └────┤  traces   ──► Tempo   (otelcol.exporter.otlp)│
                    │                + span ─► R.E.D 메트릭 자동 생성│
                    │  metrics  ──► Prometheus (remote_write)      │
                    │  logs     ──► Loki      (loki.write)         │
                    └──────┬──────────┬──────────┬─────────────────┘
                           │          │          │
                     ┌─────▼──┐  ┌────▼────┐ ┌───▼────┐
                     │ Tempo  │  │Prometheus│ │ Loki   │
                     └─────┬──┘  └────┬────┘ └───┬────┘
                           └─────  Grafana  ─────┘  ← 대시보드 / Explore
```

- **앱 → Alloy**: OpenTelemetry SDK가 OTLP로 전송 (`http://localhost:4317` gRPC, `http://localhost:4318` HTTP)
- **Alloy → Tempo**: 스팬 트레이스 저장 + **spanmetrics** 커넥터가 스팬에서 RED 메트릭(호출수/오류/지연)을 자동 생성
- **Alloy → Prometheus**: 앱 메트릭과 spanmetrics를 remote write
- **Alloy → Loki**: OTLP 로그 push (로그에 `traceid`/`spanid` 포함 → 트레이스 상관분석 가능)
- **Grafana**: Loki/Tempo/Prometheus Datasource 자동 등록 + 제공 대시보드

## 구성 요소 & 버전

| 컴포넌트 | 이미지 | 역할 | 호스트 포트 |
|---|---|---|---|
| Grafana Alloy | `grafana/alloy:v1.19.2` | 통합 수집기 | 4317(gRPC), 4318(HTTP), 12345(UI) |
| Tempo | `grafana/tempo:3.0.3` | 트레이스 저장 | 3200(API) |
| Prometheus | `prom/prometheus:v3.13.3` | 메트릭 저장 | 9090 |
| Loki | `grafana/loki:3.7.8` | 로그 저장 | 3100 |
| Grafana | `grafana/grafana:13.2.2` | 시각화 | 3000 |

## 시작하기

요구사항: Docker + Docker Compose (WSL2)

```bash
# 1. 인프라 시작 (Alloy + Tempo + Prometheus + Loki + Grafana)
make up            # 또는 docker compose up -d

# 2. 상태 확인
make ps

# 3. Grafana 접속  →  http://localhost:3000  (admin / admin, 익명 조회 가능)
#    좌측 메뉴: Dashboards → "LangChain Observability"
```

### 데모 앱으로 파이프라인 검증

docker-compose에 **프로필 `demo`**로 포함된 데모 앱이 실제 LangChain 서비스 대신
"LangChain형" 트레이스/메트릭/로그를 계속 생성합니다.

```bash
make demo-up      # 백그라운드로 데모 앱 실행 (실시간 데이터 생성)
make demo-logs    # 데모 앱 로그 확인
make demo-down    # 데모 앱 중지
# 1회성 실행(전경): make demo   (Ctrl+C 종료)
```

데모 앱이 생성하는 것:
- 트레이스: `langchain.run` → `tool.retriever.search` + `llm.openai.generate` (요청 11건당 1건 오류)
- 메트릭: `langchain_calls_total` / `langchain_duration_milliseconds`(spanmetrics), `llm_tokens_total`, `llm_operation_duration_milliseconds`
- 로그: 각 단계 로그 (OTLP, `traceid`/`spanid` 자동 포함)

### 데이터 유입 확인 (curl)

```bash
make metrics      # Prometheus에 적재된 langchain/llm 메트릭 조회
make traces       # Tempo에 저장된 최근 트레이스 조회
```

또는 직접:
```bash
curl 'http://localhost:9090/api/v1/query?query=langchain_calls_total'
curl 'http://localhost:3200/api/search?tags=service.name%3Ddemo-langchain'
curl 'http://localhost:3100/loki/api/v1/query_range?query=%7Bservice_name%3D%22demo-langchain%22%7D&limit=3'
```

## 디렉터리 구조

```
├── docker-compose.yml              # 전체 인프라 정의
├── Makefile                        # 편의 명령
├── config/
│   ├── alloy/config.alloy          # Alloy 데이터 흐름 정의 (여기가 핵심!)
│   ├── tempo/tempo.yaml
│   ├── loki/loki.yaml
│   ├── prometheus/prometheus.yml
│   └── grafana/
│       ├── provisioning/           # Datasource/대시보드 자동 등록
│       └── dashboards/langchain-observability.json
└── app/demo/                       # 파이프라인 검증용 데모 앱 (OTel SDK)
```

## Grafana에서 보는 법

1. **대시보드** — Dashboards → **LangChain Observability**
   - 요청 RPS / 오류율 / 지연(p50,p95) / 토큰 사용률 / 로그 볼륨 / 최근 트레이스 / 인프라 상태
2. **Explore**
   - Tempo 선택 후 `{}` 검색 → 트레이스 선택 → 각 스팬의 `gen_ai.*` 속성 확인
   - 트레이스 → 로그 점프: 한 스팬 선택 → "Logs" 탭 (traceid 기반 연동)
   - Loki: `{service_name="demo-langchain"}` 로그 질의
3. **Alloy UI** — http://localhost:12345 에서 컴포넌트 상태/파이프라인 확인

## 다음 단계: 실제 LangChain 서비스 연결

`app/demo/demo_app.py`가 바로 템플릿입니다. 실제 서비스는 두 가지만 하면 연결됩니다.

### 방법 1 — 자동 계측 (권장, OpenLLMetry)

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc \
            opentelemetry-instrumentation-langchain
```

```python
import os
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"  # Alloy

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

LangchainInstrumentor().instrument()   # 이후 LangChain 실행이 자동으로 트레이스화

# 이제 LangChain 앱을 그대로 실행하면 됨 (RAG/Agent/Chat 등)
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage

llm = ChatOpenAI()   # 등등
```

### 방법 2 — 수동 계측

`app/demo/demo_app.py`의 구조를 그대로 사용하면 됩니다.
`tracer.start_as_current_span("llm.openai.generate", kind=SpanKind.CLIENT)`, `gen_ai.*` 속성,
`llm_tokens_total` 카운터, `llm_operation_duration` 히스토그램 등이 이미 정의되어 있습니다.

> 참고: Grafana에서 **메트릭 → 트레이스 exemplar 점프**를 사용하려면 OpenTelemetry SDK에서
> exemplar가 지원되는 버전 조합이 필요합니다 (본 데모는 검증 편의상 exemplar 미사용).
> LangChain 자동 계측(`opentelemetry-instrumentation-langchain`)은 OpenAI 등 provider 호출을
> `gen_ai.*` 표준 속성으로 기록하므로 위 대시보드의 차원(`gen_ai_operation_name`,
> `gen_ai_request_model`)에 바로 잡힙니다.

## 유용한 명령

```bash
make up          # 인프라 시작
make logs        # 모든 컨테이너 로그 팔로우
make ps          # 상태 확인
make down        # 종료 (데이터 저장됨, 볼륨 유지)
make clean       # 완전 삭제 (볼륨 포함)
```

## 참고 사항

- Alloy/Loki/Tempo 이미지는 셸 도구가 없는 distroless 기반이라 healthcheck를 넣지 않았고,
  데모 앱이 내부적으로 OTLP 연결을 재시도합니다.
- Loki/Tempo는 로컬 파일시스템 저장이므로 인프라 삭제 시 데이터가 사라집니다 (볼륨 유지 시 보존).
- WSL2에서 Windows 브라우저로 `localhost:3000` 접속이 가능합니다.