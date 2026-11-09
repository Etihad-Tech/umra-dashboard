# Collector: fetches the three sources every few minutes, writes to Supabase, sends Telegram alerts.
# Build context is the repository root:  docker compose -f deploy/docker-compose.yml build
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home collector
WORKDIR /app
COPY collector/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY collector/collector ./collector
USER collector
# "alive" = every enabled source succeeded within 3 intervals (collector/healthcheck.py)
HEALTHCHECK --interval=5m --timeout=30s --start-period=15m --retries=2 CMD ["python", "-m", "collector.healthcheck"]
CMD ["python", "-m", "collector.run", "--source", "all", "--notify", "--loop"]
