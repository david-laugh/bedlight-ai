# BedLight AI — db-writer
# 현재: PLACEHOLDER 이미지. MQTT 구독/스키마 검증/Supabase INSERT 로직은 아직 구현되지 않았다.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY placeholder.py /app/placeholder.py

USER app

LABEL org.opencontainers.image.title="bedlight-db-writer" \
      org.opencontainers.image.description="PLACEHOLDER - not functional" \
      bedlight.status="placeholder"

CMD ["python", "/app/placeholder.py"]
