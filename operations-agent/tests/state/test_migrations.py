"""Tasks 1.3, 3.5, 5.2a: Migrationen, Advisory-Lock, Schema-Versionsprüfung beim Start."""

import threading

import psycopg
import pytest

from api.__main__ import main as api_main
from persistence import cli, migrate
from persistence.db import connect
from worker.__main__ import main as worker_main


def test_initial_migration_and_idempotent_rerun(empty_db_url, capsys):
    assert cli.main(["migrate"]) == 0
    assert f"eingespielt: {[m.version for m in migrate.load_migrations()]}" in capsys.readouterr().out
    with connect(empty_db_url) as conn:
        conn.execute("INSERT INTO incidents (source, incident_key) VALUES ('maintenance', 'keep-me')")
        tables_before = conn.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()["n"]

    assert cli.main(["migrate"]) == 0
    assert "keine Änderung" in capsys.readouterr().out
    with connect(empty_db_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM incidents").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()["n"] == tables_before
        assert conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"] == migrate.expected_version()


def test_concurrent_migrations_serialize(empty_db_url):
    """ops-migrate.yml und Helm-Hook gleichzeitig: einer wartet, beide enden fehlerfrei."""
    errors, reports = [], []
    barrier = threading.Barrier(2)

    def run():
        try:
            with psycopg.connect(empty_db_url, autocommit=True) as conn:
                barrier.wait()
                reports.append(migrate.migrate(conn))
        except Exception as exc:  # pragma: no cover - Fehlerfall wird geprüft
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(len(r.applied) for r in reports) == [0, len(migrate.load_migrations())]


def test_changed_migration_is_rejected(empty_db_url):
    with psycopg.connect(empty_db_url, autocommit=True) as conn:
        migrate.migrate(conn)
        conn.execute("UPDATE schema_migrations SET checksum = 'manipuliert'")
        with pytest.raises(RuntimeError, match="Prüfsumme"):
            migrate.migrate(conn)


def test_lock_client_without_schema_points_to_ops_migrate(empty_db_url, capsys):
    code = cli.main(["lock", "acquire", "--app", "affine", "--env", "staging", "--owner", "test"])
    assert code == 3
    assert "ops-migrate.yml" in capsys.readouterr().err
    with connect(empty_db_url) as conn:
        assert conn.execute("SELECT to_regclass('operation_locks') AS t").fetchone()["t"] is None


def test_lock_client_accepts_older_schema_before_helm_hook(db_url, monkeypatch):
    """Deploy mit neuer Migration: Der Sperrclient aus dem neuen Code arbeitet noch auf dem alten Schema."""
    monkeypatch.setenv("OPS_DATABASE_URL", db_url)
    with connect(db_url) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version > 1")
    args = ["--app", "affine", "--env", "staging", "--owner", "deploy-test"]
    assert cli.main(["lock", "acquire", *args, "--wait-timeout", "0"]) == 0
    with connect(db_url) as conn:
        conn.execute("DELETE FROM schema_migrations")
        conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (0, 'alt', 'x')")
    assert cli.main(["lock", "acquire", "--app", "other", "--env", "staging", "--owner", "x", "--wait-timeout", "0"]) == 3


@pytest.mark.parametrize("entrypoint", [api_main, worker_main], ids=["api", "worker"])
def test_entrypoints_abort_on_old_schema(db_url, entrypoint, caplog):
    with connect(db_url) as conn:
        conn.execute("DELETE FROM schema_migrations")
        conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (0, 'alt', 'x')")
    assert entrypoint([]) == 3
    assert f"erwartet {migrate.expected_version()}, vorhanden 0" in caplog.text
    with connect(db_url) as conn:  # Schema unverändert
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [{"version": 0}]


def test_entrypoints_abort_without_schema(empty_db_url, caplog):
    assert worker_main() == 3
    assert "ops-migrate.yml" in caplog.text
