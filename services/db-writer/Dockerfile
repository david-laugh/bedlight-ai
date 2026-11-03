# BedLight AI — db-writer: MQTT events -> JSON Schema validation -> de-dup -> Supabase (or DRY-RUN file)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONTRACTS_DIR=/app/contracts/v1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/app/
# contracts come from the compose additional build context "contracts" (repo ./contracts)
COPY --from=contracts v1/ /app/contracts/v1/

RUN useradd --create-home --uid 10001 app && mkdir -p /data/db-writer && chown -R app /data/db-writer
USER app

LABEL org.opencontainers.image.title="bedlight-db-writer" bedlight.status="implemented"
CMD ["python", "-m", "app.main"]
