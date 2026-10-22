#!/usr/bin/env bash
# Copies the nightly Core backups in deploy/backups/ to Oracle Object Storage, compressed.
# Runs on the host from a systemd timer (deploy/systemd/). It signs in as the instance
# ("instance principal"), so no keys are stored on the server, and the IAM policy lets it
# only list and add objects: it can't overwrite or delete what's already in the bucket.
# Today's file is skipped because the backup container may still rewrite it.
set -euo pipefail

BUCKET="${STARDUST_BACKUP_BUCKET:-stardust-core-backups}"
BACKUPS="$(cd "$(dirname "$0")" && pwd)/backups"
OCI=(oci --auth instance_principal)
today="core-$(date -u +%Y%m%d).db"

uploaded="$("${OCI[@]}" os object list -bn "$BUCKET" --prefix core- --all --query 'data[].name' --raw-output)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

shopt -s nullglob
for f in "$BACKUPS"/core-*.db; do
  base="$(basename "$f")"
  name="$base.gz"
  [[ "$base" == "$today" ]] && continue
  grep -qF "\"$name\"" <<<"$uploaded" && continue
  gzip -c "$f" > "$tmp/$name"
  "${OCI[@]}" os object put -bn "$BUCKET" --name "$name" --file "$tmp/$name" --no-overwrite --no-multipart >/dev/null
  echo "uploaded $name ($(stat -c %s "$tmp/$name") bytes)"
  rm -f "$tmp/$name"
done
