# Stardust Core. Builds for amd64 and arm64 (Oracle Ampere A1), e.g.:
#   docker buildx build --platform linux/amd64,linux/arm64 -f deploy/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Core reads its factors from schema/ at the repo root, so keep the repo layout.
COPY schema/ schema/
COPY middleware/ middleware/
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && pip install -e middleware && useradd --system --uid 10001 stardust && mkdir -p /data && chown stardust /data

USER stardust
ENV STARDUST_DATABASE_PATH=/data/core.db \
    STARDUST_PRICING_CACHE=/data/cache/model_prices_and_context_window.json
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:8080/healthz > /dev/null || exit 1

# Behind Caddy: trust its X-Forwarded-For so per-IP limits see real clients. Core's port is not published.
CMD ["uvicorn", "stardust_core.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
