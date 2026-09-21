# LangChain Observability Makefile
# make up        : 인프라(start) - Alloy + Tempo + Prometheus + Loki + Grafana
# make app-up    : 인프라 + 앱 3종 (mock-llm + ai-service + frontend)
# make app-logs  : 앱 로그 팔로우
# make app-down  : 앱 3종 중지/삭제 (ai-service SQLite 데이터 보존)
# make logs      : 전체 로그 팔로우
# make metrics   : Prometheus에 적재된 spanmetrics 메트릭 확인
# make traces    : Tempo에 저장된 ai-service 트레이스 확인
# make down      : 인프라 종료 (데이터 유지)
# make clean     : 인프라/앱 완전 삭제 + 볼륨 삭제

.PHONY: up down ps logs app-up app-down app-logs metrics traces clean

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

# 앱 3종 (mock-llm + ai-service + frontend) 빌드 후 실행.
# 주의: docker compose v5.5.1은 --profile 플래그 미지원 → COMPOSE_PROFILES env 사용
app-up:
	COMPOSE_PROFILES=apps docker compose up -d --build

app-logs:
	COMPOSE_PROFILES=apps docker compose logs -f --tail=100 mock-llm ai-service frontend

app-down:
	COMPOSE_PROFILES=apps docker compose rm -sf mock-llm ai-service frontend

# 데이터 유입 검증 (Alloy spanmetrics: namespace="langchain")
metrics:
	@echo "== langchain spanmetrics (calls_total) =="
	@curl -s 'http://localhost:9090/api/v1/query?query=langchain_calls_total' | python3 -m json.tool
	@echo "== langchain spanmetrics (duration, 1m rate) =="
	@curl -s 'http://localhost:9090/api/v1/query?query=rate(langchain_duration_milliseconds_count[1m])' | python3 -m json.tool

traces:
	@curl -s 'http://localhost:3200/api/search?tags=service.name%3Dai-service' | python3 -m json.tool

clean:
	docker compose down -v