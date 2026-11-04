# Data Center Resource Ledger API. Builds for amd64 and arm64 (Oracle Ampere A1), e.g.:
#   docker buildx build --platform linux/amd64,linux/arm64 -f deploy/ledger.Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# The migrations and the import script live beside the package, so keep the ledger/ layout.
COPY ledger/ ledger/
# pip is removed once the ledger is installed, as in Core's image: it isn't needed at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && pip install --upgrade pip && pip install -e ledger && pip uninstall -y pip \
 && useradd --system --uid 10002 ledger

USER ledger
ENV LEDGER_MIGRATIONS_DIR=/app/ledger/migrations
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:8090/healthz > /dev/null || exit 1

# Behind Caddy: trust its X-Forwarded-For so per-IP limits see real clients. The port is not published.
CMD ["uvicorn", "ledger.main:app", "--host", "0.0.0.0", "--port", "8090", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
