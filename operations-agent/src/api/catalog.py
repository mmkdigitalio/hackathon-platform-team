"""Prüfung von App-/Umgebungsreferenzen gegen B's Katalog.

Eine Referenz gilt, wenn ``platform/catalog/<app-id>/app.yaml`` und
``platform/environments/<env>/<app-id>.yaml`` existieren.
"""

import os
import re
from pathlib import Path

APP_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
ENVIRONMENTS = ("staging", "production")


def find_platform_root(start: Path | None = None) -> Path | None:
    configured = os.environ.get("OPS_PLATFORM_ROOT")
    if configured:
        return Path(configured)
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        platform = candidate / "platform"
        if (platform / "catalog").is_dir() or (platform / "schemas").is_dir():
            return candidate / "platform"
    return None


class Catalog:
    def __init__(self, platform_root: Path | None):
        self.root = platform_root

    def problem(self, app_id: str | None, environment: str | None) -> str | None:
        """Zuordnungsfehler als Text oder None, wenn die Referenz gültig ist."""
        if not app_id or not environment:
            missing = [n for n, v in (("App", app_id), ("Umgebung", environment)) if not v]
            return f"{' und '.join(missing)} nicht ermittelbar"
        if not APP_ID.match(app_id):
            return f"App-ID {app_id!r} entspricht nicht der Konvention"
        if environment not in ENVIRONMENTS:
            return f"Umgebung {environment!r} unbekannt"
        if self.root is None:
            return "Katalog nicht verfügbar (OPS_PLATFORM_ROOT nicht gesetzt)"
        if not (self.root / "catalog" / app_id / "app.yaml").is_file():
            return f"App {app_id!r} nicht im Katalog (platform/catalog/{app_id}/app.yaml fehlt)"
        if not (self.root / "environments" / environment / f"{app_id}.yaml").is_file():
            return (
                f"App {app_id!r} hat keinen Sollzustand für {environment!r} "
                f"(platform/environments/{environment}/{app_id}.yaml fehlt)"
            )
        return None

    def app_ids(self) -> list[str]:
        if self.root is None or not (self.root / "catalog").is_dir():
            return []
        return sorted(p.parent.name for p in (self.root / "catalog").glob("*/app.yaml"))
