FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# 단일 worker 필수 (SQLite checkpoint/store 단일 연결 공유)
# opentelemetry-instrument: env(OTEL_*)로 프로바이더 구성 + fastapi/langchain 자동 계측.
#   주의: opentelemetry-distro(configurator) 없이는 프로바이더가 구성되지 않아 스팬이 안 나감.
CMD ["opentelemetry-instrument", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]