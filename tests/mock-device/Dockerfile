# BedLight AI — mock-device (compose profile: test). Replays firmware 0.3 protocol with source="mock".
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir paho-mqtt==2.1.0 httpx==0.28.1 && useradd --create-home --uid 10001 app
WORKDIR /app
COPY mock_device.py placeholder.py /app/
USER app

LABEL org.opencontainers.image.title="bedlight-mock-device" bedlight.status="implemented-scripted"
ENTRYPOINT ["python", "/app/mock_device.py"]
CMD ["--scenario", "B"]
