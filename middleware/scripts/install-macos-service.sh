#!/usr/bin/env bash
# Install or update Stardust Core as a macOS login service (a LaunchAgent).
#
# The service runs from a copy on the internal disk: Core is installed (not linked) into
# its own virtualenv, and its settings files and price list are copied next to the
# database. It never reads the repository at runtime, so it starts at login even if the
# repo lives on an external drive (macOS blocks background services from removable
# volumes). Re-run this script after changing Core to update the copy and restart.
#
#   middleware/scripts/install-macos-service.sh              install or update, then start
#   middleware/scripts/install-macos-service.sh --uninstall  stop and remove the service (keeps your data)
#
# Environment: PYTHON (default python3), STARDUST_PORT (default 8080).
# Extra Core settings (e.g. STARDUST_OTLP_ENDPOINT=...) go in
# ~/Library/Application Support/stardust/service.env, one KEY=VALUE per line; they are
# applied on every install or update.
set -euo pipefail

LABEL="org.aifore.stardust-core"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$HOME/Library/Application Support/stardust"
VENV="$DATA/venv"
CONFIG="$DATA/config"
LOGS="$HOME/Library/Logs/stardust"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${STARDUST_PORT:-8080}"
DOMAIN="gui/$(id -u)"

stop_service() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  # bootout returns before the old process has exited; starting again too soon fails with
  # "Bootstrap failed: 5". Wait until launchd has forgotten the service.
  for _ in $(seq 1 60); do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || return 0
    sleep 0.25
  done
  echo "The old service didn't stop within 15 seconds" >&2
  return 1
}

if [[ "${1:-}" == "--uninstall" ]]; then
  stop_service
  rm -f "$PLIST"
  echo "Removed the $LABEL service. Your data is still in: $DATA"
  exit 0
fi

mkdir -p "$CONFIG" "$LOGS" "$HOME/Library/LaunchAgents"

echo "Installing Stardust Core into $VENV"
[[ -x "$VENV/bin/python" ]] || "${PYTHON:-python3}" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# Build from a clean copy on the internal disk: macOS writes "._*" files on exFAT/FAT
# drives, including mid-build, which breaks packaging.
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
rsync -a --exclude '._*' --exclude '*.egg-info' --exclude '__pycache__' --exclude '/build/' --exclude '/dist/' \
  --exclude '/data/' --exclude '/tests/' \
  "$REPO/middleware/" "$BUILD/middleware/"
"$VENV/bin/pip" wheel -q --no-deps -w "$BUILD/dist" "$BUILD/middleware"
WHEEL="$(ls "$BUILD"/dist/stardust_core-*.whl)"
"$VENV/bin/pip" install -q "$WHEEL"                                # dependencies
"$VENV/bin/pip" install -q --force-reinstall --no-deps "$WHEEL"    # always take the current code

echo "Copying settings files to $CONFIG"
METHODOLOGY="$(ls "$REPO"/schema/factors/methodology-v*.json | sort -V | tail -1)"
cp "$METHODOLOGY" "$CONFIG/methodology.json"
cp "$REPO/schema/esc.json" "$CONFIG/esc.json"
cp "$REPO/middleware/data/model_prices_and_context_window.json" "$CONFIG/model_prices.json"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# Extra settings from service.env (KEY=VALUE lines; blank lines and # comments ignored).
EXTRA_ENV=""
if [[ -f "$DATA/service.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    key="${line%%=*}"; value="${line#*=}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "Skipping invalid line in service.env: $line" >&2; continue; }
    EXTRA_ENV+="    <key>$key</key><string>$(xml "$value")</string>"$'\n'
  done < "$DATA/service.env"
fi
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$VENV/bin/uvicorn")</string>
    <string>stardust_core.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$DATA")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>STARDUST_DATABASE_PATH</key><string>$(xml "$DATA/core.db")</string>
    <key>STARDUST_METHODOLOGY_FILE</key><string>$(xml "$CONFIG/methodology.json")</string>
    <key>STARDUST_ESC_FILE</key><string>$(xml "$CONFIG/esc.json")</string>
    <key>STARDUST_PRICING_FILE</key><string>$(xml "$CONFIG/model_prices.json")</string>
    <key>STARDUST_PRICING_CACHE</key><string>$(xml "$DATA/pricing-cache.json")</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
${EXTRA_ENV}  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$(xml "$LOGS/core.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOGS/core.log")</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST" >/dev/null

echo "Starting the service"
stop_service
launchctl bootstrap "$DOMAIN" "$PLIST"

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null; then
    echo "Stardust Core is running on http://127.0.0.1:$PORT (starts at login; logs: $LOGS/core.log)"
    exit 0
  fi
  sleep 0.5
done
echo "Stardust Core didn't answer on port $PORT. Last log lines:" >&2
tail -20 "$LOGS/core.log" >&2
exit 1
