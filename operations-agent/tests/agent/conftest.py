from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from agent import Dependencies, execute
from agent.config import AgentSettings, ModelSettings
from contracts import AgentInvocation, AgentResult, ContextRefs, OperationsServices, ResumeInfo, RunLimits
from contracts import Ownership as Lease
from tools.http_check import HttpProbe
from tools.kubernetes import KubernetesAdapter
from tools.targets import TargetPolicy

from .fake_github import REPO, FakeGitHub
from .fakes import (
    FakeClock,
    FakeCluster,
    FakeDeployment,
    MemoryActions,
    MemoryLock,
    NoCheckpoints,
    Ownership,
    RecordingState,
    ScriptedModel,
)

RULES = Path(__file__).resolve().parents[3] / "platform" / "operations" / "agent" / "betriebsregeln.md"
SMOKE_IMAGE = "acr.example/smoke-affine@sha256:" + "b" * 64
DIGEST = "sha256:" + "a" * 64

CATALOG = f"""
schemaVersion: "0.1"
appId: affine
ingress: {{ service: affine, port: 3010 }}
availability: {{ path: /info }}
smokeTest: {{ image: "{SMOKE_IMAGE}" }}
runbook: {{ repository: mmkdigitalio/hackathon-platform-affine, path: docs/runbook.md, commit: 1234567 }}
allowedActions: [restart, redeploy]
"""
DESIRED = f"""
image: {{ digest: "{DIGEST}" }}
chartVersion: 1.0.0
sizes: {{ replicas: 1 }}
values: {{ server: {{ externalUrl: https://affine.example }} }}
"""


@dataclass
class World:
    cluster: FakeCluster
    github: FakeGitHub
    actions: MemoryActions
    lock: MemoryLock
    state: RecordingState
    ownership: Ownership
    clock: FakeClock
    http_status: int = 200

    @property
    def services(self) -> OperationsServices:
        return OperationsServices(self.actions, self.lock, self.state, self.ownership, NoCheckpoints())

    def policy(self) -> TargetPolicy:
        return TargetPolicy({"affine", "operations-agent"}, "1-2-3-4.sslip.io")

    def k8s(self, policy: TargetPolicy | None = None) -> KubernetesAdapter:
        c = self.cluster
        return KubernetesAdapter(policy or self.policy(), c.apps, c.core, c.batch)

    def probe(self) -> HttpProbe:
        transport = httpx.MockTransport(lambda r: httpx.Response(self.http_status))
        return HttpProbe(self.policy(), http=httpx.Client(transport=transport))

    def invocation(self, **kw) -> AgentInvocation:
        base = dict(
            job_id="job-1",
            incident_id="inc-1",
            execution_id="exec-1",
            app_id="affine",
            environment="staging",
            operation_id="op-1",
            ownership=Lease(worker_id="w1", lease_version=1),
            limits=RunLimits(),
            context=ContextRefs(git_ref="main", alert_payload=ALERT),
            resume=ResumeInfo(thread_id="job-1"),
        )
        base.update(kw)
        return AgentInvocation(**base)

    def run(self, model: ScriptedModel, inv: AgentInvocation | None = None, **settings_kw) -> AgentResult:
        return execute(
            inv or self.invocation(),
            self.services,
            settings=settings(**settings_kw),
            model=MODEL,
            deps=Dependencies(
                github=self.github.client(),
                kubernetes=lambda policy: self.k8s(policy),
                http=self.probe(),
                chat_model=model,
                sleep=self.clock.sleep,
                clock=self.clock,
            ),
        )


ALERT = {
    "schemaId": "azureMonitorCommonAlertSchema",
    "data": {
        "essentials": {
            "alertId": "/subscriptions/x/alerts/1",
            "alertRule": "alert-affine-staging-availability",
            "severity": "Sev1",
            "monitorCondition": "Fired",
            "firedDateTime": "2026-10-30T09:58:00Z",
        },
        "alertContext": {"condition": {"allOf": [{"metricName": "availabilityResults/availabilityPercentage", "operator": "LessThan", "threshold": "100", "metricValue": 0, "webTestName": "affine-staging"}]}},
        "customProperties": {"app-id": "affine", "environment": "staging"},
    },
}

MODEL = ModelSettings(provider="anthropic", model="scripted", api_key=SecretStr("sk-test-not-real"), price_input_per_mtok=3.0, price_output_per_mtok=15.0)


def settings(**kw) -> AgentSettings:
    base = dict(
        github_repository=REPO,
        github_token=SecretStr("test-token"),
        base_domain="1-2-3-4.sslip.io",
        dispatch_workflows=frozenset({"app-deploy.yml"}),
        dispatch_ref="main",
        image_pull_secret="acr-pull",
        http_scheme="https",
        rules_file=RULES,
        post_issue_comment=True,
        smoke_timeout_seconds=120,
    )
    base.update(kw)
    return AgentSettings(**base)


@pytest.fixture
def world() -> World:
    cluster = FakeCluster()
    cluster.add("affine-staging", "affine", FakeDeployment(replicas=1, ready=1, digest=DIGEST))
    gh = FakeGitHub()
    gh.catalog_apps = ["affine", "operations-agent"]
    gh.files[(REPO, "platform/catalog/affine/app.yaml")] = CATALOG
    gh.files[(REPO, "platform/environments/staging/affine.yaml")] = DESIRED
    gh.files[("mmkdigitalio/hackathon-platform-affine", "docs/runbook.md")] = "# AFFiNE Runbook\n0 Replicas: Sollzustand per app-deploy.yml wiederherstellen.\n"
    gh.commits["platform/environments/staging/affine.yaml"] = {
        "sha": "c0ffee1234567890",
        "login": "kollegin",
        "name": "Kollegin",
        "date": "2026-10-30T08:00:00Z",
        "message": "affine: externalUrl setzen",
        "path": "platform/environments/staging/affine.yaml",
        "patch": "-  externalUrl: https://affine.example\n+  externalUrl: not-a-url",
    }
    return World(cluster, gh, MemoryActions(), MemoryLock(), RecordingState(), Ownership(), FakeClock())
