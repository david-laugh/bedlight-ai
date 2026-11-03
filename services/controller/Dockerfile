# BedLight AI — controller (FastAPI): MQTT state aggregation, WebSocket to simulator-web, manual light control.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/app/

RUN useradd --create-home --uid 10001 app
USER app

LABEL org.opencontainers.image.title="bedlight-controller" bedlight.status="implemented"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
