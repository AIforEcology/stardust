"""deploy/auto-deploy.sh: which services a change rebuilds, and which CI checks a commit must pass.

These run the script's own subcommands, so they need bash but not Docker, git history or the network.
"""

import itertools
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "auto-deploy.sh"


def run(*args: str, stdin: str = "") -> list:
    out = subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True, text=True, check=True)
    return out.stdout.split("\n")[:-1]


def services(*paths: str) -> list:
    return run("changed-services", stdin="".join(p + "\n" for p in paths))


@pytest.mark.parametrize("paths, expected", [
    (["middleware/src/stardust_core/api.py"], ["core"]),
    (["schema/methodology/default.json"], ["core"]),
    (["deploy/Dockerfile"], ["core"]),
    (["ledger/src/ledger/api.py"], ["ledger"]),
    (["ledger/migrations/0007_x.sql"], ["ledger"]),
    (["deploy/ledger.Dockerfile"], ["ledger"]),
    (["deploy/ledger-db.Dockerfile"], ["ledger", "ledger-db"]),
    (["deploy/Caddyfile"], ["caddy"]),
    (["deploy/docker-compose.yml"], ["all"]),
    (["deploy/docker-compose.yml", "deploy/Caddyfile"], ["all", "caddy"]),
    (["middleware/README.md", "ledger/tests/test_api.py"], ["core", "ledger"]),
])
def test_changed_services(paths, expected):
    assert services(*paths) == expected


@pytest.mark.parametrize("path", [
    "README.md", "docs/architecture/ledger.md", "CHANGELOG.md",
    "subscriber-client/browser-extension/src/popup.ts", "subscriber-client/sdk-js/package.json",
    "mcp/server.py", "providers-service/src/app.py", "intel/data/grids.json",
    ".github/workflows/ci.yml", ".github/workflows/core-uptime.yml",
    "deploy/README.md", "deploy/.env.example", "deploy/auto-deploy.sh", "deploy/offsite-backup.sh",
    "deploy/systemd/stardust-auto-deploy.timer", "deploy/tests/test_auto_deploy.py",
    # Prefixes must match whole directories, not names that merely start the same way.
    "middleware-notes.md", "ledgers.md", "deploy/Dockerfile.bak",
])
def test_changes_that_need_no_rebuild(path):
    assert services(path) == []


def test_no_paths_no_services():
    assert services() == []


def ci_job_check_names() -> list:
    """The check-run names GitHub gives the CI workflow's jobs: `name`, or the job id plus matrix values."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    names = []
    for job_id, job in jobs.items():
        base = job.get("name", job_id)
        matrix = {k: v for k, v in (job.get("strategy") or {}).get("matrix", {}).items()
                  if k not in ("include", "exclude")}
        if not matrix:
            names.append(base)
            continue
        for combo in itertools.product(*matrix.values()):
            names.append(f"{base} ({', '.join(str(v) for v in combo)})")
    return names


def test_required_checks_match_the_ci_workflow():
    # A job added to or renamed in ci.yml must be added here too, or auto-deploy would wait on a check
    # that never comes (or skip one that should block a deploy).
    assert sorted(run("required-checks")) == sorted(ci_job_check_names())
