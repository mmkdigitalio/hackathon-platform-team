"""Versionierte Migrationen der Datenbank operations.

Migrationen liegen als ``NNNN_name.sql`` neben diesem Modul und laufen je in einer
eigenen Transaktion. Eine Advisory-Lock serialisiert gleichzeitige Läufe
(``ops-migrate.yml`` und Helm-Hook-Job). Ein erneuter Lauf auf aktuellem Schema
ändert nichts. API und Worker rufen dieses Modul nie auf, sie prüfen nur die Version.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib import resources

import psycopg

# Fester Schlüssel für pg_advisory_lock; beliebig, aber stabil.
MIGRATION_LOCK_KEY = 0x6F70735F6D6967  # "ops_mig"

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def load_migrations() -> list[Migration]:
    found = []
    for entry in resources.files("persistence.migrations").iterdir():
        match = _NAME.match(entry.name)
        if match:
            found.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise RuntimeError(f"Migrationen nicht lückenlos nummeriert: {versions}")
    return found


def expected_version() -> int:
    return len(load_migrations())


@dataclass
class MigrationReport:
    applied: list[int]
    version: int


def migrate(conn: psycopg.Connection) -> MigrationReport:
    """Bringt das Schema auf den neuesten Stand. ``conn`` muss autocommit sein."""
    if not conn.autocommit:
        raise ValueError("migrate() braucht eine autocommit-Verbindung")
    migrations = load_migrations()
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     integer PRIMARY KEY,
                name        text NOT NULL,
                checksum    text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        applied = {_get(r, "version", 0): _get(r, "checksum", 1) for r in rows}
        for m in migrations:
            if m.version in applied and applied[m.version] != m.checksum:
                raise RuntimeError(
                    f"Migration {m.version:04d}_{m.name} wurde nach dem Einspielen verändert "
                    "(Prüfsumme weicht ab). Änderungen nur als neue Migration."
                )
        unknown = sorted(set(applied) - {m.version for m in migrations})
        if unknown:
            raise RuntimeError(
                f"Datenbank hat neuere Migrationen {unknown} als dieses Paket; kein Downgrade."
            )
        newly = []
        for m in migrations:
            if m.version in applied:
                continue
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (m.version, m.name, m.checksum),
                )
            newly.append(m.version)
        return MigrationReport(applied=newly, version=migrations[-1].version if migrations else 0)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def _get(row, key, index):
    return row[key] if isinstance(row, dict) else row[index]
