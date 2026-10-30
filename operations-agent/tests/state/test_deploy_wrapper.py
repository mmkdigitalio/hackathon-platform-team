"""Task 4.5: B's Deploy-Wrapper (platform/infra/app-runtime/scripts/ops-lock.sh) gegen C's CLI.

Läuft wie auf dem Runner: nur PG*-Variablen, keine Agenten-API.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from persistence import locks
from persistence.db import connect

from .conftest import REPO_ROOT, _pg_bin

SCRIPT = REPO_ROOT / "platform" / "infra" / "app-runtime" / "scripts" / "ops-lock.sh"
RUN_URL = "https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/777"


def env_for(url: str, tmp_path: Path, **extra) -> dict:
    parsed = urlparse(url)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "OPS_DATABASE", "GITHUB_"))}
    env.update(
        PATH=f"{Path(sys.executable).parent}:{_pg_bin()}:{env['PATH']}",
        PGHOST=parsed.hostname, PGPORT=str(parsed.port), PGUSER=parsed.username or "postgres",
        PGDATABASE=parsed.path.lstrip("/"), PGPASSWORD="egal",
        APP_ID="affine", ENVIRONMENT="staging", OPERATION_ID="op-deploy-777", RUN_URL=RUN_URL,
        RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        GITHUB_RUN_ID="777", GITHUB_RUN_ATTEMPT="1", GITHUB_SHA="3f9c2e1",
    )
    env.update(extra)
    return env


def sh(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_full_deploy_cycle(db_url, tmp_path):
    env = env_for(db_url, tmp_path)
    for step in (("acquire",), ("record", "started"), ("check",)):
        r = sh(env, *step)
        assert r.returncode == 0, (step, r.stderr)
    with connect(db_url) as conn:
        assert locks.status(conn, "affine", "staging")["owner_ref"] == RUN_URL
    result = tmp_path / "verification.json"
    result.write_text(json.dumps({"success": True, "checkRef": "sha256:" + "c" * 64}))
    assert sh(env, "record", "succeeded", str(result)).returncode == 0
    r = sh(env, "release")
    assert r.returncode == 0, r.stderr
    with connect(db_url) as conn:
        assert locks.status(conn, "affine", "staging") is None
        action = conn.execute("SELECT * FROM actions").fetchone()
    assert action["id"] == "act-deploy-777-1-affine-staging"
    assert action["status"] == "succeeded" and action["external_refs"]["runUrl"] == RUN_URL
    assert action["detail"]["result"]["success"] is True
    with connect(db_url) as conn:
        operation = conn.execute("SELECT status, finished_at FROM operations").fetchone()
    assert operation["status"] == "completed" and operation["finished_at"] is not None


def test_failed_deploy_closes_own_operation_as_failed(db_url, tmp_path):
    env = env_for(db_url, tmp_path)
    for step in (("acquire",), ("record", "started"), ("record", "failed"), ("release",)):
        assert sh(env, *step).returncode == 0, step
    with connect(db_url) as conn:
        assert conn.execute("SELECT status FROM operations").fetchone()["status"] == "failed"


def test_dispatched_run_correlates_with_agent_action(db_url, tmp_path):
    env = env_for(db_url, tmp_path, OPERATION_ID="op-agent-1", ACTION_ID="act-agent-dispatch")
    assert sh(env, "acquire").returncode == 0
    assert sh(env, "record", "started").returncode == 0
    with connect(db_url) as conn:
        action = conn.execute("SELECT * FROM actions").fetchone()
    assert action["operation_id"] == "op-agent-1"
    assert action["correlation"] == {"agentActionId": "act-agent-dispatch"}
    assert sh(env, "record", "succeeded").returncode == 0
    assert sh(env, "release").returncode == 0
    with connect(db_url) as conn:
        # Die Operation gehört dem Agenten; der Workflow lässt sie offen.
        assert conn.execute("SELECT status FROM operations").fetchone()["status"] == "active"


def test_lost_lock_fails_check(db_url, tmp_path):
    env = env_for(db_url, tmp_path)
    assert sh(env, "acquire").returncode == 0
    with connect(db_url) as conn:
        conn.execute("DELETE FROM operation_locks")
    r = sh(env, "check")
    assert r.returncode == 1 and "verloren" in r.stdout


def test_unreachable_database_without_force(unreachable_db_url, tmp_path):
    env = env_for(unreachable_db_url, tmp_path, PGCONNECT_TIMEOUT="1")
    r = sh(env, "acquire")
    assert r.returncode == 1 and "ohne force" in r.stdout


def test_unreachable_database_with_force(unreachable_db_url, tmp_path):
    env = env_for(unreachable_db_url, tmp_path, FORCE="true")
    r = sh(env, "acquire")
    assert r.returncode == 0, r.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "Break-glass" in summary and "3f9c2e1" in summary and RUN_URL in summary
    for step in (("record", "succeeded"), ("check",), ("release",)):
        assert sh(env, *step).returncode == 0


@pytest.mark.parametrize("force", ["false", "true"])
def test_force_ignored_when_reachable(db_url, tmp_path, force):
    env = env_for(db_url, tmp_path, FORCE=force)
    r = sh(env, "acquire")
    assert r.returncode == 0
    assert ("force abgelehnt" in r.stdout) == (force == "true")
