"""Tasks 2.5 (Issue-Form), 2.8 (ops health-check) und 5.4/5.5 (Laufnachweise)."""

import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from api.normalize import issue_form_field
from persistence import cli, health_check
from persistence.db import connect
from persistence.runs import get_run

from . import fakes
from .conftest import REPO_ROOT
from .test_worker_and_states import enqueue, make_worker

FORM = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "support.yml"


# --- 2.5 Issue-Form ------------------------------------------------------------------------


def _render(form: dict, values: dict) -> str:
    """Rendert den Issue-Text so, wie GitHub eine Issue-Form ausgibt."""
    parts = []
    for item in form["body"]:
        if item["type"] == "markdown":
            continue
        label = item["attributes"]["label"]
        parts.append(f"### {label}\n\n{values.get(item['id']) or '_No response_'}")
    return "\n\n".join(parts)


def test_issue_form_has_app_and_environment_dropdowns():
    form = yaml.safe_load(FORM.read_text())
    fields = {i["id"]: i for i in form["body"] if i["type"] != "markdown"}
    assert fields["app"]["type"] == "dropdown" and fields["app"]["attributes"]["label"] == "App"
    assert fields["environment"]["type"] == "dropdown" and fields["environment"]["attributes"]["label"] == "Umgebung"
    assert fields["app"]["validations"]["required"] and fields["environment"]["validations"]["required"]
    rendered = _render(form, {"app": "affine", "environment": "staging", "description": "kaputt"})
    assert issue_form_field(rendered, "App") == "affine"
    assert issue_form_field(rendered, "Umgebung") == "staging"


def test_issue_form_options_match_catalog():
    catalog = REPO_ROOT / "platform" / "catalog"
    if not catalog.is_dir():
        pytest.skip("platform/catalog/ noch nicht geliefert (Strang B)")
    apps = sorted(p.parent.name for p in catalog.glob("*/app.yaml") if p.parent.name != "operations-agent")
    form = yaml.safe_load(FORM.read_text())
    options = next(i for i in form["body"] if i.get("id") == "app")["attributes"]["options"]
    # Jede Katalog-App ist auswählbar; noch nicht katalogisierte Optionen (affine bis zur
    # Lieferung) führen beim Eingang zu einem sichtbaren Zuordnungsfehler.
    assert set(apps) <= set(options)


@pytest.mark.parametrize("body,expected", [
    ("### App\n\naffine\n\n### Umgebung\n\nstaging", ("affine", "staging")),
    ("### App\r\n\r\naffine\r\n\r\n### Umgebung\r\n\r\nstaging\r\n", ("affine", "staging")),
    ("### App\n\n_No response_\n\n### Umgebung\n\nstaging", (None, "staging")),
    ("AFFiNE geht nicht", (None, None)),
    (None, (None, None)),
])
def test_issue_form_parsing(body, expected):
    assert (issue_form_field(body, "App"), issue_form_field(body, "Umgebung")) == expected


# --- 2.8 Gesundheitscheck -------------------------------------------------------------------


def _args(**kw):
    return SimpleNamespace(env="staging", base_domain="20-50-60-70.sslip.io",
                           api_host="operations-agent.20-50-60-70.sslip.io", scheme="https",
                           timeout=5, dry_run=False, **kw)


def _client(app_status: int, requests: list):
    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path == "/events/maintenance":
            return httpx.Response(202, json={"status": "accepted"})
        return httpx.Response(app_status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_healthy_check_sends_nothing(platform_root, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    requests = []
    assert health_check.run_health_check(_args(), _client(200, requests)) == 0
    assert [str(r.url) for r in requests] == ["https://affine.20-50-60-70.sslip.io/info"]
    text = summary.read_text()
    assert "| affine | staging |" in text and "ok" in text


def test_failed_check_sends_maintenance_job_with_key(platform_root, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("MAINTENANCE_API_KEY", "maint-key-0123456789abcdef")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    requests = []
    assert health_check.run_health_check(_args(), _client(503, requests)) == 0
    post = requests[-1]
    assert str(post.url) == "https://operations-agent.20-50-60-70.sslip.io/events/maintenance"
    assert post.headers["x-api-key"] == "maint-key-0123456789abcdef"
    body = json.loads(post.content)
    assert body["appId"] == "affine" and body["eventType"] == "health-check-failed"
    assert body["deliveryId"] == "health-check:42:1:affine:staging"
    assert "maint-key" not in (tmp_path / "summary.md").read_text()


# --- 5.4 / 5.5 Laufnachweise ---------------------------------------------------------------


def test_complete_run_evidence_without_secrets(db_url, platform_root, monkeypatch, capsys):
    secret = "super-geheimer-maintenance-key-123"
    monkeypatch.setenv("MAINTENANCE_API_KEY", secret)
    job_id = enqueue(db_url, platform_root)

    def script(inv, services):
        result = fakes.restart_and_resolve(inv, services)
        result.references["httpProbe"] = {"url": "https://affine/info", "headers": {"X-Api-Key": secret},
                                          "note": f"503 (key {secret})"}
        return result

    make_worker(db_url, script)[0].run_once()
    with connect(db_url) as conn:
        run = get_run(conn, job_id)

    assert run["job"]["state"] == "resolved"
    assert run["events"][0]["source"] == "maintenance"
    assert run["incident"]["incident_key"] == "health-check:affine:staging:availability"
    assert [t["to_state"] for t in run["transitions"]][-1] == "resolved"
    execution = run["executions"][0]
    assert execution["modelSettings"] == {"provider": "test", "model": "scripted"}
    assert execution["usage"]["input_tokens"] == 1000
    assert execution["configRef"] == "platform/environments/staging/affine.yaml@3f9c2e1"
    assert execution["references"]["desiredState"]["imageDigest"] == fakes.DIGEST
    assert run["toolCalls"][0]["tool"] == "get_workload_state"
    assert run["actions"][0]["kind"] == "restart" and run["actions"][0]["external_refs"]["rollout"] == "complete"
    assert run["operations"][0]["kind"] == "agent"
    assert {v["kind"] for v in run["verifications"]} == {"smoke-test", "k8s-state"}
    assert all(v["release"]["image_digest"] for v in run["verifications"])
    assert run["findings"]["confirmed"][0]["provenance"] == "tool:get_workload_state"
    assert run["findings"]["suspected"]

    text = json.dumps(run)
    assert secret not in text
    assert "***" in text

    assert cli.main(["runs", "show", job_id]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["job"]["id"] == job_id and secret not in out
