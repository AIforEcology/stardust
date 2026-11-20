# The Stardust Sandbox Provider (test only): the mock reforestation provider from providers-service,
# reachable only by Core on the Compose network (no published port, no Caddy route). Sandbox keys get
# their quotes from it; it removes no carbon and moves no money. Builds for amd64 and arm64.
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY providers-service/ providers-service/
# Same image hygiene as Core's Dockerfile: Debian security fixes in, pip out once installed.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && pip install --upgrade pip && pip install ./providers-service && pip uninstall -y pip \
 && useradd --system --uid 10002 sandbox

USER sandbox
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -fsS http://127.0.0.1:8081/provider > /dev/null || exit 1

CMD ["uvicorn", "stardust_provider.examples.mock_reforestation:app", "--host", "0.0.0.0", "--port", "8081"]
