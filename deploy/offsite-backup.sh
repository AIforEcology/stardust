#!/usr/bin/env bash
# Copies the nightly backups in deploy/backups/ to Oracle Object Storage: Core's SQLite copies
# (core-YYYYMMDD.db, uploaded gzipped as .db.gz) and the ledger's pg_dump files
# (ledger-YYYYMMDD.dump, uploaded as they are: pg_dump -Fc output is already compressed).
# Runs on the host from a systemd timer (deploy/systemd/). It signs in as the instance
# ("instance principal"), so no keys are stored on the server, and the IAM policy lets it
# only list and add objects: it can't overwrite or delete what's already in the bucket.
# Today's files are skipped because the backup containers may still rewrite them.
set -euo pipefail

BUCKET="${STARDUST_BACKUP_BUCKET:-stardust-core-backups}"
BACKUPS="$(cd "$(dirname "$0")" && pwd)/backups"
OCI=(oci --auth instance_principal)
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
shopt -s nullglob

# upload <prefix> <extension> <gzip|plain>
upload() {
  local prefix="$1" ext="$2" how="$3" today uploaded f base name
  today="$prefix$(date -u +%Y%m%d)$ext"
  uploaded="$("${OCI[@]}" os object list -bn "$BUCKET" --prefix "$prefix" --all --query 'data[].name' --raw-output)"
  for f in "$BACKUPS/$prefix"*"$ext"; do
    base="$(basename "$f")"
    [[ "$base" == "$today" ]] && continue
    if [[ "$how" == gzip ]]; then name="$base.gz"; else name="$base"; fi
    grep -qF "\"$name\"" <<<"$uploaded" && continue
    if [[ "$how" == gzip ]]; then gzip -c "$f" > "$tmp/$name"; else cp "$f" "$tmp/$name"; fi
    "${OCI[@]}" os object put -bn "$BUCKET" --name "$name" --file "$tmp/$name" --no-overwrite --no-multipart >/dev/null
    echo "uploaded $name ($(stat -c %s "$tmp/$name") bytes)"
    rm -f "$tmp/$name"
  done
}

upload core- .db gzip
upload ledger- .dump plain
