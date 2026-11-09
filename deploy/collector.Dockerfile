# Collector: fetches the three sources every few minutes, writes to Supabase, sends Telegram alerts.
# Build context is the repository root:  docker compose -f deploy/docker-compose.yml build
# Base image pinned to an exact version AND digest (multi-arch index of python:3.12.16-slim, Debian trixie).
# To upgrade: docker buildx imagetools inspect python:3.12.<x>-slim  -> put the new tag and "Digest:" here.
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --system --uid 10001 --no-create-home collector
WORKDIR /app
COPY collector/requirements.txt ./
# every package (requests and all its dependencies) must match a sha256 in requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY collector/collector ./collector
USER collector
# "alive" = every enabled source succeeded within 3 intervals (collector/healthcheck.py). Docker only reports
# "unhealthy"; a STUCK loop is ended by the collector's own watchdog (exit code 70), and the restart policy restarts it.
HEALTHCHECK --interval=5m --timeout=30s --start-period=15m --retries=2 CMD ["python", "-m", "collector.healthcheck"]
CMD ["python", "-m", "collector.run", "--source", "all", "--notify", "--loop"]
