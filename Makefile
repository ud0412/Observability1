# LangChain Observability Makefile
# make up        : 인프라(start) - Alloy + Tempo + Prometheus + Loki + Grafana
# make logs      : 전체 로그 팔로우
# make demo      : 데모 앱 1회 실행 (파이프라인 검증, 로그를 화면에 표시)
# make demo-up   : 데모 앱을 백그라운드에서 계속 실행 (Grafana에서 실시간 관찰)
# make demo-down : 데모 앱 중지
# make metrics   : Prometheus에 적재된 주요 메트릭 확인
# make traces    : Tempo에 저장된 최근 트레이스 확인
# make down      : 인프라 종료 (데이터 유지)
# make clean     : 인프라/데모 완전 삭제 + 볼륨 삭제

.PHONY: up down ps logs demo demo-up demo-down metrics traces clean

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

# 데모 앱 1회 실행 (전경, 로그 실시간 출력. Ctrl+C로 종료)
demo:
	COMPOSE_PROFILES=demo docker compose run --rm demo-app

# 데모 앱을 백그라운드 데몬으로 계속 실행 (실시간 데이터 생성)
demo-up:
	COMPOSE_PROFILES=demo docker compose up -d --build demo-app

demo-logs:
	COMPOSE_PROFILES=demo docker compose logs -f --tail=100 demo-app

# 데모 앱 중지 및 삭제
demo-down:
	COMPOSE_PROFILES=demo docker compose rm -sf demo-app

# 데이터 유입 검증
metrics:
	@echo "== langchain spanmetrics (calls_total) =="
	@curl -s 'http://localhost:9090/api/v1/query?query=langchain_calls_total' | python3 -m json.tool
	@echo "== demo app metrics =="
	@curl -s 'http://localhost:9090/api/v1/query?query=llm_tokens_total' | python3 -m json.tool

traces:
	@curl -s 'http://localhost:3200/api/search?tags=service.name%3Ddemo-langchain' | python3 -m json.tool

clean:
	docker compose down -v