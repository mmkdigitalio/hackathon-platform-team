"""SDK-Checkpoint-Anbindung (LangGraph ``PostgresSaver``) ohne eigene Initialisierung.

D ruft ``PostgresSaver.setup()`` nie auf. Die Tabellen legt ausschließlich C's ``ops migrate`` an;
dafür liefert ``sdk_migrations()`` die SQL-Schritte der gepinnten Version.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.base import MIGRATIONS
from psycopg import Connection
from psycopg.rows import dict_row

SDK_SCHEMA_VERSION = len(MIGRATIONS) - 1


@dataclass(frozen=True)
class SdkMigration:
    version: int
    sql: str
    # CREATE INDEX CONCURRENTLY läuft nicht in einem Transaktionsblock.
    requires_autocommit: bool


def sdk_migrations() -> list[SdkMigration]:
    """Für C: Schritte in Reihenfolge; Version ``v`` danach in ``checkpoint_migrations`` eintragen."""
    return [SdkMigration(v, sql, "CONCURRENTLY" in sql.upper()) for v, sql in enumerate(MIGRATIONS)]


def apply_sdk_migrations(conn: Connection) -> int:
    """Referenz für C's ``ops migrate``; nie zur Laufzeit von API, Worker oder Agent aufrufen.

    Erwartet eine Verbindung mit ``autocommit=True``; C serialisiert über seine Advisory-Lock.
    """
    with conn.cursor() as cur:
        cur.execute(MIGRATIONS[0])
        cur.execute("SELECT COALESCE(MAX(v), -1) AS v FROM checkpoint_migrations")
        current = cur.fetchone()["v"]
        for step in sdk_migrations()[current + 1 :]:
            cur.execute(step.sql)
            cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s) ON CONFLICT DO NOTHING", (step.version,))
    return SDK_SCHEMA_VERSION


class CheckpointSchemaMissing(Exception):
    pass


def check_schema(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('checkpoint_migrations') IS NOT NULL AS present")
        if not cur.fetchone()["present"]:
            raise CheckpointSchemaMissing("SDK-Tabellen fehlen; `ops migrate` ausführen")
        cur.execute("SELECT COALESCE(MAX(v), -1) AS v FROM checkpoint_migrations")
        version = cur.fetchone()["v"]
    if version != SDK_SCHEMA_VERSION:
        raise CheckpointSchemaMissing(f"SDK-Schema erwartet v{SDK_SCHEMA_VERSION}, vorhanden v{version}; `ops migrate` ausführen")
    return version


def connect(dsn: str) -> Connection:
    """Verbindungsparameter wie von ``PostgresSaver.from_conn_string`` erwartet."""
    return Connection.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)


def checkpointer(conn: Connection) -> PostgresSaver:
    check_schema(conn)
    return PostgresSaver(conn)
