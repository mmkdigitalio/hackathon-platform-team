"""Tasks 1.1 und 1.2: Ereignisschema und Aufruf-/Ergebnisverträge."""

import json
from pathlib import Path

import pytest

from api import normalize
from api.catalog import Catalog
from contracts import ActionRecord, AgentInvocation, AgentResult
from persistence.event_schema import errors_for, validate_files, validator

from .conftest import REPO_ROOT

EVENTS = REPO_ROOT / "platform" / "schemas" / "events" / "examples"
CONTRACT_EXAMPLES = REPO_ROOT / "operations-agent" / "src" / "contracts" / "examples"


def _files(kind):
    return sorted((EVENTS / kind).glob("*.json"))


def test_example_sets_exist():
    names = {p.stem for p in _files("valid")}
    for expected in ("azure-monitor-fired", "azure-monitor-resolved", "github-issue-opened",
                     "github-comment-human-same-account", "maintenance-health-check-failed"):
        assert expected in names
    assert len(_files("invalid")) >= 4


@pytest.mark.parametrize("path", _files("valid"), ids=lambda p: p.name)
def test_valid_examples_pass(path, platform_root):
    assert errors_for(json.loads(path.read_text())) == []


@pytest.mark.parametrize("path", _files("invalid"), ids=lambda p: p.name)
def test_invalid_examples_fail_with_named_field(path, platform_root):
    doc = json.loads(path.read_text())
    errors = errors_for(doc["event"])
    assert errors, f"{path.name} hätte abgelehnt werden müssen"
    assert any(e.startswith(doc["expectedField"]) for e in errors), errors


def test_cli_validate(platform_root, capsys):
    assert validate_files([str(p) for p in _files("valid")]) == []


def test_normalized_payloads_conform_to_schema(platform_root):
    """Die Normalisierung erzeugt aus allen Beispielzustellungen schemakonforme Ereignisse."""
    catalog = Catalog(platform_root)
    v = validator()
    payloads = EVENTS / "payloads"
    for name in payloads.glob("azure-monitor-*.json"):
        raw = name.read_bytes()
        assert errors_for(normalize.azure_monitor(json.loads(raw), raw, catalog), v) == [], name.name
    for name, event in (("github-issue-opened.json", "issues"),
                        ("github-issue-opened-without-form.json", "issues"),
                        ("github-comment-human-same-account.json", "issue_comment")):
        raw = (payloads / name).read_bytes()
        result = normalize.github(event, "d-1", json.loads(raw), raw, catalog,
                                  {"mmkdigitalio/hackathon-platform-team"})
        assert errors_for(result, v) == [], name
    raw = (payloads / "maintenance-health-check-failed.json").read_bytes()
    assert errors_for(normalize.maintenance(json.loads(raw), raw, catalog), v) == []


# --- Vertrag mit D (contracts.v0_1) -----------------------------------------------------


def test_contract_examples_parse():
    normal = AgentInvocation.model_validate_json((CONTRACT_EXAMPLES / "invocation-normal.json").read_text())
    assert normal.resume.open_actions == []
    resumed = AgentInvocation.model_validate_json((CONTRACT_EXAMPLES / "invocation-resume.json").read_text())
    assert resumed.resume.thread_id == normal.resume.thread_id  # Checkpoint wird fortgesetzt
    dispatch = resumed.resume.open_actions[0]
    assert dispatch.kind == "workflow_dispatch" and dispatch.external_refs["runId"]
    result = AgentResult.model_validate_json((CONTRACT_EXAMPLES / "result-resolved.json").read_text())
    assert result.references["desiredState"]["imageDigest"]
    assert {f.certainty for f in result.findings} == {"confirmed", "hypothesis"}
    ActionRecord.model_validate_json((CONTRACT_EXAMPLES / "action-workflow-dispatch.json").read_text())


def test_contract_rejects_unknown_fields():
    doc = json.loads((CONTRACT_EXAMPLES / "result-resolved.json").read_text())
    doc["state"] = "resolved"
    with pytest.raises(ValueError):
        AgentResult.model_validate(doc)


def test_action_status_matches_contract():
    """C's Persistenz und D's Vertrag kennen dieselben Status."""
    from typing import get_args

    from contracts import ActionStatus as ContractStatus
    from contracts.actions import ActionStatus

    assert {s.value for s in ActionStatus} == set(get_args(ContractStatus))
