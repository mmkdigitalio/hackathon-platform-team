"""Kubernetes-Adapter über den ServiceAccount ``operations-agent``."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import MaxRetryError, ProtocolError
from urllib3.exceptions import TimeoutError as Urllib3Timeout

from contracts import KubernetesState, ReleaseRef
from tools.common import (
    Conflict,
    NotFound,
    PermissionDenied,
    TimedOut,
    UpstreamError,
    ValidationFailed,
    k8s_ref,
    redact,
    utcnow,
)
from tools.targets import Target, TargetPolicy

ACTION_ANNOTATION = "ops.mmk/action-id"
# Release-Referenz des letzten Rollouts am Namespace (B, app-catalog „Soll- und Iststand unterscheidbar“).
RELEASE_ANNOTATIONS = {
    "git_sha": "ops.mmk/release-git-sha",
    "image_digest": "ops.mmk/release-image-digest",
    "chart_version": "ops.mmk/release-chart-version",
}
MAX_LOG_LINES = 200
MAX_LOG_BYTES = 16_000

T = TypeVar("T")


def _call(what: str, fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ApiException as e:
        if e.status in (401, 403):
            raise PermissionDenied(f"Kubernetes verweigert {what}", status=e.status, reason=e.reason) from e
        if e.status == 404:
            raise NotFound(f"{what}: Ressource nicht gefunden", status=e.status) from e
        if e.status == 409:
            raise Conflict(f"{what}: Konflikt", status=e.status) from e
        raise UpstreamError(f"{what}: Kubernetes-Fehler {e.status}", status=e.status, reason=e.reason) from e
    except (Urllib3Timeout, TimeoutError) as e:
        raise TimedOut(f"{what}: Zeitüberschreitung") from e
    except (MaxRetryError, ProtocolError) as e:
        if isinstance(getattr(e, "reason", None), Urllib3Timeout):
            raise TimedOut(f"{what}: Zeitüberschreitung") from e
        raise UpstreamError(f"{what}: Verbindung fehlgeschlagen") from e


class KubernetesAdapter:
    def __init__(
        self,
        policy: TargetPolicy,
        apps: client.AppsV1Api,
        core: client.CoreV1Api,
        batch: client.BatchV1Api,
        request_timeout: float = 10.0,
    ) -> None:
        self.policy = policy
        self.apps = apps
        self.core = core
        self.batch = batch
        self.timeout = request_timeout

    @classmethod
    def from_environment(cls, policy: TargetPolicy) -> KubernetesAdapter:
        """Im Cluster den ServiceAccount-Token, lokal nur einen ausdrücklich benannten Kontext."""
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
        else:
            context = os.environ.get("OPS_KUBE_CONTEXT")
            if not context:
                raise ValidationFailed("OPS_KUBE_CONTEXT muss lokal gesetzt sein; der Standardkontext wird nie verwendet")
            # Pfad erst hier lesen: der Client wertet KUBECONFIG sonst nur beim Import aus.
            config_file = os.environ.get("OPS_KUBECONFIG") or os.environ.get("KUBECONFIG") or None
            config.load_kube_config(config_file=config_file, context=context)
        return cls(policy, client.AppsV1Api(), client.CoreV1Api(), client.BatchV1Api())

    # Lesen

    def _deployment(self, t: Target, name: str) -> Any:
        return _call(
            f"Deployment {t.namespace}/{name} lesen",
            lambda: self.apps.read_namespaced_deployment(name, t.namespace, _request_timeout=self.timeout),
        )

    def _pods(self, t: Target, deployment: Any) -> list[Any]:
        labels = deployment.spec.selector.match_labels or {}
        selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        pods = _call(
            f"Pods in {t.namespace} lesen",
            lambda: self.core.list_namespaced_pod(t.namespace, label_selector=selector, _request_timeout=self.timeout),
        )
        return list(pods.items)

    def workload_state(self, t: Target, deployment: str) -> tuple[KubernetesState, dict[str, Any], list[str]]:
        dep = self._deployment(t, deployment)
        pods = self._pods(t, dep)
        at = utcnow()
        spec_replicas = dep.spec.replicas or 0
        status = dep.status
        digests: set[str] = set()
        restarts: dict[str, int] = {}
        pod_view = []
        for p in pods:
            statuses = p.status.container_statuses or []
            restarts[p.metadata.name] = sum(c.restart_count or 0 for c in statuses)
            for c in statuses:
                if c.image_id and "sha256:" in c.image_id:
                    digests.add("sha256:" + c.image_id.split("sha256:", 1)[1])
            waiting = [c.state.waiting.reason for c in statuses if c.state and c.state.waiting]
            pod_view.append({"name": p.metadata.name, "phase": p.status.phase, "waiting": waiting, "restarts": restarts[p.metadata.name]})
        rollout_complete = (
            (status.observed_generation or 0) >= (dep.metadata.generation or 0)
            and (status.updated_replicas or 0) == spec_replicas
            and (status.available_replicas or 0) == spec_replicas
        )
        state = KubernetesState(
            namespace=t.namespace,
            deployment=deployment,
            desired_replicas=spec_replicas,
            ready_replicas=status.ready_replicas or 0,
            image_digests=sorted(digests),
            restart_counts=restarts,
            rollout_complete=rollout_complete,
            captured_at=at,
        )
        template_annotations = (dep.spec.template.metadata.annotations or {}) if dep.spec.template.metadata else {}
        extra = {
            "pods": pod_view,
            "conditions": [{"type": c.type, "status": c.status, "reason": c.reason} for c in (status.conditions or [])],
            "templateActionId": template_annotations.get(ACTION_ANNOTATION),
            "generation": dep.metadata.generation,
        }
        return state, extra, [k8s_ref(t.namespace, "deployment", deployment, at)]

    def events(self, t: Target, limit: int = 30) -> tuple[list[dict[str, Any]], list[str]]:
        evs = _call(
            f"Events in {t.namespace} lesen",
            lambda: self.core.list_namespaced_event(t.namespace, _request_timeout=self.timeout),
        )
        at = utcnow()
        items = sorted(
            evs.items,
            key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp or at,
        )[-limit:]
        out = [
            {
                "time": (e.last_timestamp or e.event_time or e.metadata.creation_timestamp),
                "type": e.type,
                "reason": e.reason,
                "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "message": (e.message or "")[:500],
                "count": e.count,
            }
            for e in items
        ]
        return out, [k8s_ref(t.namespace, "events", "*", at)]

    def logs(self, t: Target, deployment: str, tail_lines: int = 100, since_seconds: int = 3600) -> tuple[list[dict[str, Any]], list[str]]:
        tail_lines = max(1, min(tail_lines, MAX_LOG_LINES))
        dep = self._deployment(t, deployment)
        pods = self._pods(t, dep)
        at = utcnow()
        out, refs = [], []
        budget = MAX_LOG_BYTES
        for p in pods:
            text = _call(
                f"Logs von {t.namespace}/{p.metadata.name} lesen",
                lambda p=p: self.core.read_namespaced_pod_log(
                    p.metadata.name,
                    t.namespace,
                    tail_lines=tail_lines,
                    since_seconds=since_seconds,
                    timestamps=True,
                    _request_timeout=self.timeout,
                ),
            )
            text = redact(text or "")[-budget:]
            budget -= len(text)
            out.append({"pod": p.metadata.name, "lines": text.splitlines(), "sinceSeconds": since_seconds, "until": at})
            refs.append(k8s_ref(t.namespace, "pod-log", p.metadata.name, at))
            if budget <= 0:
                break
        return out, refs

    def app_deployments(self, t: Target) -> list[str]:
        deps = _call(
            f"Deployments in {t.namespace} lesen",
            lambda: self.apps.list_namespaced_deployment(
                t.namespace, label_selector=f"app.kubernetes.io/instance={t.app_id}", _request_timeout=self.timeout
            ),
        )
        return sorted(d.metadata.name for d in deps.items)

    def namespace_release(self, t: Target) -> ReleaseRef:
        ns = _call(f"Namespace {t.namespace} lesen", lambda: self.core.read_namespace(t.namespace, _request_timeout=self.timeout))
        ann = ns.metadata.annotations or {}
        return ReleaseRef(**{k: ann.get(v) for k, v in RELEASE_ANNOTATIONS.items()})

    def template_action_id(self, t: Target, deployment: str) -> str | None:
        dep = self._deployment(t, deployment)
        meta = dep.spec.template.metadata
        return (meta.annotations or {}).get(ACTION_ANNOTATION) if meta else None

    # Ändern

    def patch_restart(self, t: Target, deployment: str, action_id: str) -> int:
        body = {"spec": {"template": {"metadata": {"annotations": {ACTION_ANNOTATION: action_id}}}}}
        dep = _call(
            f"Deployment {t.namespace}/{deployment} patchen",
            lambda: self.apps.patch_namespaced_deployment(deployment, t.namespace, body, _request_timeout=self.timeout),
        )
        return dep.metadata.generation

    def create_job(self, t: Target, body: client.V1Job) -> None:
        _call(
            f"Job in {t.namespace} anlegen",
            lambda: self.batch.create_namespaced_job(t.namespace, body, _request_timeout=self.timeout),
        )

    def read_job(self, t: Target, name: str) -> Any:
        return _call(f"Job {t.namespace}/{name} lesen", lambda: self.batch.read_namespaced_job(name, t.namespace, _request_timeout=self.timeout))

    def job_pod(self, t: Target, job_name: str) -> Any | None:
        pods = _call(
            f"Pods des Jobs {job_name} lesen",
            lambda: self.core.list_namespaced_pod(t.namespace, label_selector=f"job-name={job_name}", _request_timeout=self.timeout),
        )
        return pods.items[-1] if pods.items else None

    def pod_log(self, t: Target, pod: str) -> str:
        return _call(
            f"Logs von {t.namespace}/{pod} lesen",
            lambda: self.core.read_namespaced_pod_log(pod, t.namespace, _request_timeout=self.timeout),
        ) or ""

    def delete_job(self, t: Target, name: str) -> None:
        try:
            _call(
                f"Job {t.namespace}/{name} löschen",
                lambda: self.batch.delete_namespaced_job(
                    name, t.namespace, propagation_policy="Background", _request_timeout=self.timeout
                ),
            )
        except NotFound:
            pass
