"""Verbindungsaufbau und Schema-Versionsprüfung."""

import os

import psycopg
from psycopg.rows import dict_row

from contracts.errors import DatabaseUnavailable, SchemaMissing, SchemaVersionMismatch

from .migrate import expected_version


def conninfo(database_url: str | None = None) -> str:
    """Verbindungsangabe aus Argument, OPS_DATABASE_URL oder den libpq-Variablen PG*.

    Leerer String heißt: libpq liest PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGSSLMODE.
    Runner setzen diese aus den Foundation-Ausgaben (postgresAdminPassword usw.).
    """
    return database_url or os.environ.get("OPS_DATABASE_URL", "")


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


def check_schema(conn: psycopg.Connection) -> int:
    """Prüft nur die Version; verändert das Schema nie."""
    version = current_version(conn)
    expected = expected_version()
    if version is None:
        raise SchemaMissing()
    if version != expected:
        raise SchemaVersionMismatch(expected, version)
    return version


def _value(row, key):
    if row is None:
        return None
    return row[key] if isinstance(row, dict) else row[0]


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
