"""Ende-zu-Ende im lokalen kind-Cluster: echtes Modell, echte Kubernetes-API mit ServiceAccount-Token,
echter Smoke-Test-Job. Nur GitHub ist simuliert; dessen Deploy-Workflow setzt die Replicas zurück.

Aufruf aus ``operations-agent/`` nach ``tests/agent/kind/setup.sh``:
    PYTHONPATH=. uv run python tests/agent/kind/e2e.py repair|unrepairable
Modell über ``OPS_MODEL_*`` und ``AZURE_OPENAI_API_KEY`` (siehe platform/operations/agent/README.md).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from kubernetes import client, config

from agent import Dependencies, execute
from agent.config import model_settings
from contracts import AgentInvocation, ContextRefs, OperationsServices, Ownership, ResumeInfo, RunLimits
from tests.agent.conftest import ALERT, settings
from tests.agent.fake_github import REPO, FakeGitHub
from tests.agent.fakes import MemoryActions, MemoryLock, NoCheckpoints, RecordingState
from tests.agent.fakes import Ownership as Lease

KIND = Path(os.environ.get("KIND_DIR", Path(__file__).resolve().parents[3] / ".kind"))
NS, DEP = "affine-staging", "affine"


def admin() -> tuple[client.AppsV1Api, client.CoreV1Api]:
    api = config.new_client_from_config(config_file=str(KIND / "admin.kubeconfig"))
    return client.AppsV1Api(api), client.CoreV1Api(api)


def wait(pred, seconds: int = 180) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return
        time.sleep(3)
    raise TimeoutError("Zustand nicht erreicht")


def nginx_digest(core: client.CoreV1Api) -> str:
    pods = core.list_namespaced_pod(NS, label_selector="app=affine").items
    return "sha256:" + pods[0].status.container_statuses[0].image_id.split("sha256:", 1)[1]


def main(scenario: str) -> int:
    apps, core = admin()
    wait(lambda: (apps.read_namespaced_deployment(DEP, NS).status.ready_replicas or 0) >= 1)
    digest = nginx_digest(core)
    smoke_image = (KIND / "smoke-image").read_text().strip()

    gh = FakeGitHub()
    gh.catalog_apps = ["affine", "operations-agent"]
    gh.files[(REPO, "platform/catalog/affine/app.yaml")] = (
        f'schemaVersion: "0.1"\nappId: affine\ningress: {{ service: affine, port: 80 }}\navailability: {{ path: / }}\n'
        f'smokeTest: {{ image: "{smoke_image}" }}\n'
        "runbook: { repository: mmkdigitalio/hackathon-platform-affine, path: docs/runbook.md, commit: 1234567 }\n"
        "allowedActions: [restart, redeploy]\n"
    )
    gh.files[(REPO, "platform/environments/staging/affine.yaml")] = f'image: {{ digest: "{digest}" }}\nchartVersion: 0.0.0-local\nsizes: {{ replicas: 1 }}\n'
    gh.files[("mmkdigitalio/hackathon-platform-affine", "docs/runbook.md")] = (
        "# AFFiNE Runbook\n- 0 Replicas: Sollzustand per app-deploy.yml wiederherstellen.\n"
        "- CrashLoop nach Konfigurationsänderung: nicht neu deployen, Sollzustand-Commit prüfen.\n"
    )
    gh.commits["platform/environments/staging/affine.yaml"] = {
        "sha": "c0ffee1234567890", "login": "kollegin", "name": "Kollegin", "date": "2026-10-30T08:00:00Z",
        "message": "affine: Konfigurationsdatei umstellen", "path": "platform/environments/staging/affine.yaml",
        "patch": "-  configFile: /etc/nginx/nginx.conf\n+  configFile: /etc/nginx/kaputt.conf",
    }

    def deploy_workflow(_: dict) -> None:  # simuliert app-deploy.yml: Sollzustand ausrollen
        apps.patch_namespaced_deployment_scale(DEP, NS, {"spec": {"replicas": 1}})

    gh.on_dispatch = deploy_workflow
    original = apps.read_namespaced_deployment(DEP, NS).spec.template.spec.containers[0].command
    if scenario == "repair":
        apps.patch_namespaced_deployment_scale(DEP, NS, {"spec": {"replicas": 0}})
        wait(lambda: not core.list_namespaced_pod(NS, label_selector="app=affine").items)
    else:
        apps.patch_namespaced_deployment(DEP, NS, {"spec": {"template": {"spec": {"containers": [
            {"name": "affine", "command": ["nginx", "-g", "daemon off;", "-c", "/etc/nginx/kaputt.conf"]}]}}}})
        wait(lambda: any(
            (cs.state.waiting and cs.state.waiting.reason in ("CrashLoopBackOff", "Error")) or (cs.restart_count or 0) > 0
            for p in core.list_namespaced_pod(NS, label_selector="app=affine").items for cs in (p.status.container_statuses or [])
        ))

    os.environ["KUBECONFIG"] = str(KIND / "sa.kubeconfig")
    os.environ["OPS_KUBE_CONTEXT"] = "kind-ops-agent-sa"
    actions = MemoryActions()
    services = OperationsServices(actions, MemoryLock(), RecordingState(), Lease(), NoCheckpoints())
    inv = AgentInvocation(
        job_id=f"kind-{scenario}", incident_id="inc-kind", execution_id="exec-kind", app_id="affine", environment="staging",
        operation_id=f"op-kind-{scenario}", ownership=Ownership(worker_id="local", lease_version=1),
        limits=RunLimits(), context=ContextRefs(git_ref="main", alert_payload=ALERT), resume=ResumeInfo(thread_id=f"kind-{scenario}"),
    )
    try:
        result = execute(
            inv, services,
            settings=settings(base_domain="affine-staging.svc.cluster.local", http_scheme="http", image_pull_secret=None, smoke_timeout_seconds=120),
            model=model_settings(),
            deps=Dependencies(github=gh.client(), http=None),
        )
    finally:
        if scenario != "repair":
            apps.patch_namespaced_deployment(DEP, NS, {"spec": {"template": {"spec": {"containers": [{"name": "affine", "command": original}]}}}})
        apps.patch_namespaced_deployment_scale(DEP, NS, {"spec": {"replicas": 1}})

    print(json.dumps({
        "scenario": scenario,
        "proposal": result.proposal,
        "reason": result.reason,
        "limitReached": result.limit_reached,
        "toolCalls": [f"{t.name}:{t.status}{':' + t.error_kind if t.error_kind else ''}" for t in result.tool_calls],
        "dispatches": len(gh.dispatches),
        "actions": [f"{a.kind}:{a.status}" for a in actions.items.values()],
        "verification": [
            {"success": v.success, "exitCode": v.exit_code, "job": v.job_name, "result": v.result,
             "readyReplicas": v.kubernetes.ready_replicas if v.kubernetes else None, "digests": v.kubernetes.image_digests if v.kubernetes else None}
            for v in result.verification
        ],
        "requiredChange": result.required_change,
        "logLines": result.references.get("logLines"),
        "modelFindings": [f"{f.certainty}: {f.statement}" for f in result.findings if f.provenance == "model"],
        "usage": result.usage.model_dump(),
    }, ensure_ascii=False, indent=1))
    expected = "resolved" if scenario == "repair" else "needs_attention"
    return 0 if result.proposal == expected else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
