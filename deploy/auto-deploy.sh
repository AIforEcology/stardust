#!/usr/bin/env bash
# Deploys new commits on main to this server, so merged changes go live without anyone ssh-ing in.
# Runs on the host as opc from a systemd timer every 10 minutes (deploy/systemd/), and by hand.
# Pull-based: it only reads the public repository and GitHub's public API. No SSH key, token or
# webhook lives in GitHub, and nothing outside can reach in. See "Automatic deploys" in README.md.
#
# Each run:
#   1. fetches origin/main; if it's the commit in .deployed-commit, stops quietly;
#   2. deploys it only if every job of the CI workflow passed on it (still running: try next run;
#      failed: log and skip that commit, never deploy it);
#   3. rebuilds only the services the changed files belong to (changed_services below), after a fresh
#      backup of the database of each service it rebuilds;
#   4. waits up to 2 minutes for /healthz and /ledger/healthz to report ok and the new commit, from
#      inside the containers and through https://core.aifore.org, and checks the admin routes still 404;
#   5. on failure checks out the previous commit, rebuilds the same services, and alerts Slack
#      (STARDUST_ALERT_WEBHOOK in .env). The failed commit is not retried; the next commit on main is.
#
# Usage:
#   auto-deploy.sh                       deploy origin/main if CI passed (what the timer runs)
#   auto-deploy.sh --commit <sha>        deploy that commit instead, e.g. to roll back (CI must have passed)
#   auto-deploy.sh --commit <sha> --no-ci-check
#                                        same, without the CI check: only for a commit known to be good
#   auto-deploy.sh changed-services      read paths on stdin, print the services they rebuild
#   auto-deploy.sh required-checks       print the CI checks a commit must pass
#   auto-deploy.sh ci-status <sha>       print success, pending or failure for a commit
set -euo pipefail

REPO_SLUG="AIforEcology/stardust"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$DEPLOY_DIR")"
STATE="$DEPLOY_DIR/.deployed-commit"      # full SHA of the commit running now
SKIP="$DEPLOY_DIR/.auto-deploy.skip"      # a commit whose CI or deploy failed: not tried again
ALERTED="$DEPLOY_DIR/.auto-deploy.alerted" # last alert sent, so a stuck state alerts once, not every run
PUBLIC_URL="${STARDUST_PUBLIC_URL:-https://core.aifore.org}"
HEALTH_TIMEOUT="${STARDUST_HEALTH_TIMEOUT:-120}"
PREDEPLOY_KEEP=10

# The jobs of .github/workflows/ci.yml, as GitHub names their check runs. deploy/tests checks that this
# list matches ci.yml, so a renamed or added job can't be skipped silently.
REQUIRED_CHECKS=(
  "python (3.9)"
  "python (3.12)"
  "mcp"
  "browser-extension"
  "extension-version"
  "sdk-js"
  "ledger"
  "deploy-scripts"
)

log() { echo "auto-deploy: $*"; }

# Reads changed paths (one per line) on stdin and prints the Compose services to update, one per line:
#   all        deploy/docker-compose.yml changed: `docker compose up -d --build` for everything
#   core       middleware/, schema/ or deploy/Dockerfile (what Core's image is built from)
#   ledger     ledger/ or deploy/ledger.Dockerfile
#   ledger-db  deploy/ledger-db.Dockerfile (the ledger is rebuilt with it)
#   caddy      deploy/Caddyfile: Caddy is restarted to load it
# Anything else (docs, browser extension, MCP servers, providers-service, intel, .github/, these deploy
# scripts and units) needs nothing on the server: the commit is just recorded.
changed_services() {
  local path
  while IFS= read -r path; do
    case "$path" in
      deploy/docker-compose.yml) echo all ;;
      deploy/Caddyfile) echo caddy ;;
      middleware/* | schema/* | deploy/Dockerfile) echo core ;;
      deploy/ledger-db.Dockerfile) echo ledger-db; echo ledger ;;
      ledger/* | deploy/ledger.Dockerfile) echo ledger ;;
    esac
  done | sort -u
}

has() { # has <word> <list...>
  local want="$1" w; shift
  for w in "$@"; do [[ "$w" == "$want" ]] && return 0; done
  return 1
}

# Prints success, pending or failure (with the failed checks) for a commit, from the public check-runs
# API: one unauthenticated request (60 an hour are allowed; the timer makes at most 6, and only while
# a new commit waits). GITHUB_TOKEN is used if set, as in the core-uptime workflow; the server has none.
ci_status() {
  local sha="$1" json auth=()
  [[ -n "${GITHUB_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  if ! json="$(curl -fsS --max-time 30 -H "Accept: application/vnd.github+json" ${auth[@]+"${auth[@]}"} \
      "https://api.github.com/repos/$REPO_SLUG/commits/$sha/check-runs?per_page=100")"; then
    echo "pending (GitHub API unreachable)"
    return 0
  fi
  python3 -c '
import json, sys
runs = {r["name"]: r for r in json.load(sys.stdin).get("check_runs", [])}
required = sys.argv[1:]
failed = [n for n in required if n in runs and runs[n]["status"] == "completed" and runs[n]["conclusion"] != "success"]
waiting = [n for n in required if n not in runs or runs[n]["status"] != "completed"]
if failed:
    print("failure (" + ", ".join(failed) + ")")
elif waiting:
    print("pending (" + ", ".join(waiting) + ")")
else:
    print("success")
' "${REQUIRED_CHECKS[@]}" <<<"$json"
}

webhook_url() {
  local url=""
  if [[ -r "$DEPLOY_DIR/.env" ]]; then
    url="$(sed -n 's/^STARDUST_ALERT_WEBHOOK=//p' "$DEPLOY_DIR/.env" | tail -n 1)"
    url="${url%\"}"; url="${url#\"}"
  fi
  echo "$url"
}

# Logs the message and posts it to Slack, like the core-uptime workflow does. Posts once per distinct
# message, so a state that persists (a dirty tree, a failed commit) doesn't post every 10 minutes.
alert() {
  local msg="$1" url payload
  log "ALERT: $msg"
  [[ -f "$ALERTED" && "$(cat "$ALERTED")" == "$msg" ]] && return 0
  echo "$msg" > "$ALERTED"
  url="$(webhook_url)"
  if [[ -z "$url" ]]; then
    log "STARDUST_ALERT_WEBHOOK is not set in .env; not posting to Slack."
    return 0
  fi
  payload="$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' \
    ":rotating_light: Stardust auto-deploy on $(hostname): $msg")"
  # The URL is passed as curl config on a file descriptor, not as an argument, so it isn't visible in ps.
  curl -sS --fail --max-time 20 -X POST -H 'Content-Type: application/json' --data "$payload" \
    --config <(printf 'url = "%s"\n' "$url") >/dev/null || log "could not post the alert to Slack"
}

compose() { (cd "$DEPLOY_DIR" && docker compose "$@"); }

# Fresh backups, taken with the old code still running, into backups/predeploy/ (the newest
# $PREDEPLOY_KEEP of each kept). The subfolder keeps them out of the nightly rotation and off-site upload.
backup_before() {
  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  if has all "$@" || has core "$@"; then
    log "backing up Core's database to backups/predeploy/core-$ts.db"
    compose exec -T backup python -c '
import glob, os, sqlite3, sys
os.makedirs("/backups/predeploy", exist_ok=True)
src = sqlite3.connect("file:/data/core.db?mode=ro", uri=True); dst = sqlite3.connect(sys.argv[1])
src.backup(dst); dst.close(); src.close()
for old in sorted(glob.glob("/backups/predeploy/core-*.db"))[:-int(sys.argv[2])]: os.remove(old)
' "/backups/predeploy/core-$ts.db" "$PREDEPLOY_KEEP" || return 1
  fi
  if has all "$@" || has ledger "$@" || has ledger-db "$@"; then
    log "backing up the ledger database to backups/predeploy/ledger-$ts.dump"
    # shellcheck disable=SC2016 # $1 and $2 expand inside the container's sh, not here
    compose exec -T ledger-backup sh -c 'mkdir -p /backups/predeploy && pg_dump -Fc -f "$1.tmp" && mv "$1.tmp" "$1" \
      && ls -1 /backups/predeploy/ledger-*.dump | sort | head -n -"$2" | xargs -r rm -f' \
      sh "/backups/predeploy/ledger-$ts.dump" "$PREDEPLOY_KEEP" || return 1
  fi
}

# Rebuilds and restarts only the given services, with the checked-out code. GIT_COMMIT becomes the
# "commit" field of /healthz and /ledger/healthz.
rebuild() {
  local short="$1"; shift
  local build=() s
  if has all "$@"; then
    GIT_COMMIT="$short" compose up -d --build || return 1
  else
    for s in core ledger-db ledger; do has "$s" "$@" && build+=("$s"); done
    if ((${#build[@]})); then
      # --no-deps: never recreate a service that wasn't asked for (e.g. Core when only the ledger changed).
      GIT_COMMIT="$short" compose up -d --build --no-deps "${build[@]}" || return 1
    fi
  fi
  # `up` doesn't reload a changed bind-mounted Caddyfile, and checkout replaced the file, so Caddy's
  # mount still shows the old one: restart it (Core is unreachable for a second or two).
  if has caddy "$@"; then compose restart caddy || return 1; fi
}

# body_ok <json> [<commit>]: the health body says ok, and that commit if one is given.
body_ok() {
  local body="$1" short="${2:-}"
  grep -Eq '"ok": ?true' <<<"$body" || return 1
  [[ -z "$short" ]] || grep -Eq "\"commit\": ?\"$short\"" <<<"$body"
}

# Healthy within HEALTH_TIMEOUT seconds: each rebuilt service reports ok and the new commit inside its
# container; Core (and the ledger, if it or Caddy was touched) report ok through Caddy and HTTPS; the
# admin routes of both still return 404 from the internet.
healthy() {
  local short="$1"; shift
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) core_commit="" ledger_commit="" check_ledger=0 problem
  if has all "$@" || has core "$@"; then core_commit="$short"; fi
  if has all "$@" || has ledger "$@"; then ledger_commit="$short"; check_ledger=1; fi
  has caddy "$@" && check_ledger=1
  while :; do
    problem=""
    if [[ -n "$core_commit" ]] && ! body_ok "$(compose exec -T core curl -fsS --max-time 5 http://127.0.0.1:8080/healthz 2>/dev/null)" "$core_commit"; then
      problem="core /healthz (in the container) is not ok with commit $core_commit"
    elif [[ -n "$ledger_commit" ]] && ! body_ok "$(compose exec -T ledger curl -fsS --max-time 5 http://127.0.0.1:8090/healthz 2>/dev/null)" "$ledger_commit"; then
      problem="ledger /healthz (in the container) is not ok with commit $ledger_commit"
    elif ! body_ok "$(curl -fsS --max-time 10 "$PUBLIC_URL/healthz" 2>/dev/null)" "$core_commit"; then
      problem="$PUBLIC_URL/healthz is not ok"
    elif ((check_ledger)) && ! body_ok "$(curl -fsS --max-time 10 "$PUBLIC_URL/ledger/healthz" 2>/dev/null)" "$ledger_commit"; then
      problem="$PUBLIC_URL/ledger/healthz is not ok"
    elif [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PUBLIC_URL/v1/admin/keys")" != 404 ]]; then
      problem="$PUBLIC_URL/v1/admin/keys does not return 404"
    elif [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PUBLIC_URL/ledger/v1/admin/keys")" != 404 ]]; then
      problem="$PUBLIC_URL/ledger/v1/admin/keys does not return 404"
    fi
    [[ -z "$problem" ]] && return 0
    if ((SECONDS >= deadline)); then
      log "unhealthy after ${HEALTH_TIMEOUT}s: $problem"
      return 1
    fi
    sleep 5
  done
}

deploy() {
  local target="" ci_check=1 manual=0 old status start services short old_short
  while (($#)); do
    case "$1" in
      --commit) target="${2:?--commit needs a SHA}"; manual=1; shift 2 ;;
      --no-ci-check) ci_check=0; shift ;;
      *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
  done

  cd "$REPO_DIR"
  exec 9>"$DEPLOY_DIR/.auto-deploy.lock"
  if ! flock -n 9; then log "another deploy is running; skipping this run"; exit 0; fi

  if [[ ! -s "$STATE" ]] || ! old="$(git rev-parse -q --verify "$(cat "$STATE")^{commit}")"; then
    alert "$STATE is missing or not a commit. Deploy by hand once and write the commit to it (deploy/README.md, Automatic deploys)."
    exit 1
  fi

  git fetch -q origin main
  if ((manual)); then
    target="$(git rev-parse -q --verify "$target^{commit}")" || { echo "not a commit: $target" >&2; exit 2; }
  else
    target="$(git rev-parse origin/main)"
  fi
  [[ "$target" == "$old" ]] && { ((manual)) && log "already at $target"; exit 0; }
  if ((!manual)) && [[ -f "$SKIP" && "$(cat "$SKIP")" == "$target" ]]; then exit 0; fi

  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    alert "the checkout in $REPO_DIR has local changes to tracked files, so $target was not deployed. Look with \`git status\`; never reset --hard over them blindly."
    exit 1
  fi
  [[ "$(git rev-parse HEAD)" == "$old" ]] || log "warning: HEAD is $(git rev-parse --short=12 HEAD), not the recorded $old"

  if ((ci_check)); then
    status="$(ci_status "$target")"
    case "$status" in
      success) ;;
      pending*) log "CI has not finished for $target: $status. Trying again next run."; exit 0 ;;
      *) log "CI did not pass for $target: $status. Not deploying it; waiting for the next commit on main."
         echo "$target" > "$SKIP"; exit 0 ;;
    esac
  fi

  start=$SECONDS
  short="$(git rev-parse --short=12 "$target")"; old_short="$(git rev-parse --short=12 "$old")"
  mapfile -t services < <(git diff --name-only "$old" "$target" | changed_services)

  if ((${#services[@]})); then
    if ! backup_before "${services[@]}"; then
      alert "could not back up before deploying $short (${services[*]}); nothing was changed, still running $old_short."
      echo "$target" > "$SKIP"; exit 1
    fi
  fi

  git checkout -q --detach "$target"
  if ((${#services[@]} == 0)) || { rebuild "$short" "${services[@]}" && healthy "$short" "${services[@]}"; }; then
    echo "$target" > "$STATE"
    rm -f "$SKIP" "$ALERTED"
    log "deployed $old_short -> $short, rebuilt: ${services[*]:-none}, $((SECONDS - start))s"
    return 0
  fi

  log "deploy of $short failed; rolling back to $old_short"
  git checkout -q --detach "$old"
  echo "$target" > "$SKIP"
  if rebuild "$old_short" "${services[@]}" && healthy "$old_short" "${services[@]}"; then
    alert "deploy of $short (${services[*]}) failed its health checks and was rolled back to $old_short, which is healthy. It won't be retried; the next commit on main will. Logs: journalctl -u stardust-auto-deploy"
  else
    alert "deploy of $short (${services[*]}) failed AND the rollback to $old_short is unhealthy too: the site may be down. Pre-deploy backups are in deploy/backups/predeploy/. Logs: journalctl -u stardust-auto-deploy"
  fi
  exit 1
}

main() {
  case "${1:-}" in
    changed-services) changed_services ;;
    required-checks) printf '%s\n' "${REQUIRED_CHECKS[@]}" ;;
    ci-status) ci_status "${2:?ci-status needs a SHA}" ;;
    *) deploy "$@" ;;
  esac
}

# Everything is in functions, so bash has read the whole script before checkout replaces it.
main "$@"
