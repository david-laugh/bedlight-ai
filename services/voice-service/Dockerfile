# BedLight AI — voice-service (FastAPI)
# Implemented: WAV upload / validation / dataset storage. KWS inference: not yet (see docs/voice-inference-plan.md).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/app/

# Default non-root user. compose overrides it with VOICE_UID/VOICE_GID so files written to the
# bind-mounted dataset directory are owned by the host user on Linux.
RUN useradd --create-home --uid 10001 app && mkdir -p /data/voice /models && chown -R app /data/voice
USER app

LABEL org.opencontainers.image.title="bedlight-voice-service" bedlight.status="dataset-collection"

EXPOSE 8001
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8001"]
