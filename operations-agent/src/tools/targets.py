"""Zielbeschränkung auf Katalog-Apps: Namespace ``<app-id>-<env>`` und Host ``<app-id>.<baseDomain>``."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from tools.common import NotAllowed, ValidationFailed

ENVIRONMENTS = frozenset({"staging"})


@dataclass(frozen=True)
class Target:
    app_id: str
    environment: str

    @property
    def namespace(self) -> str:
        return f"{self.app_id}-{self.environment}"

    def __str__(self) -> str:
        return f"{self.app_id}/{self.environment}"


class TargetPolicy:
    """Prüft Ziele im Code, bevor ein externer Request entsteht (ClusterRoleBinding gilt clusterweit)."""

    def __init__(self, catalog_app_ids: set[str], base_domain: str, environments: frozenset[str] = ENVIRONMENTS) -> None:
        self._apps = frozenset(catalog_app_ids)
        self._envs = environments
        self.base_domain = base_domain.strip(".").lower()

    @property
    def namespaces(self) -> frozenset[str]:
        return frozenset(f"{a}-{e}" for a in self._apps for e in self._envs)

    def target(self, app_id: str, environment: str) -> Target:
        if app_id not in self._apps:
            raise ValidationFailed(f"App '{app_id}' steht nicht im Katalog", app_id=app_id)
        if environment not in self._envs:
            raise ValidationFailed(f"Umgebung '{environment}' ist nicht erlaubt", environment=environment)
        return Target(app_id, environment)

    def namespace(self, namespace: str) -> Target:
        if namespace not in self.namespaces:
            raise ValidationFailed(
                f"Namespace '{namespace}' gehört zu keiner Katalog-App",
                namespace=namespace,
                allowed=sorted(self.namespaces),
            )
        app_id, _, environment = namespace.rpartition("-")
        return Target(app_id, environment)

    def host(self, app_id: str) -> str:
        return f"{app_id}.{self.base_domain}"

    def check_url(self, url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if not self.base_domain or not (host == self.base_domain or host.endswith("." + self.base_domain)):
            raise NotAllowed(f"Host '{host}' liegt nicht unter baseDomain", host=host)
        return url
