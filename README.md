# LangChain Observability Stack (공부용)

WSL2 환경에서 **LangChain AI 서비스의 실행을 관찰**하기 위한 셀프호스팅 observability 인프라 + **실제 LangChain 채팅 앱**입니다.

- 인프라: 모든 텔레메트리는 **Grafana Alloy**를 단일 진입점으로 수집되어 **Tempo**(트레이스), **Prometheus**(메트릭), **Loki**(로그)에 저장되고 **Grafana**에서 통합 분석합니다.
- 앱: **frontend(채팅 UI)** → **ai-service(LangChain agent)** → **mock-llm(가상 OpenAI 호환 LLM)** 3계층. 나중에 frontend 설정에서 base_url/api_key만 바꾸면 실제 OpenAI/vLLM으로 바로 전환할 수 있습니다.

## 아키텍처

```
 ┌──────────┐   :8080    ┌────────────┐   :8000   ┌──────────────┐   :8100  ┌──────────┐
 │ frontend │ ─────────► │ ai-service │ ────────► │  mock-llm    │ ──────► │ (실제 LLM │
 │ (채팅 UI)│   fetch/   │ (LangChain │  ChatOpenAI│(OpenAI 호환  │  이후    │  OpenAI/  │
 │ SQLite DB│   SSE      │  create_agent)│ ─────►  │  가상 서버)   │  교체    │  vLLM)    │
 └────┬─────┘            └─────┬──────┘           └──────────────┘          └──────────┘
     │ SQLite환경설정/대화       │ OTel SDK (트레이스/로그) + OTLP gRPC:4317
     └──────────────────────────▼──────────────────────────────────────────────┐
                         ┌────────────────────────────────────────────┐        │
                         │              Grafana Alloy                 │        │
                         │           (통합 OTLP 수집기)                │◄───────┘
                         │  traces   ──► Tempo   (+spanmetrics R.E.D) │
                         │  metrics  ──► Prometheus (remote_write)    │
                         │  logs     ──► Loki      (loki.write)       │
                         └──────┬──────────┬──────────┬───────────────┘
                                └─────  Grafana  ─────┘  ← 대시보드 / Explore
```

- **frontend → ai-service**: 브라우저는 frontend만 호출합니다. `POST /api/chat`이 ai-service의 `POST /chat/stream`(SSE)을 릴레이합니다. (CORS 불필요)
- **ai-service(관찰 대상)**: `langchain.agents.create_agent`를 기본 설정 그대로 사용 (tools=[], middleware 없음). OpenAI 호환 `ChatOpenAI`만 지원. 채팅 히스토리/메모리 store는 **SQLite**.
- **ai-service → Alloy**: **커스텀 스팬 없이** `LangchainInstrumentor`(OpenLLMetry) 자동 계측 트레이스 + Python 로그를 OTel로 전송. 메트릭은 Alloy가 트레이스를 보고 파생 생성하는 spanmetrics(`langchain_calls_total` 등).
- **Alloy → 백엔드**: Traces→Tempo, metrics→Prometheus(remote write), logs→Loki(로그에 `traceid`/`spanid` 포함 → 트레이스 상관분석 가능).

## 구성 요소

| 컴포넌트 | 이미지/스택 | 역할 | 포트 |
|---|---|---|---|
| Grafana Alloy | `grafana/alloy:v1.19.2` | 통합 OTLP 수집기 | 4317(gRPC), 4318(HTTP), 12345(UI) |
| Tempo | `grafana/tempo:3.0.3` | 트레이스 저장 | 3200 |
| Prometheus | `prom/prometheus:v3.13.3` | 메트릭 저장 (remote-write 수신) | 9090 |
| Loki | `grafana/loki:3.7.8` | 로그 저장 | 3100 |
| Grafana | `grafana/grafana:13.2.2` | 시각화 (자동 프로비저닝) | 3000 |
| frontend | FastAPI + HTML/JS (SQLite) | 채팅 UI + 모델 설정 + SSE 릴레이 | 8080 |
| ai-service | FastAPI + LangChain/LangGraph (SQLite) | `create_agent` 스트리밍 API | 8000 |
| mock-llm | FastAPI | OpenAI 호환 가상 LLM (테스트용) | 8100 |

## 시작하기

요구사항: Docker + Docker Compose (WSL2)

```bash
# 1. 인프라 시작 (Alloy + Tempo + Prometheus + Loki + Grafana)
make up

# 2. 앱 3종 빌드 + 실행 (mock-llm + ai-service + frontend)
make app-up

# 3. 채팅 열기 → http://localhost:8080  (모델 설정 버튼에서 base_url/model 변경 가능)
#    Grafana → http://localhost:3000   (admin/admin, 익명 조회 가능)
```

> docker compose v5.5.1은 `--profile` 플래그가 없어 `COMPOSE_PROFILES=apps` env 방식으로 앱을 실행합니다(Makefile 참고).

## 채팅/스트리밍 동작

1. 브라우저가 `POST /api/chat` 호출 → frontend가 SQLite에서 모델 설정을 꺼내 ai-service에 전달
2. ai-service가 `ChatOpenAI(base_url, api_key, model, temperature, ...)` 생성 → `create_agent(model, tools=[], system_prompt, checkpointer=SQLite, store=SQLite)`
3. `agent.astream(..., stream_mode=["messages", "updates"])` — **messages(토큰 청크)** 와 **updates(노드 상태)** 둘 다 SSE로 전달
4. frontend가 SSE를 브라우저로 재전송 → 답변이 실시간으로 표시되고, 완료 시 SQLite에 저장

SSE 이벤트 (ai-service → frontend → 브라우저):
`meta` → `messages`(Chunk 내용) → `updates`(node별 상태) → … → `done`(전체 답변) / `error`

대화 세션마다 `thread_id`(=세션 id)가 SQLite checkpoint 키가 되어 **이전 대화 맥락을 기억**합니다.

## 모델 설정 변경

frontend 좌측 하단 **⚙ 모델 설정**에서 변경 (SQLite에 저장):

| 항목 | 기본값 | 설명 |
|---|---|---|
| Base URL | `http://mock-llm:8100/v1` | OpenAI 호환 엔드포인트 |
| API Key | `mock-key` | mock 서버는 아무 값 허용 |
| Model | `mock-gpt-4o` | mock-llm의 `/v1/models` 목록 |
| Temperature | `0.7` | |
| Max Tokens | (비움=기본) | |
| System Prompt | `You are a helpful assistant.` | |

**실제 OpenAI/vLLM 전환**: Base URL을 `https://api.openai.com/v1`(또는 vLLM 서버), API Key/Model을 실제 값으로 바꾸면 됩니다. mock-llm 없이도 동작합니다.

## mock-llm (가상 OpenAI 호환 서버)

- `POST /v1/chat/completions` — stream=True/False 모두 지원 (OpenAI 스펙: `choices[].delta.content` + `data: [DONE]`)
- `GET /v1/models`, `GET /health`
- 응답은 사용자 메시지를 반영한 결정적(deterministic) 텍스트를 단어 단위로 스트리밍
- 환경변수: `MOCK_LLM_STREAM_DELAY`(토큰당 지연 초), `MOCK_LLM_MODELS`, `MOCK_LLM_RESPONSE`(응답 직접 지정)

## 데이터 유입 확인

```bash
make metrics      # Prometheus의 langchain spanmetrics 조회
make traces       # Tempo의 ai-service 트레이스 조회
```

직접 확인:
```bash
curl -s 'http://localhost:9090/api/v1/query?query=langchain_calls_total'
curl -s 'http://localhost:3200/api/search?tags=service.name%3Dai-service'
curl -s 'http://localhost:3100/loki/api/v1/query_range?query=%7Bservice_name%3D%22ai-service%22%7D&limit=3'
# ai-service SSE 직접 테스트
curl -N -X POST http://localhost:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"안녕","llm":{"base_url":"http://mock-llm:8100/v1","api_key":"mock-key","model":"mock-gpt-4o"}}'
```

## Grafana에서 보는 법

1. **대시보드** — Dashboards → **LangChain Observability** (요청 RPS/오류율/지연, 최근 트레이스 등)
2. **Explore**
   - Tempo: `{}` 검색 → 트레이스 선택 → LangChain 자동 계측 스팬들(agent → ChatOpenAI 호출) 확인
   - Loki: `{service_name="ai-service"}` 로그 질의 → 로그의 traceid로 트레이스 점프
3. **Alloy UI** — http://localhost:12345 에서 컴포넌트 상태/파이프라인 확인

## 디렉터리 구조

```
├── docker-compose.yml          # 인프라 + 앱(프로필 "apps") 정의
├── Makefile                    # 편의 명령
├── config/
│   ├── alloy/config.alloy      # Alloy 데이터 흐름 (핵심)
│   ├── tempo/ loki/ prometheus/
│   └── grafana/                # provisioning + 대시보드 (auto-provision)
└── app/
    ├── mock-llm/               # OpenAI 호환 가상 LLM (FastAPI)
    ├── ai-service/             # LangChain agent + SSE + OTel(tracing.py)
    │   ├── main.py             #   POST /chat/stream (SSE)
    │   ├── agent.py            #   create_agent + SQLite checkpoint/store
    │   ├── tracing.py          #   OTel 구성 + LangchainInstrumentor
    │   └── schemas.py
    └── frontend/               # 채팅 UI + SQLite DB + SSE 릴레이
        ├── main.py             #   /api/{settings,sessions,chat} + 정적 UI
        ├── db.py               #   SQLite (설정/세션/메시지)
        └── static/             #   index.html / app.js / style.css
```

## 유용한 명령

```bash
make up          # 인프라 시작
make app-up      # 인프라 + 앱 3종 (빌드 포함)
make ps          # 상태 확인
make app-logs    # 앱 로그 팔로우
make logs        # 전체 컨테이너 로그 팔로우
make app-down    # 앱 중지 (ai-service/frontend SQLite 볼륨 보존)
make down        # 전체 종료 (볼륨 보존)
make clean       # 완전 삭제 (볼륨 포함)
```

## 참고 사항

- Alloy/Loki/Tempo 이미지는 셸 도구가 없는 distroless 기반이라 healthcheck를 넣지 않았습니다.
- ai-service SQLite 데이터(`checkpoints.sqlite`, `store.sqlite`)와 frontend DB(`frontend.db`)는 named volume(`ai-service-data`, `frontend-data`)에 저장됩니다. `make clean` 시 함께 삭제됩니다.
- LangChain 쪽 OTel은 `opentelemetry-instrumentation-langchain`(OpenLLMetry)의 `LangchainInstrumentor`를 사용합니다 (LangChain 자체에 내장 OTel은 없음). 자세한 주의사항은 `AGENTS.md` 참고.