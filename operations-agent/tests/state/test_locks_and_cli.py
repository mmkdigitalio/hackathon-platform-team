"""Tasks 4.2 bis 4.6: Operations-Sperre, Konfliktregel, Break-glass, Nachtrag, CLI."""

import threading
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest

from contracts.actions import ActionStatus
from contracts.errors import LockNotHeld, TargetLocked, UnresolvedActions
from persistence import actions, cli, locks
from persistence.db import connect

from .conftest import connector


def acquire(url, app="affine", env="staging", op="op-1", owner="owner-1", ttl=600, kind="deploy"):
    with connect(url) as conn:
        return locks.try_acquire(conn, app_id=app, environment=env, operation_id=op, owner_ref=owner,
                                 kind=kind, ttl_seconds=ttl)


# --- 4.2 Exklusive Sperre ------------------------------------------------------------------


def test_concurrent_acquire_has_one_winner(db_url):
    barrier = threading.Barrier(4)
    won, lost = [], []

    def run(i):
        barrier.wait()
        try:
            won.append(acquire(db_url, op=f"op-{i}", owner=f"owner-{i}"))
        except TargetLocked as exc:
            lost.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and len(lost) == 3
    assert all(e.operation_id == won[0].operation_id for e in lost)


def test_independent_apps_and_environments(db_url):
    acquire(db_url, app="affine", env="staging", op="op-a", owner="a")
    acquire(db_url, app="operations-agent", env="staging", op="op-b", owner="b")
    acquire(db_url, app="affine", env="production", op="op-c", owner="c")


def test_foreign_release_is_rejected(db_url):
    handle = acquire(db_url)
    foreign = handle.model_copy(update={"owner_ref": "someone-else"})
    with connect(db_url) as conn:
        with pytest.raises(LockNotHeld, match="gehört Operation op-1"):
            locks.release(conn, foreign)
        assert locks.status(conn, "affine", "staging")["owner_ref"] == "owner-1"


def test_renew_and_check_require_owner(db_url):
    handle = acquire(db_url)
    with connect(db_url) as conn:
        renewed = locks.renew(conn, handle, 900)
        assert renewed.expires_at > handle.expires_at
        locks.check(conn, handle)
        with pytest.raises(LockNotHeld):
            locks.check(conn, handle.model_copy(update={"lock_version": handle.lock_version + 99}))


# --- 4.3 Konfliktregel für Workflows --------------------------------------------------------


def test_workflow_waits_at_most_15_minutes(db_url):
    acquire(db_url, op="op-agent", owner="job:123", kind="agent")
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    with pytest.raises(TargetLocked) as exc:
        locks.acquire_waiting(connector(db_url), sleep=sleep, clock=lambda: clock[0], poll_seconds=15,
                              app_id="affine", environment="staging", operation_id="op-wf",
                              owner_ref="github-run:x/1/1", kind="deploy")
    assert exc.value.operation_id == "op-agent"
    assert clock[0] == 15 * 60 and set(sleeps) == {15}
    with connect(db_url) as conn:  # keine Zieländerung, Sperre unverändert
        assert locks.status(conn, "affine", "staging")["operation_id"] == "op-agent"
        assert conn.execute("SELECT count(*) AS n FROM actions").fetchone()["n"] == 0


def test_workflow_gets_lock_when_released_while_waiting(db_url):
    agent = acquire(db_url, op="op-agent", owner="job:123", kind="agent")
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 60:
            with connect(db_url) as conn:
                locks.release(conn, agent)

    handle = locks.acquire_waiting(connector(db_url), sleep=sleep, clock=lambda: clock[0],
                                   app_id="affine", environment="staging", operation_id="op-wf",
                                   owner_ref="github-run:x/1/1", kind="deploy")
    assert handle.operation_id == "op-wf"


def test_cli_lock_cycle(db_url, capsys, tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "gh-1"]) == 0
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())
    op, version = values["operation-id"], values["lock-version"]
    assert values["break-glass"] == "false"
    base = ["--app", "affine", "--env", "staging", "--owner", "gh-1", "--lock-version", version]
    assert cli.main(["lock", "check", *base]) == 0
    assert cli.main(["lock", "renew", *base]) == 0
    assert cli.main(["actions", "record", *base, "--operation-id", op, "--kind", "pulumi-up",
                     "--action-id", "act-wf-1", "--agent-action-id", "act-agent-1"]) == 0
    assert cli.main(["actions", "update", *base, "--operation-id", op, "--action-id", "act-wf-1",
                     "--status", "succeeded", "--external-ref", "runUrl=https://github.com/x/y/actions/runs/7"]) == 0
    assert cli.main(["lock", "release", *base, "--owner", "someone-else"]) == 7
    assert cli.main(["lock", "release", *base]) == 0
    with connect(db_url) as conn:
        assert conn.execute("SELECT status FROM operations WHERE id = %s", (op,)).fetchone()["status"] == "completed"
        action = actions.get_action(conn, "act-wf-1")
    assert action.status == "succeeded" and action.external_refs["runUrl"].endswith("/runs/7")
    assert action.correlation == {"agentActionId": "act-agent-1"}


def test_cli_busy_lock_exits_4(db_url, capsys):
    acquire(db_url, op="op-agent", owner="job:1", kind="agent")
    code = cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "gh-2",
                     "--wait-timeout", "0"])
    assert code == 4
    assert "gesperrt durch Operation op-agent" in capsys.readouterr().err


# --- 4.4 Break-glass -------------------------------------------------------------------------


def test_without_force_unreachable_db_aborts(unreachable_db_url, tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "gh-1"])
    assert code == 5
    assert "Break-glass nur mit --force" in capsys.readouterr().err
    assert not summary.exists()


def test_force_with_unreachable_db_writes_summary(unreachable_db_url, tmp_path, monkeypatch):
    summary, out = tmp_path / "summary.md", tmp_path / "out"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_SHA", "3f9c2e1aa")
    monkeypatch.setenv("GITHUB_RUN_ID", "555")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mmkdigitalio/hackathon-platform-team")
    code = cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--force"])
    assert code == 0
    text = summary.read_text()
    assert "BREAK-GLASS" in text
    for expected in ("affine", "staging", "3f9c2e1aa", "/actions/runs/555", "ops lock backfill"):
        assert expected in text
    assert "break-glass=true" in out.read_text()


def test_force_is_ignored_when_db_reachable(db_url, capsys):
    acquire(db_url, op="op-agent", owner="job:1", kind="agent")
    code = cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "gh-3",
                     "--force", "--wait-timeout", "0"])
    assert code == 4
    assert "--force ist wirkungslos" in capsys.readouterr().out


def test_backfill_after_recovery(db_url, capsys):
    args = ["lock", "backfill", "--app", "affine", "--env", "staging", "--git-sha", "3f9c2e1aa",
            "--workflow-run", "https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/555",
            "--at", "2026-10-30T10:00:00+00:00"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0  # idempotent
    with connect(db_url) as conn:
        ops_rows = conn.execute("SELECT * FROM operations WHERE kind = 'break-glass'").fetchall()
        acts = conn.execute("SELECT * FROM actions").fetchall()
    assert len(ops_rows) == 1 and len(acts) == 1
    op = ops_rows[0]
    assert op["note"].startswith("break-glass") and op["git_sha"] == "3f9c2e1aa"
    assert op["workflow_run"].endswith("/runs/555")
    assert op["started_at"] == datetime(2026, 10, 30, 10, tzinfo=UTC)


# --- 4.5 Client ohne API, nur mit PG*-Variablen wie auf dem Runner ---------------------------


def test_cli_with_libpq_env_like_runner(db_url, monkeypatch, capsys):
    parsed = urlparse(db_url)
    monkeypatch.delenv("OPS_DATABASE_URL")
    monkeypatch.setenv("PGHOST", parsed.hostname)
    monkeypatch.setenv("PGPORT", str(parsed.port))
    monkeypatch.setenv("PGUSER", parsed.username)
    monkeypatch.setenv("PGPASSWORD", "ignored-by-trust-auth")
    monkeypatch.setenv("PGDATABASE", parsed.path.lstrip("/"))
    assert cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "gh-runner"]) == 0
    assert cli.main(["lock", "status", "--app", "affine", "--env", "staging"]) == 0
    assert '"owner_ref": "gh-runner"' in capsys.readouterr().out


# --- 4.6 Abgelaufene Sperre mit ungeklärter Änderung ------------------------------------------


def test_expired_lock_with_unclear_action_is_not_reassigned(db_url, capsys):
    import time

    handle = acquire(db_url, op="op-old", owner="gh-old", ttl=1)
    with connect(db_url) as conn:
        action_id = actions.record_action(conn, handle, operation_id="op-old", kind="pulumi-up", target="affine").action_id
        actions.update_status(conn, handle, action_id, ActionStatus.STARTED)
    time.sleep(1.2)
    with pytest.raises(UnresolvedActions) as exc:
        acquire(db_url, op="op-new", owner="gh-new")
    assert exc.value.action_ids == [action_id]
    with connect(db_url) as conn:  # alter Besitzer darf nichts mehr schreiben
        with pytest.raises(LockNotHeld):
            actions.record_action(conn, handle, operation_id="op-old", kind="helm", target="affine")

    assert cli.main(["actions", "resolve", "--action-id", action_id, "--status", "failed",
                     "--reason", "Lauf abgebrochen, Ziel unverändert", "--by", "tester"]) == 0
    assert acquire(db_url, op="op-new", owner="gh-new").operation_id == "op-new"


def test_expired_lock_without_open_actions_is_reassigned(db_url):
    import time

    acquire(db_url, op="op-old", owner="gh-old", ttl=1)
    time.sleep(1.2)
    assert acquire(db_url, op="op-new", owner="gh-new").owner_ref == "gh-new"
