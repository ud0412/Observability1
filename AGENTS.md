# AGENTS.md

Self-hosted observability stack for studying LangChain execution. A client app
emits OTLP to a single Grafana Alloy collector, which fans out to Tempo
(traces), Prometheus (metrics), and Loki (logs); Grafana visualizes all three.
Config lives in `config/`; `app/demo/` is the reference OTel client and the
template for the future real LangChain service. README.md (Korean) is the
user-facing guide; keep it in sync when behavior changes.

## Commands (Makefile)

- `make up` — start stack (Alloy, Tempo, Prometheus, Loki, Grafana). Does NOT start the demo (it is behind compose profile `demo`).
- `make ps` / `make logs` — status / follow all container logs.
- `make demo-up` — build + run demo app as a daemon (live data). `make demo` — one-shot foreground run. `make demo-logs` / `make demo-down` to manage it.
- `make metrics` / `make traces` — verify data actually landed in Prometheus / Tempo.
- `make down` keeps named volumes (data preserved); `make clean` = `docker compose down -v` (wipes all data).

## Hard-earned gotchas (do not "fix" these)

- **Docker Compose v5.5.1 has no `--profile` flag.** Use `COMPOSE_PROFILES=demo <cmd>` env instead (see Makefile). `docker compose --profile demo ...` fails with `unknown flag: --profile`.
- **Alloy v1.19 CLI**: config file is a *positional* arg to `alloy run` — `--config.file` does not exist (`unknown flag`). `otelcol.pipeline` was also removed; components are wired with per-component `output` blocks as in `config/alloy/config.alloy`.
- **Reload Alloy without restart**: `curl -X POST http://localhost:12345/-/reload` (returns 200). Changes to `config.alloy` are picked up this way.
- **Prometheus needs `--web.enable-remote-write-receiver`** (already in docker-compose.yml) or Alloy's remote writes are silently dropped. Do not remove it.
- **alloy/loki/tempo images are distroless** (no shell, no wget) → they intentionally have NO healthchecks; only Grafana has one. Never add `depends_on: {condition: service_healthy}` for them — the demo app self-retries OTLP connectivity (`_wait_for_otlp` in `app/demo/demo_app.py`).
- **Alloy spanmetrics connector**: default `metrics_flush_interval` is 60s; the config lowers it to 10s for live dashboards. `namespace = "langchain"` prefixes emitted metrics → `langchain_calls_total`, `langchain_duration_milliseconds_bucket/_count/_sum`. Default dimensions: `service_name`, `span_name`, `span_kind`, `status_code` (errors = `status_code="STATUS_CODE_ERROR"`), plus custom `gen_ai_operation_name`, `gen_ai_request_model`.
- **Grafana dashboard JSON hardcodes metric names and datasource uids** (`prometheus`, `loki`, `tempo`) in `config/grafana/dashboards/langchain-observability.json`. Renaming the spanmetrics `namespace`, a metric, or a datasource uid silently breaks panels. Datasources auto-provision from `config/grafana/provisioning/datasources/`; dashboard files hot-reload every ~30s, but a *new* dashboard file requires `docker compose restart grafana`.
- **Grafana creds**: admin/admin; anonymous viewer enabled (read-only). UI at http://localhost:3000 (WSL2 → Windows browser works). Alloy UI at :12345.

## App / OTel SDK notes

- `app/demo/requirements.txt` pins only `>=1.28`; OTel Python moves fast. As of SDK 1.44, logs live under `opentelemetry.sdk._logs` / `opentelemetry.exporter.otlp.proto.grpc._log_exporter` (leading underscore). `demo_app.py` imports both old/new paths in try/except — keep those fallbacks.
- Recent SDKs removed the `exemplars=` kwarg from `Histogram.record()`; exemplar support was deliberately left out of the demo. Don't re-add without verifying the current SDK API.
- When curling Prometheus with range queries (`[1m]`, `[5m]`), URL-encode with `curl -G --data-urlencode 'query=...'` — raw brackets break the API.

## Data flow (why things are where they are)

- App → `localhost:4317` (OTLP gRPC, from host) or `http://alloy:4317` (in-compose) → Alloy:
  - traces → Tempo, plus spanmetrics RED metrics,
  - metrics → Prometheus remote write,
  - logs → Loki (JSON payload embeds `traceid`/`spanid`, enabling trace↔log correlation in Grafana).
- Ports: Grafana 3000, Prometheus 9090, Loki 3100, Tempo 3200, Alloy 4317/4318 (OTLP) + 12345 (UI, /-/ready).
- Visit `metrics`/`traces` Makefile targets before debugging "no data" — they confirm ingestion end to end.
- Images are pinned to specific versions; Tempo 3.x and Alloy 1.19 both had breaking config changes. Before bumping versions, re-verify against the official docker-compose examples.