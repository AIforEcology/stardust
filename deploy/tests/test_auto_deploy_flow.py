"""deploy/auto-deploy.sh end to end, with fake `docker` and `curl` on PATH and a throwaway git repository.

Checks what the script does on the server without a server: which commits it deploys, which services it
rebuilds (and never the others), the backups it takes first, the rollback and alert on a failed health
check, and that it refuses a dirty checkout. Needs bash 4+ and flock (Linux, as in CI); skipped elsewhere.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "auto-deploy.sh"


def _bash_ok() -> bool:
    if not shutil.which("flock"):
        return False
    out = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
    return out.returncode == 0 and int(out.stdout.strip() or 0) >= 4


# In CI (Linux) these must run, never skip.
pytestmark = pytest.mark.skipif(not _bash_ok() and not os.environ.get("CI"),
                                reason="needs bash 4+ and flock (runs in CI on Linux)")

FAKE_CURL = r"""#!/usr/bin/env bash
# Fake curl: GitHub's check-runs API, the health endpoints and the Slack webhook.
url="" write="" config=""
while (($#)); do
  case "$1" in
    -w) write="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    -o|-H|-X|--data|--max-time) shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ -n "$config" ]]; then cat "$config" >> "$FAKE/slack"; echo posted >> "$FAKE/slack"; exit 0; fi
case "$url" in
  https://api.github.com/*) cat "$FAKE/ci.json" ;;
  */v1/admin/keys) printf 404 ;;
  */ledger/healthz) printf '{"ok":true,"service":"ledger","commit":"%s"}' "$(cat "$FAKE/ledger")" ;;
  */healthz) printf '{"ok":true,"commit":"%s"}' "$(cat "$FAKE/core")" ;;
  *) echo "fake curl: unexpected $url" >&2; exit 7 ;;
esac
"""

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Fake docker compose: logs each call; `up --build` sets the services' reported commit, unless that
# commit is listed in $FAKE/broken (its health check then never passes).
echo "$*" >> "$FAKE/docker.log"
shift  # compose
case "$1" in
  up)
    shift; svcs=()
    for a in "$@"; do [[ "$a" == -* ]] || svcs+=("$a"); done
    ((${#svcs[@]})) || svcs=(core ledger ledger-db caddy)
    c="$GIT_COMMIT"; grep -qx "$c" "$FAKE/broken" 2>/dev/null && c="broken"
    for s in "${svcs[@]}"; do [[ "$s" == core || "$s" == ledger ]] && echo "$c" > "$FAKE/$s"; done
    ;;
  exec)
    svc="$3"
    case "$svc" in
      core|ledger) printf '{"ok":true,"commit":"%s"}' "$(cat "$FAKE/$svc")" ;;
      backup|ledger-backup) echo "$svc" >> "$FAKE/backups" ;;
    esac
    ;;
esac
"""

CHECKS = subprocess.run(["bash", str(SCRIPT), "required-checks"], capture_output=True, text=True).stdout.splitlines()


class Server:
    def __init__(self, tmp: Path):
        self.tmp, self.fake = tmp, tmp / "fake"
        self.origin, self.work, self.clone = tmp / "origin.git", tmp / "work", tmp / "stardust"
        self.fake.mkdir()
        bin_ = tmp / "bin"; bin_.mkdir()
        for name, body in (("curl", FAKE_CURL), ("docker", FAKE_DOCKER)):
            (bin_ / name).write_text(body); (bin_ / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE": str(self.fake),
                    "STARDUST_HEALTH_TIMEOUT": "3", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
        self.env.pop("GITHUB_TOKEN", None)
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=tmp)
        self.git("clone", "-q", str(self.origin), str(self.work), cwd=tmp)
        self.commit({"deploy/auto-deploy.sh": SCRIPT.read_text(), "deploy/docker-compose.yml": "services: {}\n",
                     ".gitignore": "deploy/.env\ndeploy/.deployed-commit\ndeploy/.auto-deploy.*\n"})
        self.git("clone", "-q", str(self.origin), str(self.clone), cwd=tmp)
        (self.clone / "deploy" / ".env").write_text("STARDUST_ADMIN_TOKEN=x\nSTARDUST_ALERT_WEBHOOK=https://hooks.example.org/T/B/x\n")
        (self.clone / "deploy" / ".deployed-commit").write_text(self.head() + "\n")
        for s in ("core", "ledger"):
            (self.fake / s).write_text("unknown\n")
        self.ci("success")

    def git(self, *args, cwd=None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.work, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict) -> str:
        for path, body in files.items():
            p = self.work / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
        self.git("add", "-A"); self.git("commit", "-qm", "change"); self.git("push", "-q", "origin", "main")
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def ci(self, conclusion: str, status: str = "completed"):
        runs = [{"name": n, "status": status, "conclusion": conclusion if status == "completed" else None}
                for n in CHECKS]
        (self.fake / "ci.json").write_text(json.dumps({"check_runs": runs}))

    def run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.clone / "deploy" / "auto-deploy.sh"), *args],
                              env=self.env, capture_output=True, text=True)

    def deployed(self) -> str:
        return (self.clone / "deploy" / ".deployed-commit").read_text().strip()

    def read(self, name: str) -> str:
        p = self.fake / name
        return p.read_text() if p.exists() else ""


@pytest.fixture
def server(tmp_path):
    return Server(tmp_path)


def test_up_to_date_does_nothing(server):
    r = server.run()
    assert r.returncode == 0 and r.stdout == "" and server.read("docker.log") == ""


def test_docs_only_change_is_recorded_without_rebuilding(server):
    sha = server.commit({"docs/x.md": "hi\n"})
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.deployed() == sha
    assert server.read("docker.log") == ""
    assert "rebuilt: none" in r.stdout


def test_ledger_change_rebuilds_only_the_ledger_after_a_backup(server):
    sha = server.commit({"ledger/src/ledger/api.py": "x = 1\n"})
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.deployed() == sha
    assert server.read("backups") == "ledger-backup\n"
    ups = [l for l in server.read("docker.log").splitlines() if " up " in f" {l} "]
    assert ups == ["compose up -d --build --no-deps ledger"]
    assert server.read("ledger").strip() == sha[:12]
    assert server.read("core").strip() == "unknown"  # Core was never touched
    assert server.git("rev-parse", "HEAD", cwd=server.clone) == sha
    assert f"-> {sha[:12]}, rebuilt: ledger," in r.stdout


def test_core_and_caddy_change(server):
    server.commit({"middleware/a.py": "1\n", "deploy/Caddyfile": "x\n"})
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    log = server.read("docker.log")
    assert "compose up -d --build --no-deps core" in log and "compose restart caddy" in log
    assert server.read("backups") == "backup\n"


def test_waits_for_ci_and_never_deploys_a_failed_commit(server):
    old = server.deployed()
    server.commit({"middleware/a.py": "1\n"})
    server.ci(None, status="in_progress")
    r = server.run()
    assert r.returncode == 0 and "has not finished" in r.stdout and server.deployed() == old

    server.ci("failure")
    r = server.run()
    assert r.returncode == 0 and "did not pass" in r.stdout and server.deployed() == old
    assert server.run().stdout == ""  # skipped quietly from now on
    assert server.read("docker.log") == ""

    sha = server.commit({"middleware/a.py": "2\n"})  # the fix
    server.ci("success")
    assert server.run().returncode == 0 and server.deployed() == sha


def test_failed_health_check_rolls_back_and_alerts(server):
    old = server.deployed()
    sha = server.commit({"middleware/a.py": "1\n"})
    (server.fake / "broken").write_text(sha[:12] + "\n")
    r = server.run()
    assert r.returncode == 1
    assert server.deployed() == old
    assert server.git("rev-parse", "HEAD", cwd=server.clone) == old
    ups = [l for l in server.read("docker.log").splitlines() if " up " in f" {l} "]
    assert ups == ["compose up -d --build --no-deps core"] * 2  # the deploy, then the rollback
    assert server.read("core").strip() == old[:12]
    assert "hooks.example.org" in server.read("slack") and "rolled back" in r.stdout
    # Not retried, and not re-alerted, on the next run.
    assert server.run().stdout == "" and server.read("slack").count("posted") == 1


def test_dirty_checkout_is_never_overwritten(server):
    server.commit({"middleware/a.py": "1\n"})
    (server.clone / "deploy" / "docker-compose.yml").write_text("edited on the server\n")
    r = server.run()
    assert r.returncode == 1 and "local changes" in r.stdout
    assert (server.clone / "deploy" / "docker-compose.yml").read_text() == "edited on the server\n"
    assert server.read("docker.log") == ""


def test_manual_rollback_to_a_commit(server):
    first = server.deployed()
    server.commit({"ledger/a.py": "1\n"})
    assert server.run().returncode == 0
    r = server.run("--commit", first)
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.deployed() == first and server.read("ledger").strip() == first[:12]


def test_untracked_env_and_backups_are_left_alone(server):
    env = (server.clone / "deploy" / ".env").read_text()
    (server.clone / "deploy" / "backups").mkdir()
    (server.clone / "deploy" / "backups" / "core-20261101.db").write_text("db")
    server.commit({"middleware/a.py": "1\n"})
    assert server.run().returncode == 0
    assert (server.clone / "deploy" / ".env").read_text() == env
    assert (server.clone / "deploy" / "backups" / "core-20261101.db").read_text() == "db"

