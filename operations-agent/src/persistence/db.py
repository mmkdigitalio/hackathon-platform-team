"""Verbindungsaufbau und Schema-Versionsprüfung."""

import os

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from contracts.errors import DatabaseUnavailable, SchemaMissing, SchemaVersionMismatch

from .migrate import expected_version


def conninfo(database_url: str | None = None) -> str:
    """Verbindungsangabe, in dieser Reihenfolge:

    1. Argument bzw. ``OPS_DATABASE_URL`` (lokal),
    2. ``POSTGRES_HOST``/``_USER``/``_PASSWORD``/``_DATABASE`` aus dem Secret des Agenten (Pod), mit TLS,
    3. sonst leer: libpq liest ``PGHOST``, ``PGUSER``, ``PGPASSWORD``, ``PGDATABASE``, ``PGSSLMODE``
       (GitHub-Runner mit den Foundation-Ausgaben).
    """
    explicit = database_url or os.environ.get("OPS_DATABASE_URL")
    if explicit:
        return explicit
    env = os.environ
    if env.get("POSTGRES_HOST"):
        return make_conninfo(
            host=env["POSTGRES_HOST"],
            user=env.get("POSTGRES_USER"),
            password=env.get("POSTGRES_PASSWORD"),
            dbname=env.get("POSTGRES_DATABASE", "operations"),
            sslmode=env.get("POSTGRES_SSLMODE", "require"),
        )
    return ""


def connect(database_url: str | None = None, *, autocommit: bool = True) -> psycopg.Connection:
    """Alle Module arbeiten mit autocommit und expliziten ``conn.transaction()``-Blöcken."""
    try:
        return psycopg.connect(
            conninfo(database_url),
            autocommit=autocommit,
            row_factory=dict_row,
            connect_timeout=int(os.environ.get("OPS_DB_CONNECT_TIMEOUT", "10")),
        )
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(f"PostgreSQL nicht erreichbar: {_first_line(exc)}") from exc


def current_version(conn: psycopg.Connection) -> int | None:
    """Vorhandene Schema-Version oder None, wenn noch nie migriert wurde."""
    row = conn.execute("SELECT to_regclass('schema_migrations') AS t").fetchone()
    if _value(row, "t") is None:
        return None
    row = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
    return _value(row, "v")


def check_schema(conn: psycopg.Connection, *, include_sdk: bool = False) -> int:
    """Prüft nur die Version; verändert das Schema nie.

    ``include_sdk`` prüft zusätzlich die Checkpoint-Tabellen von D (API und Worker).
    """
    version = current_version(conn)
    expected = expected_version()
    if version is None:
        raise SchemaMissing()
    if version != expected:
        raise SchemaVersionMismatch(expected, version)
    if include_sdk:
        from agent.persistence import CheckpointSchemaMissing, check_schema as check_sdk

        try:
            check_sdk(conn)
        except CheckpointSchemaMissing as exc:
            raise SchemaVersionMismatch.sdk(str(exc)) from exc
    return version


def _value(row, key):
    if row is None:
        return None
    return row[key] if isinstance(row, dict) else row[0]


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
