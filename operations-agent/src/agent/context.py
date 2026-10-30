"""Versionsbezogener Kontext: Katalog, Sollzustand, Runbook, tatsächlicher Release, Alarm, frühere Aktionen.

Soll (Git zu ``git_ref``) und Ist (Cluster) werden getrennt geladen und getrennt ausgewiesen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from contracts import ActionRecord, AgentInvocation, KubernetesState, ReleaseRef
from tools.alert import summarize_alert
from tools.common import NotFound, ToolError, github_ref
from tools.github import GitHubClient
from tools.kubernetes import KubernetesAdapter
from tools.targets import Target

CATALOG_DIR = "platform/catalog"
RUNBOOK_MAX_CHARS = 20_000

DriftKind = Literal["scaled_to_zero", "image_drift", "in_sync", "unknown"]


@dataclass
class Sourced:
    value: Any
    source: str


@dataclass
class OperationalContext:
    target: Target
    git_ref: str
    catalog: Sourced
    desired: Sourced
    desired_path: str
    runbook: Sourced | None
    actual: KubernetesState | None
    actual_release: ReleaseRef | None
    alert: dict[str, Any] | None
    previous_actions: list[ActionRecord]
    errors: list[str] = field(default_factory=list)
    resolved_deployment: str | None = None

    @property
    def allowed_actions(self) -> frozenset[str]:
        return frozenset(self.catalog.value.get("allowedActions") or [])

    @property
    def deployment(self) -> str:
        return self.resolved_deployment or deployment_name(self.catalog.value, self.target.app_id)

    @property
    def check_path(self) -> str:
        return (self.catalog.value.get("availability") or {}).get("path", "/")

    @property
    def smoke_test(self) -> dict[str, Any]:
        return self.catalog.value.get("smokeTest") or {}


def deployment_name(catalog: dict[str, Any], app_id: str) -> str:
    ingress = catalog.get("ingress") or {}
    return catalog.get("deployment") or ingress.get("service") or app_id


def desired_replicas(desired: dict[str, Any]) -> int | None:
    for path in (("sizes", "replicas"), ("replicas",), ("values", "replicaCount"), ("values", "replicas")):
        node: Any = desired
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, int):
            return node
    return None


def desired_digest(desired: dict[str, Any]) -> str | None:
    image = desired.get("image")
    value = image.get("digest") if isinstance(image, dict) else desired.get("imageDigest") or image
    if isinstance(value, str) and "sha256:" in value:
        return "sha256:" + value.split("sha256:", 1)[1]
    return None


def assess_drift(ctx: OperationalContext) -> tuple[DriftKind, str]:
    """Deterministischer Soll-/Istvergleich; entscheidet, ob ein erneutes Deployment reparieren kann."""
    if ctx.actual is None:
        return "unknown", "Istzustand nicht lesbar"
    want = desired_replicas(ctx.desired.value)
    if ctx.actual.desired_replicas == 0 and (want is None or want > 0):
        return "scaled_to_zero", f"Deployment hat 0 Replicas, Sollzustand {want if want is not None else 'mindestens 1 (Chart-Standard)'}"
    if want is not None and ctx.actual.desired_replicas != want:
        return "scaled_to_zero" if ctx.actual.desired_replicas < want else "unknown", (
            f"Replicas Ist {ctx.actual.desired_replicas}, Soll {want}"
        )
    digest = desired_digest(ctx.desired.value)
    if digest and ctx.actual.image_digests and digest not in ctx.actual.image_digests:
        return "image_drift", f"laufender Digest {ctx.actual.image_digests} statt {digest}"
    return "in_sync", "Istzustand entspricht dem Sollzustand"


class ContextLoader:
    def __init__(self, github: GitHubClient, k8s: KubernetesAdapter | None) -> None:
        self.gh = github
        self.k8s = k8s

    def catalog_app_ids(self, git_ref: str) -> set[str]:
        return set(self.gh.list_dir(CATALOG_DIR, git_ref))

    def load(self, inv: AgentInvocation, target: Target, previous_actions: list[ActionRecord]) -> OperationalContext:
        ref = inv.context.git_ref
        repo = self.gh.repository
        catalog_path = f"{CATALOG_DIR}/{target.app_id}/app.yaml"
        desired_path = f"platform/environments/{target.environment}/{target.app_id}.yaml"
        cat = self.gh.get_file(catalog_path, ref)
        des = self.gh.get_file(desired_path, ref)
        catalog = Sourced(yaml.safe_load(cat["content"]) or {}, github_ref(repo, "blob", f"{ref}/{catalog_path}"))
        desired = Sourced(yaml.safe_load(des["content"]) or {}, github_ref(repo, "blob", f"{ref}/{desired_path}"))
        errors: list[str] = []

        runbook = None
        rb = catalog.value.get("runbook") or {}
        if rb.get("repository") and rb.get("path") and rb.get("commit"):
            try:
                f = self.gh.get_document(rb["path"], rb["commit"], repository=rb["repository"], max_chars=RUNBOOK_MAX_CHARS)
                runbook = Sourced(f["content"][:RUNBOOK_MAX_CHARS], github_ref(rb["repository"], "blob", f"{rb['commit']}/{rb['path']}"))
            except ToolError as e:
                errors.append(f"Runbook nicht lesbar: {e}")
        else:
            errors.append("Katalogeintrag ohne vollständige Runbook-Referenz (repository, path, commit)")

        actual = actual_release = resolved = None
        if self.k8s is not None:
            try:
                resolved = self._resolve_deployment(target, deployment_name(catalog.value, target.app_id))
                actual, _, _ = self.k8s.workload_state(target, resolved)
                actual_release = self.k8s.namespace_release(target)
            except ToolError as e:
                errors.append(f"Istzustand nicht lesbar: {e.kind}: {e}")

        return OperationalContext(
            target=target,
            git_ref=ref,
            catalog=catalog,
            desired=desired,
            desired_path=desired_path,
            runbook=runbook,
            actual=actual,
            actual_release=actual_release,
            alert=summarize_alert(inv.context.alert_payload),
            previous_actions=previous_actions,
            errors=errors,
            resolved_deployment=resolved,
        )

    def _resolve_deployment(self, target: Target, preferred: str) -> str:
        """Bevorzugter Name aus dem Katalog, sonst über das Label ``app.kubernetes.io/instance`` (B's Konvention)."""
        names = self.k8s.app_deployments(target)
        if preferred in names or not names:
            return preferred
        if target.app_id in names:
            return target.app_id
        if len(names) == 1:
            return names[0]
        raise NotFound(f"kein eindeutiges Haupt-Deployment für {target}", candidates=names)
