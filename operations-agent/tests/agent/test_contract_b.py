"""Vertragstests gegen die echten Dateien von Strang B (Katalog, Sollzustand, Workflows)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.context import ContextLoader, assess_drift, desired_digest, desired_replicas
from tools.dispatch import mentions
from tools.targets import Target

from .conftest import REPO, World
from .fakes import FakeDeployment
from .test_tools import smoke

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "platform/schemas/app-catalog/examples/valid"
pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="Repository-Dateien von Strang B nicht vorhanden (z. B. im Image)")


def load(world: World, app: str, catalog: Path, desired: Path, runbook_files: dict[tuple[str, str], str]):
    world.github.files[(REPO, f"platform/catalog/{app}/app.yaml")] = catalog.read_text()
    world.github.files[(REPO, f"platform/environments/staging/{app}.yaml")] = desired.read_text()
    world.github.files.update(runbook_files)
    inv = world.invocation(app_id=app)
    return ContextLoader(world.github.client(), world.k8s()).load(inv, Target(app, "staging"), [])


def test_affine_example_is_understood(world: World) -> None:
    rb = {("mmkdigitalio/hackathon-platform-affine", "runbook/README.md"): "# Runbook"}
    ctx = load(world, "affine", EXAMPLES / "catalog/affine/app.yaml", EXAMPLES / "environments/staging/affine.yaml", rb)
    assert ctx.allowed_actions == {"restart", "redeploy"}
    assert ctx.deployment == "affine" and ctx.check_path == "/info"
    assert desired_replicas(ctx.desired.value) == 1
    assert desired_digest(ctx.desired.value) == "sha256:e442d8e6a4923ecf037c44cfe6ce9a6c78de415250f029c1a57aac6ca37dc7c7"
    assert ctx.runbook is not None and ctx.errors == []
    assert assess_drift(ctx)[0] == "image_drift"  # Fake-Cluster läuft mit anderem Digest

    st = ctx.smoke_test
    ev = smoke(world).run(Target("affine", "staging"), "affine", st["image"], st["secrets"], "op-1", 60, static_env=st["env"])
    env = world.cluster.jobs[("affine-staging", ev.job_name)]["body"].spec.template.spec.containers[0].env
    by_name = {e.name: e for e in env}
    assert by_name["SMOKE_USER_EMAIL"].value == "smoke@affine.invalid"
    assert by_name["SMOKE_USER_PASSWORD"].value_from.secret_key_ref.name == "affine-platform"


def test_agent_catalog_runbook_directory_and_acr_image(world: World) -> None:
    docs = ROOT / "platform/operations/agent"
    rb = {(REPO, f"platform/operations/agent/{p.name}"): p.read_text() for p in docs.glob("*.md")}
    world.cluster.add("operations-agent-staging", "operations-agent-api", FakeDeployment(replicas=1, ready=1))
    ctx = load(world, "operations-agent", ROOT / "platform/catalog/operations-agent/app.yaml", ROOT / "platform/environments/staging/operations-agent.yaml", rb)
    assert ctx.runbook is not None and ctx.runbook.value.startswith("<!-- Datei: README.md -->")
    assert len(ctx.runbook.value) <= 20_000 and ctx.errors == []
    assert ctx.deployment == "operations-agent-api" and ctx.actual is not None
    assert ctx.allowed_actions == frozenset()
    assert desired_digest(ctx.desired.value) == "sha256:" + "0" * 64
    s = smoke(world)
    s.acr = "mmkacr.azurecr.io"
    assert s.resolve_image(ctx.smoke_test["image"]).startswith("mmkacr.azurecr.io/platform/smoke-http@sha256:")


def test_app_deploy_dispatch_interface() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/app-deploy.yml").read_text())
    inputs = wf[True]["workflow_dispatch"]["inputs"]  # YAML liest den Schlüssel "on" als True
    assert {"appId", "environment", "operationId", "actionId", "force"} <= set(inputs)
    assert "action=${{ inputs.actionId" in wf["run-name"] and "op=${{ inputs.operationId" in wf["run-name"]
    title = "app-deploy affine/staging op=op-1 action=act-0001"
    assert mentions(title, "act-0001") and mentions(title, "op-1")


def test_agent_build_calls_desired_state_workflow_correctly() -> None:
    callee = yaml.safe_load((ROOT / ".github/workflows/app-update-desired-state.yml").read_text())[True]["workflow_call"]
    caller = yaml.safe_load((ROOT / ".github/workflows/agent-build.yml").read_text())["jobs"]["desired-state"]
    given = set(caller["with"])
    required = {k for k, v in callee["inputs"].items() if v.get("required")}
    assert required <= given <= set(callee["inputs"])
    assert caller["with"]["appId"] == "operations-agent" and caller["secrets"] == "inherit"


def test_real_affine_delivery_is_understood(world: World) -> None:
    rb = {("mmkdigitalio/hackathon-platform-affine", "runbook/README.md"): "# AFFiNE Runbook"}
    ctx = load(world, "affine", ROOT / "platform/catalog/affine/app.yaml", ROOT / "platform/environments/staging/affine.yaml", rb)
    assert ctx.allowed_actions == {"restart", "redeploy"} and ctx.deployment == "affine" and ctx.check_path == "/info"
    assert desired_replicas(ctx.desired.value) == 1
    assert desired_digest(ctx.desired.value).startswith("sha256:b649f5ce")
    assert ctx.runbook is not None and ctx.errors == []
    image = ctx.smoke_test["image"]
    assert smoke(world).resolve_image(image) == image and "@sha256:" in image
    fault = yaml.safe_load((ROOT / "platform/operations/apps/affine/fault-invalid-config.yaml").read_text())
    assert fault["path"] == ["affine", "nodeOptions"]  # Fehlerwert liegt in values, nicht in Replicas/Digest -> in_sync
