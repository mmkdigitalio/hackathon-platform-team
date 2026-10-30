"""Schema-Versionsausgabe für Auftragsverwaltung und LangGraph-Checkpoints."""

import json

import pytest

from agent.persistence import SDK_SCHEMA_VERSION
from persistence import cli, migrate
from persistence.db import connect


def test_schema_version_current(db_url, capsys):
    assert cli.main(["schema-version"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "expected": migrate.expected_version(),
        "actual": migrate.expected_version(),
        "sdkExpected": SDK_SCHEMA_VERSION,
        "sdkActual": SDK_SCHEMA_VERSION,
    }
    assert output.err == ""


@pytest.mark.parametrize("schema", ["sdk", "own"])
def test_schema_version_behind(db_url, capsys, schema):
    with connect(db_url) as conn:
        if schema == "sdk":
            conn.execute("DELETE FROM checkpoint_migrations WHERE v = %s", (SDK_SCHEMA_VERSION,))
        else:
            conn.execute("UPDATE schema_migrations SET version = version - 1")

    assert cli.main(["schema-version"]) == 3
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "expected": migrate.expected_version(),
        "actual": migrate.expected_version() - (schema == "own"),
        "sdkExpected": SDK_SCHEMA_VERSION,
        "sdkActual": SDK_SCHEMA_VERSION - (schema == "sdk"),
    }
    assert "ops migrate" in output.err
