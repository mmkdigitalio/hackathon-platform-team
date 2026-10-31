"""Prüfung von App-/Umgebungsreferenzen gegen den Live- oder Image-Katalog.

Eine Referenz braucht Katalogeintrag und Sollzustand für die gewählte Umgebung.
Ohne gültigen Live-Snapshot gilt der Dateibaum unter ``OPS_PLATFORM_ROOT``.
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

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


@dataclass(frozen=True)
class CatalogSource:
    root: Path | None
    kind: str = "image"
    git_sha: str | None = None

    def catalog_path(self, app_id: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / f"catalog--{app_id}--app.yaml" if self.kind == "snapshot" else self.root / "catalog" / app_id / "app.yaml"

    def environment_path(self, environment: str, app_id: str) -> Path | None:
        if self.root is None:
            return None
        if self.kind == "snapshot":
            return self.root / f"environments--{environment}--{app_id}.yaml"
        return self.root / "environments" / environment / f"{app_id}.yaml"

    def app_ids(self) -> list[str]:
        if self.root is None:
            return []
        if self.kind == "snapshot":
            return sorted(p.name.removeprefix("catalog--").removesuffix("--app.yaml")
                          for p in self.root.glob("catalog--*--app.yaml") if p.is_file())
        return sorted(p.parent.name for p in (self.root / "catalog").glob("*/app.yaml"))

    def available(self, environment: str) -> bool:
        if self.root is None:
            return False
        if self.kind == "snapshot":
            return any(self.root.glob(f"environments--{environment}--*.yaml"))
        return (self.root / "catalog").is_dir() and (self.root / "environments" / environment).is_dir()

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
        if not self.catalog_path(app_id).is_file():
            return f"App {app_id!r} nicht im Katalog (platform/catalog/{app_id}/app.yaml fehlt)"
        if not self.environment_path(environment, app_id).is_file():
            return (
                f"App {app_id!r} hat keinen Sollzustand für {environment!r} "
                f"(platform/environments/{environment}/{app_id}.yaml fehlt)"
            )
        return None


def catalog_source(platform_root: Path | None) -> CatalogSource:
    """Quelle je Aufruf wählen; ungültige Snapshots ersetzen den Image-Katalog nicht."""
    configured = os.environ.get("OPS_CATALOG_SNAPSHOT_DIR")
    if configured:
        try:
            root = Path(configured)
            # Eine ConfigMap-Generation pro Aufruf lesen, auch beim atomaren Volume-Wechsel.
            root = (root / "..data").resolve(strict=True) if (root / "..data").is_symlink() else root
            metadata = json.loads((root / "snapshot.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or not all(
                isinstance(metadata.get(key), str) and metadata[key].strip() for key in ("gitSha", "generatedAt")
            ):
                raise ValueError("Ungültige Snapshot-Metadaten")
            datetime.fromisoformat(metadata["generatedAt"])
            catalogs = list(root.glob("catalog--*--app.yaml"))
            if not catalogs:
                raise ValueError("Leerer Snapshot")
            for path in [*catalogs, *root.glob("environments--*.yaml")]:
                if not isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict):
                    raise ValueError("Ungültiger Snapshot-Inhalt")
            return CatalogSource(root, "snapshot", metadata["gitSha"])
        except (OSError, ValueError, TypeError, UnicodeError, yaml.YAMLError):
            pass
    return CatalogSource(platform_root)


class Catalog:
    def __init__(self, platform_root: Path | None):
        self.root = platform_root

    def problem(self, app_id: str | None, environment: str | None) -> str | None:
        return catalog_source(self.root).problem(app_id, environment)

    def app_ids(self) -> list[str]:
        return catalog_source(self.root).app_ids()
