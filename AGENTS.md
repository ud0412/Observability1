# AGENTS.md

Self-hosted observability stack for studying LangChain execution, plus a real
reference app to observe:

- **Infra**: a client emits OTLP to a single Grafana Alloy collector, which fans
  out to Tempo (traces), Prometheus (metrics), and Loki (logs); Grafana
  visualizes all three. Config lives in `config/`.
- **Apps** (`app/{mock-llm,ai-service,frontend}`, compose profile `apps`):
  chat UI → LangChain agent API (SSE streaming) → fake OpenAI-compatible LLM.
  The ai-service is the reference OTel client to copy when wiring a future real
  LangChain service.
- README.md (Korean) is the user-facing guide; keep it in sync when behavior changes.

## Commands (Makefile)

- `make up` — start stack (Alloy, Tempo, Prometheus, Loki, Grafana). Does NOT start apps (profile `apps`).
- `make ps` / `make logs` — status / follow all container logs.
- `make app-up` — build + run the 3 apps as daemons (mock-llm :8100, ai-service :8000, frontend :8080).
- `make app-logs` / `make app-down` — app logs / stop+remove apps (SQLite volumes preserved).
- `make metrics` / `make traces` — verify data landed in Prometheus / Tempo (`service.name=ai-service`).
- `make down` keeps named volumes (data preserved); `make clean` = `docker compose down -v` (wipes all data, incl. app SQLite vols `ai-service-data`, `frontend-data`).

## Hard-earned gotchas (do not "fix" these)

- **Docker Compose v5.5.1 has no `--profile` flag.** Use `COMPOSE_PROFILES=apps <cmd>` env instead (see Makefile). `docker compose --profile ...` fails with `unknown flag: --profile`.
- **Alloy v1.19 CLI**: config file is a *positional* arg to `alloy run` — `--config.file` does not exist. `otelcol.pipeline` was also removed; components are wired with per-component `output` blocks as in `config/alloy/config.alloy`.
- **Reload Alloy without restart**: `curl -X POST http://localhost:12345/-/reload` (returns 200).
- **Prometheus needs `--web.enable-remote-write-receiver`** (already in docker-compose.yml) or Alloy's remote writes are silently dropped.
- **alloy/loki/tempo images are distroless** (no shell/wget) → NO healthchecks; never add `depends_on: {condition: service_healthy}` for them. No app waits for OTLP at startup — ai-service only calls Alloy per-request.
- **Alloy spanmetrics connector**: `metrics_flush_interval` lowered to 10s; `namespace = "langchain"` → `langchain_calls_total`, `langchain_duration_milliseconds_*`. Default dimensions incl. `service_name`, `status_code` (`STATUS_CODE_ERROR` = errors), custom `gen_ai_operation_name`, `gen_ai_request_model` (set by OpenLLMetry instrumentation).
- **Grafana dashboard JSON hardcodes metric names and datasource uids** (`prometheus`, `loki`, `tempo`) in `config/grafana/dashboards/langchain-observability.json`. Renaming the spanmetrics namespace/metric or a datasource uid silently breaks panels. Datasources auto-provision; dashboards hot-reload ~30s but a *new* dashboard file needs `docker compose restart grafana`.
- **Grafana creds**: admin/admin; anonymous viewer enabled. UI at http://localhost:3000. Alloy UI at :12345.

## App / LangChain SDK notes (verified 2026-09: langchain 1.4.2, langgraph 1.2.11, OTel SDK 1.44)

- **`create_agent` moved to `langchain.agents`** (`from langchain.agents import create_agent`). `langgraph.prebuilt.create_react_agent` still exists but is deprecated (removal in V2.0). The new signature requires **`tools` positionally — pass `tools=[]`** for no-tools agents. `system_prompt` is still the kwarg name.
- **LangChain has no built-in OpenTelemetry tracer.** Native tracing = `opentelemetry-instrumentation-langchain` (Traceloop OpenLLMetry 0.62.x): `LangchainInstrumentor().instrument()`. Call it before any LangChain code runs — `app/ai-service/main.py` imports `tracing` and calls `setup_otel()` as its first statements (import order matters).
- **SQLite checkpoint/store are packaged inside `langgraph`** (no `langgraph-store` PyPI package; `langgraph-checkpoint-sqlite` is separate). Use async context managers: `AsyncSqliteSaver.from_conn_string("/data/checkpoints.sqlite")` and `AsyncSqliteStore.from_conn_string(...)` — not constructors. **Pass a plain filesystem path, NOT `sqlite:///...`**: aiosqlite hands the string straight to `sqlite3.connect()` (no `uri=True`), so `sqlite:///...` fails with "unable to open database file".
- **ai-service must run with a single uvicorn worker** (one shared SQLite connection; in-memory saver/store state). `--workers 1` is in its Dockerfile.
- **Streaming**: `agent.astream(inputs, config, stream_mode=["messages","updates"])` yields `("messages", (AIMessageChunk, meta))` and `("updates", {node: {"messages": [...]}})`. Chunk text comes from the model node. No OpenAI-specific SPOT dependency — just `streaming=True` on ChatOpenAI.
- **ChatOpenAI base_url must include `/v1`** (`http://mock-llm:8100/v1`). It's a plain OpenAI-compatible HTTP client → also works against real OpenAI/vLLM unchanged.
- **frontend relays SSE** (`POST /api/chat` → ai-service `/chat/stream` → browser). The browser never calls ai-service directly, so CORS isn't actually exercised (ai-service allows `*` anyway). SSE event names: `meta`, `messages`, `updates`, `error`, `done`.
- **OTel logs**: as of SDK 1.44, logs live under `opentelemetry.sdk._logs` / `opentelemetry.exporter.otlp.proto.grpc._log_exporter` (leading underscore). `ai-service/tracing.py` keeps old/new import fallbacks like the old demo did — keep them.
- **mock-llm** is OpenAI-spec compliant for `POST /v1/chat/completions` (stream + non-stream, `data: [DONE]`), plus `/v1/models`, `/health`. `MOCK_LLM_STREAM_DELAY` controls per-token pacing.

## Data flow (why things are where they are)

- Browser → frontend :8080 (`POST /api/chat`, SSE relay) → ai-service :8000 (`/chat/stream`) → ChatOpenAI → mock-llm :8100 (`/v1/chat/completions`). Real model = same path; store multiple model configs in frontend (`models` table) and pick the active one (`settings.active_model`).
- ai-service → Alloy OTLP gRPC :4317 (traces via LangchainInstrumentor + logs via LoggingHandler); metrics are derived by Alloy's spanmetrics connector, not emitted by the app.
- Ports: Grafana 3000, Prometheus 9090, Loki 3100, Tempo 3200, Alloy 4317/4318/12345, ai-service 8000, frontend 8080, mock-llm 8100.
- SQLite: ai-service checkpoints/store in volume `ai-service-data` (`/data/checkpoints.sqlite`, `/data/store.sqlite`); frontend models/settings/sessions/messages in `frontend-data` (`/data/frontend.db`).
- Visit `metrics`/`traces` Makefile targets before debugging "no data" — they confirm ingestion end to end.
- Images are pinned; Tempo 3.x and Alloy 1.19 both had breaking config changes. Before bumping versions, re-verify against the official docker-compose examples.