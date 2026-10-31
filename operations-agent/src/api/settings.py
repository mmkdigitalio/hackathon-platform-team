"""Konfiguration aus Umgebungsvariablen.

Im Cluster legt der Agenten-App-Stack die Werte aus den Foundation-Ausgaben als
Kubernetes-Secret an (alertWebhookKey, maintenanceApiKey, githubWebhookSecret).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import find_platform_root

DEFAULT_ALLOWLIST = "mmkdigitalio/hackathon-platform-team"


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    alert_webhook_key: str = ""
    maintenance_api_key: str = ""
    github_webhook_secret: str = ""
    repo_allowlist: frozenset[str] = field(default_factory=lambda: frozenset({DEFAULT_ALLOWLIST}))
    platform_root: Path | None = None
    healthz_max_heartbeat_age_seconds: int = 600
    base_domain: str = field(default_factory=lambda: os.environ.get("OPS_BASE_DOMAIN", "").lower())
    public_scheme: str = field(default_factory=lambda: os.environ.get(
        "OPS_PUBLIC_SCHEME", os.environ.get("OPS_HTTP_SCHEME", "https")))
    portal_allowed_origins: tuple[str, ...] | None = field(default_factory=lambda: (
        tuple(origin.strip() for origin in os.environ["OPS_PORTAL_ALLOWED_ORIGINS"].split(",") if origin.strip())
        if "OPS_PORTAL_ALLOWED_ORIGINS" in os.environ else None
    ))

    @classmethod
    def from_env(cls) -> "Settings":
        allowlist = os.environ.get("OPS_GITHUB_REPO_ALLOWLIST", DEFAULT_ALLOWLIST)
        return cls(
            database_url=os.environ.get("OPS_DATABASE_URL", ""),
            alert_webhook_key=os.environ.get("ALERT_WEBHOOK_KEY", ""),
            maintenance_api_key=os.environ.get("MAINTENANCE_API_KEY", ""),
            github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
            repo_allowlist=frozenset(r.strip().lower() for r in allowlist.split(",") if r.strip()),
            platform_root=find_platform_root(),
        )

    @property
    def secrets(self) -> list[str]:
        return [s for s in (self.alert_webhook_key, self.maintenance_api_key, self.github_webhook_secret) if s]
