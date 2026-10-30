"""Testinfrastruktur von Strang C: echtes PostgreSQL, kein Modell.

Ist ``OPS_TEST_DATABASE_URL`` gesetzt (z. B. Docker-Container), wird dieser Server
verwendet. Sonst startet die Session einen temporären Cluster mit ``initdb``/``pg_ctl``
(PATH, ``pg_config --bindir`` oder Homebrew ``postgresql@*``).
"""

import glob
import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest

from persistence import migrate

REPO_ROOT = Path(__file__).resolve().parents[3]


def _pg_bin() -> Path:
    found = shutil.which("initdb")
    if found:
        return Path(found).parent
    if shutil.which("pg_config"):
        return Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    for candidate in sorted(glob.glob("/opt/homebrew/opt/postgresql@*/bin"), reverse=True):
        return Path(candidate)
    pytest.skip("kein PostgreSQL gefunden: OPS_TEST_DATABASE_URL setzen oder PostgreSQL installieren")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory):
    """Basis-URL eines Servers, auf dem Testdatenbanken angelegt werden dürfen."""
    if os.environ.get("OPS_TEST_DATABASE_URL"):
        yield os.environ["OPS_TEST_DATABASE_URL"]
        return
    bindir = _pg_bin()
    data = tmp_path_factory.mktemp("pgdata")
    port = _free_port()
    subprocess.run(
        [bindir / "initdb", "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-instructions"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [bindir / "pg_ctl", "-D", data, "-l", data / "server.log", "-w", "start",
         "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c fsync=off"],
        check=True, capture_output=True,
    )
    try:
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)


def _with_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def template_db(pg_server):
    """Einmal migrierte Vorlage; jede Testdatenbank ist eine Kopie davon."""
    name = f"ops_template_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(_with_db(pg_server, name), autocommit=True) as conn:
        migrate.migrate(conn)
    yield name
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _create(pg_server, name, template=None):
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))


def _drop(pg_server, name):
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db_url(pg_server, template_db, monkeypatch):
    """Frische, migrierte Datenbank je Test; auch als OPS_DATABASE_URL gesetzt."""
    name = f"ops_test_{uuid.uuid4().hex[:12]}"
    _create(pg_server, name, template_db)
    url = _with_db(pg_server, name)
    monkeypatch.setenv("OPS_DATABASE_URL", url)
    yield url
    _drop(pg_server, name)


@pytest.fixture
def empty_db_url(pg_server, monkeypatch):
    """Leere Datenbank ohne Schema (Erstinitialisierung)."""
    name = f"ops_empty_{uuid.uuid4().hex[:12]}"
    _create(pg_server, name)
    url = _with_db(pg_server, name)
    monkeypatch.setenv("OPS_DATABASE_URL", url)
    yield url
    _drop(pg_server, name)


@pytest.fixture
def unreachable_db_url(monkeypatch):
    url = f"postgresql://postgres@127.0.0.1:{_free_port()}/operations?connect_timeout=1"
    monkeypatch.setenv("OPS_DATABASE_URL", url)
    monkeypatch.setenv("OPS_DB_CONNECT_TIMEOUT", "1")
    return url


@pytest.fixture
def platform_root(tmp_path, monkeypatch):
    """Minimaler Katalog nach B's Vertrag: affine und operations-agent in staging."""
    root = tmp_path / "platform"
    for app, path in (("affine", "/info"), ("operations-agent", "/healthz")):
        (root / "catalog" / app).mkdir(parents=True)
        (root / "catalog" / app / "app.yaml").write_text(
            f"schemaVersion: '0.1'\nappId: {app}\navailability:\n  path: {path}\n"
        )
        (root / "environments" / "staging").mkdir(parents=True, exist_ok=True)
        (root / "environments" / "staging" / f"{app}.yaml").write_text("chartVersion: 0.1.0\n")
    # Ereignisschema aus dem Repository mitnehmen.
    shutil.copytree(REPO_ROOT / "platform" / "schemas", root / "schemas")
    monkeypatch.setenv("OPS_PLATFORM_ROOT", str(root))
    return root


def connector(url: str):
    from persistence.db import connect

    return lambda: connect(url)
