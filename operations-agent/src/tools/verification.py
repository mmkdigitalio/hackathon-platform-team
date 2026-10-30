"""Unabhängige Verifikation: Smoke-Test-Job mit ``TARGET_URL`` plus aktueller Kubernetes-Zustand."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from kubernetes import client

from contracts import OperationsServices, ReleaseRef, VerificationEvidence
from tools.common import k8s_ref, utcnow
from tools.kubernetes import ACTION_ANNOTATION, KubernetesAdapter
from tools.targets import Target, TargetPolicy


def job_name(action_id: str) -> str:
    return ("smoke-" + re.sub(r"[^a-z0-9-]", "-", action_id.lower()))[:63].rstrip("-")


def parse_result(stdout: str) -> dict[str, Any] | None:
    """Genau ein JSON-Objekt auf stdout; gelesen wird die letzte JSON-Zeile."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


class SmokeTest:
    def __init__(
        self,
        k8s: KubernetesAdapter,
        policy: TargetPolicy,
        services: OperationsServices,
        image_pull_secret: str | None,
        scheme: str = "https",
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 5.0,
        acr_login_server: str | None = None,
    ) -> None:
        self.k8s = k8s
        self.policy = policy
        self.s = services
        self.pull_secret = image_pull_secret
        self.scheme = scheme
        self.sleep = sleep
        self.clock = clock
        self.poll = poll_seconds
        self.acr = acr_login_server

    def resolve_image(self, image: str) -> str:
        """``acr:<repo>@sha256:…`` aus dem Katalog gegen den ACR-Login-Server der Foundation auflösen."""
        if not image.startswith("acr:"):
            return image
        if not self.acr:
            raise ValueError("Smoke-Test-Image mit acr: braucht OPS_ACR_LOGIN_SERVER (Foundation-Ausgabe acrLoginServer)")
        return f"{self.acr}/{image[len('acr:') :]}"

    def run(
        self,
        t: Target,
        deployment: str,
        image: str,
        secret_env: dict[str, str],
        operation_id: str,
        timeout_seconds: int,
        static_env: dict[str, str] | None = None,
    ) -> VerificationEvidence:
        if "@sha256:" not in image:
            raise ValueError("Smoke-Test-Image muss per Digest referenziert sein")
        image = self.resolve_image(image)
        action = self.s.actions.record_planned(operation_id, "smoke_test", f"{t.namespace}/job", {"image": image})
        name = job_name(action.action_id)
        target_url = f"{self.scheme}://{self.policy.host(t.app_id)}"
        started = utcnow()
        self.k8s.create_job(
            t, self._job(t, name, image, target_url, secret_env, static_env or {}, action.action_id, operation_id, timeout_seconds)
        )
        self.s.actions.update(action.action_id, "started", {"job": name})
        deadline = self.clock() + timeout_seconds
        exit_code: int | None = None
        stdout = ""
        timed_out = False
        while True:
            job = self.k8s.read_job(t, name)
            st = job.status
            if (st.succeeded or 0) > 0 or (st.failed or 0) > 0:
                # activeDeadlineSeconds beendet den Job meist vor unserer eigenen Frist
                timed_out = any(c.type == "Failed" and c.reason == "DeadlineExceeded" for c in st.conditions or [])
                pod = self.k8s.job_pod(t, name)
                if pod is not None:
                    stdout = self.k8s.pod_log(t, pod.metadata.name)
                    for cs in pod.status.container_statuses or []:
                        if cs.state and cs.state.terminated:
                            exit_code = cs.state.terminated.exit_code
                break
            if self.clock() + self.poll > deadline:
                timed_out = True
                self.k8s.delete_job(t, name)
                break
            self.sleep(self.poll)
        finished = utcnow()
        result = parse_result(stdout)
        success = (not timed_out) and exit_code == 0 and bool(result) and result.get("success") is True
        state, _, refs = self.k8s.workload_state(t, deployment)
        ns_release = self.k8s.namespace_release(t)
        release = ReleaseRef(
            git_sha=ns_release.git_sha,
            image_digest=state.image_digests[0] if len(state.image_digests) == 1 else ns_release.image_digest,
            chart_version=ns_release.chart_version,
        )
        self.s.actions.update(
            action.action_id,
            "succeeded" if success else "failed",
            {"job": name, "exitCode": str(exit_code), "timedOut": str(timed_out).lower()},
        )
        return VerificationEvidence(
            app_id=t.app_id,
            environment=t.environment,
            release=release,
            check_ref=image.split("@", 1)[1],
            started_at=started,
            finished_at=None if timed_out else finished,
            success=success,
            timed_out=timed_out,
            exit_code=exit_code,
            job_name=name,
            action_id=action.action_id,
            result=result,
            kubernetes=state,
            evidence_refs=[k8s_ref(t.namespace, "job", name, finished), *refs],
        )

    def _job(
        self,
        t: Target,
        name: str,
        image: str,
        target_url: str,
        secret_env: dict[str, str],
        static_env: dict[str, str],
        action_id: str,
        operation_id: str,
        timeout_seconds: int,
    ) -> client.V1Job:
        # Wie app-deploy.yml: TARGET_URL, nicht geheime smokeTest.env, Test-Secrets per secretKeyRef
        env = [client.V1EnvVar(name="TARGET_URL", value=target_url)]
        env += [client.V1EnvVar(name=k, value=str(v)) for k, v in sorted(static_env.items()) if k != "TARGET_URL"]
        env += [
            client.V1EnvVar(
                name=var,
                value_from=client.V1EnvVarSource(secret_key_ref=client.V1SecretKeySelector(name=f"{t.app_id}-platform", key=var)),
            )
            for var in sorted(secret_env)
        ]
        labels = {"app.kubernetes.io/managed-by": "operations-agent", "ops.mmk/purpose": "smoke-test"}
        annotations = {ACTION_ANNOTATION: action_id, "ops.mmk/operation-id": operation_id}
        return client.V1Job(
            metadata=client.V1ObjectMeta(name=name, namespace=t.namespace, labels=labels, annotations=annotations),
            spec=client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=timeout_seconds,
                ttl_seconds_after_finished=3600,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels, annotations=annotations),
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        image_pull_secrets=[client.V1LocalObjectReference(name=self.pull_secret)] if self.pull_secret else None,
                        containers=[client.V1Container(name="smoke", image=image, env=env)],
                    ),
                ),
            ),
        )
