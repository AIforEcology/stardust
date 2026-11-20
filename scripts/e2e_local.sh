#!/usr/bin/env bash
# Run the sandbox end-to-end checks against a throwaway local Core and the sandbox provider.
# Used by CI (ci.yml, job e2e-sandbox); also handy locally:
#
#   scripts/e2e_local.sh            # Python and JavaScript SDKs
#   scripts/e2e_local.sh py         # Python only
#
# Needs middleware and providers-service installed (pip install -e middleware -e providers-service), and for
# the JS check, subscriber-client/sdk-js built. The admin token and key are generated here, used once and
# never written to disk; the database is a temporary file.
set -euo pipefail
cd "$(dirname "$0")/.."
which="${1:-all}"
PY="${PYTHON:-python3}"
CORE_PORT="${CORE_PORT:-18080}" PROVIDER_PORT="${PROVIDER_PORT:-18081}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$work"; }
trap cleanup EXIT

# In GitHub Actions, hide generated secrets from the log.
mask() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::$1"; fi; }
admin_token="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')"
mask "$admin_token"

"$PY" -m uvicorn stardust_provider.examples.mock_reforestation:app --port "$PROVIDER_PORT" --log-level warning &
pids+=($!)
(cd middleware && STARDUST_ADMIN_TOKEN="$admin_token" STARDUST_DATABASE_PATH="$work/core.db" \
  STARDUST_PRICING_REFRESH=off STARDUST_INGEST_AUTH=required STARDUST_SUBSCRIBER_AUTH=required \
  STARDUST_REMEDIATION_AUTH=required exec "$PY" -m uvicorn stardust_core.main:app --port "$CORE_PORT" --log-level warning) &
pids+=($!)

base="http://127.0.0.1:$CORE_PORT"
for _ in $(seq 1 60); do
  curl -fsS "$base/healthz" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$PROVIDER_PORT/provider" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "$base/healthz" >/dev/null

admin() { curl -fsS -X POST -H "X-Stardust-Admin-Token: $admin_token" -H "Content-Type: application/json" -d "$2" "$base$1"; }
admin /v1/admin/providers "{\"base_url\": \"http://127.0.0.1:$PROVIDER_PORT\"}" >/dev/null
admin /v1/admin/providers/mock-reforestation/approve '{"tier": "emerging", "sandbox": true}' >/dev/null

new_key() {
  admin /v1/admin/keys "{\"org_id\": \"$("$PY" -c 'import uuid; print(uuid.uuid4())')\", \"label\": \"e2e\", \"sandbox\": true,
    \"scopes\": [\"ingest.write\", \"reporting.read\", \"remediation.write\"]}" | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["key"])'
}

status=0
if [ "$which" = all ] || [ "$which" = py ]; then
  key="$(new_key)"; mask "$key"
  STARDUST_API_BASE="$base" STARDUST_API_KEY="$key" "$PY" scripts/e2e_sandbox.py || status=1
fi
if [ "$which" = all ] || [ "$which" = js ]; then
  key="$(new_key)"; mask "$key"
  STARDUST_API_BASE="$base" STARDUST_API_KEY="$key" node scripts/e2e_sandbox.mjs || status=1
fi

# A live key must never see the sandbox provider.
live="$(admin /v1/admin/keys "{\"org_id\": \"$("$PY" -c 'import uuid; print(uuid.uuid4())')\", \"scopes\": [\"remediation.write\"]}" \
  | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["key"])')"
mask "$live"
n="$(curl -fsS -X POST -H "Authorization: Bearer $live" -H "Content-Type: application/json" -d '{"co2e_g": 1000}' \
  "$base/v1/remediation/quotes" | "$PY" -c 'import json, sys; print(len(json.load(sys.stdin)["quotes"]))')"
if [ "$n" = 0 ]; then echo "  ok    a live key gets no sandbox quotes"; else echo "  FAIL  a live key got $n sandbox quote(s)"; status=1; fi
exit "$status"
