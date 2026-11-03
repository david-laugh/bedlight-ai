# BedLight AI — mock-device (compose profile: test)
# 현재: PLACEHOLDER. 이벤트를 생성하지 않고 안내 메시지 출력 후 종료한다.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY placeholder.py /app/placeholder.py

USER app

LABEL org.opencontainers.image.title="bedlight-mock-device" \
      org.opencontainers.image.description="PLACEHOLDER - does not publish events" \
      bedlight.status="placeholder"

CMD ["python", "/app/placeholder.py"]
