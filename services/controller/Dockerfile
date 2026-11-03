# BedLight AI — controller
# 현재: PLACEHOLDER 이미지. FastAPI/MQTT/WebSocket 로직은 아직 구현되지 않았다.
# 향후: requirements.txt 설치 + `uvicorn app.main:app --host 0.0.0.0 --port 8000` 로 교체.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY placeholder.py /app/placeholder.py

USER app

LABEL org.opencontainers.image.title="bedlight-controller" \
      org.opencontainers.image.description="PLACEHOLDER - not functional" \
      bedlight.status="placeholder"

CMD ["python", "/app/placeholder.py"]
