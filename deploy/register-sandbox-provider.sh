#!/usr/bin/env bash
# Register the Stardust Sandbox Provider with Core and approve it as a sandbox provider (tier emerging).
# Run once on the server, from deploy/, after `docker compose up -d --build`:
#
#   ./register-sandbox-provider.sh
#
# Safe to re-run: registering again updates the same provider, and approving again keeps it sandbox-only.
# The admin token never leaves the core container: the calls run inside it, against localhost.
set -euo pipefail
cd "$(dirname "$0")"

PROVIDER_URL="http://sandbox-provider:8081"
PROVIDER_ID="mock-reforestation"

admin() {  # admin METHOD PATH JSON
  docker compose exec -T -e METHOD="$1" -e APATH="$2" -e BODY="$3" core sh -c '
    curl -fsS -X "$METHOD" -H "X-Stardust-Admin-Token: $STARDUST_ADMIN_TOKEN" -H "Content-Type: application/json" \
      -d "$BODY" "http://localhost:8080$APATH"'
}

echo "Waiting for the sandbox provider..."
for _ in $(seq 1 30); do
  if docker compose exec -T core curl -fsS "$PROVIDER_URL/provider" > /dev/null 2>&1; then break; fi
  sleep 2
done
docker compose exec -T core curl -fsS "$PROVIDER_URL/provider" > /dev/null \
  || { echo "Core can't reach $PROVIDER_URL; is the sandbox-provider service running?" >&2; exit 1; }

echo "Registering $PROVIDER_URL"
admin POST /v1/admin/providers "{\"base_url\": \"$PROVIDER_URL\"}" > /dev/null
echo "Approving $PROVIDER_ID as a sandbox provider (tier emerging)"
approved=$(admin POST "/v1/admin/providers/$PROVIDER_ID/approve" '{"tier": "emerging", "sandbox": true}')
case "$approved" in
  *'"sandbox":true'*|*'"sandbox": true'*) ;;
  *) echo "Approval didn't come back as sandbox: $approved" >&2; exit 1 ;;
esac

echo "Done. /healthz now shows:"
docker compose exec -T core curl -fsS http://localhost:8080/healthz | python3 -c \
  'import json,sys; h=json.load(sys.stdin); print(json.dumps({k: h.get(k) for k in ("remediation_auth", "providers")}))' \
  2>/dev/null || docker compose exec -T core curl -fsS http://localhost:8080/healthz
