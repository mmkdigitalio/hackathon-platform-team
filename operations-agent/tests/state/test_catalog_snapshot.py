"""Live-Katalog: Quellenauswahl, Volume-Wechsel, Status und Ereigniseingang."""

import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import normalize
from api.app import create_app
from api.catalog import Catalog, catalog_source
from api.portal import install_portal
from api.settings import Settings
from api.status import StatusCollector, install_status
from persistence import portal
from persistence.db import connect

SHA = "a" * 40


def write_snapshot(directory, app_id="kimai", git_sha=SHA):
    directory.mkdir(exist_ok=True)
    (directory / "snapshot.json").write_text(json.dumps({
        "gitSha": git_sha, "generatedAt": "2026-10-31T12:00:00+00:00",
    }))
    (directory / f"catalog--{app_id}--app.yaml").write_text(
        f"appId: {app_id}\ndescription: Zeiterfassung\navailability:\n  path: /health\n"
    )
    (directory / f"environments--staging--{app_id}.yaml").write_text("chart:\n  version: 1.2.3\n")
    return directory


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    directory = write_snapshot(tmp_path / "snapshot")
    monkeypatch.setenv("OPS_CATALOG_SNAPSHOT_DIR", str(directory))
    return directory


def test_snapshot_exposes_new_app_and_replaces_image_catalog(platform_root, snapshot_dir):
    catalog = Catalog(platform_root)
    assert not (platform_root / "catalog" / "kimai").exists()
    assert catalog.app_ids() == ["kimai"]
    assert catalog.problem("kimai", "staging") is None
    assert "nicht im Katalog" in catalog.problem("affine", "staging")
    assert "keinen Sollzustand" in catalog.problem("kimai", "production")
    assert Catalog(None).problem("kimai", "staging") is None
    source = catalog_source(platform_root)
    assert source.catalog_path("kimai") == snapshot_dir / "catalog--kimai--app.yaml"
    assert source.environment_path("staging", "kimai") == snapshot_dir / "environments--staging--kimai.yaml"


def test_no_snapshot_preserves_tree_behavior(platform_root, monkeypatch):
    monkeypatch.delenv("OPS_CATALOG_SNAPSHOT_DIR", raising=False)
    catalog = Catalog(platform_root)
    assert catalog.app_ids() == ["affine", "operations-agent"]
    assert catalog.problem("affine", "staging") is None
    assert "nicht im Katalog" in catalog.problem("kimai", "staging")
    source = catalog_source(platform_root)
    assert (source.kind, source.git_sha) == ("image", None)
    assert source.catalog_path("affine") == platform_root / "catalog" / "affine" / "app.yaml"
    assert Catalog(None).app_ids() == []
    assert "Katalog nicht verfügbar" in Catalog(None).problem("kimai", "staging")


@pytest.mark.parametrize("metadata", [
    None, "{", "[]", "null", "{}",
    '{"gitSha": 123, "generatedAt": "2026-10-31T12:00:00Z"}',
    '{"gitSha": "", "generatedAt": "2026-10-31T12:00:00Z"}',
    '{"gitSha": "abc", "generatedAt": "invalid"}',
])
def test_missing_or_invalid_metadata_falls_back(platform_root, snapshot_dir, metadata):
    path = snapshot_dir / "snapshot.json"
    if metadata is None:
        path.unlink()
    else:
        path.write_text(metadata)
    assert catalog_source(platform_root).kind == "image"
    assert Catalog(platform_root).app_ids() == ["affine", "operations-agent"]
    assert Catalog(platform_root).problem("affine", "staging") is None


def test_unreadable_snapshot_falls_back(platform_root, snapshot_dir, monkeypatch):
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == snapshot_dir / "snapshot.json":
            raise PermissionError("nicht lesbar")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert catalog_source(platform_root).kind == "image"


@pytest.mark.parametrize("filename,content", [
    ("catalog--kimai--app.yaml", "availability: ["),
    ("catalog--kimai--app.yaml", "- invalid"),
    ("environments--staging--kimai.yaml", "chart: ["),
    ("environments--staging--kimai.yaml", ""),
])
def test_malformed_snapshot_yaml_falls_back(platform_root, snapshot_dir, filename, content):
    (snapshot_dir / filename).write_text(content)
    assert catalog_source(platform_root).kind == "image"
    assert Catalog(platform_root).problem("affine", "staging") is None


def test_missing_directory_or_catalog_files_falls_back(platform_root, snapshot_dir, monkeypatch):
    (snapshot_dir / "catalog--kimai--app.yaml").unlink()
    assert catalog_source(platform_root).kind == "image"
    monkeypatch.setenv("OPS_CATALOG_SNAPSHOT_DIR", str(snapshot_dir / "missing"))
    assert catalog_source(platform_root).kind == "image"


def test_existing_catalog_reads_updates_without_restart(platform_root, snapshot_dir):
    catalog = Catalog(platform_root)
    assert catalog.app_ids() == ["kimai"]
    write_snapshot(snapshot_dir, "new-app", "b" * 40)
    assert catalog.app_ids() == ["kimai", "new-app"]
    assert catalog.problem("new-app", "staging") is None
    (snapshot_dir / "environments--staging--new-app.yaml").unlink()
    assert "keinen Sollzustand" in catalog.problem("new-app", "staging")
    (snapshot_dir / "snapshot.json").unlink()
    assert catalog.app_ids() == ["affine", "operations-agent"]


def test_configmap_generation_switch_is_read_on_next_call(platform_root, tmp_path, monkeypatch):
    volume = tmp_path / "volume"
    volume.mkdir()
    first = write_snapshot(volume / "..first")
    second = write_snapshot(volume / "..second", "new-app", "b" * 40)
    (volume / "..data").symlink_to(first, target_is_directory=True)
    monkeypatch.setenv("OPS_CATALOG_SNAPSHOT_DIR", str(volume))
    source = catalog_source(platform_root)
    assert source.app_ids() == ["kimai"]
    (volume / "..next").symlink_to(second, target_is_directory=True)
    (volume / "..next").replace(volume / "..data")
    assert source.app_ids() == ["kimai"]
    assert source.git_sha == SHA
    assert Catalog(platform_root).app_ids() == ["new-app"]
    assert catalog_source(platform_root).git_sha == "b" * 40


def test_status_targets_and_response_use_snapshot(platform_root, snapshot_dir):
    settings = Settings(platform_root=platform_root)
    collector = StatusCollector(settings, None, base_domain="example.com")
    targets, complete = collector.targets()
    assert complete
    assert [(app["id"], app["version"], app["checkPath"]) for app in targets] == [("kimai", "1.2.3", "/health")]
    app = FastAPI()
    collector = install_status(app, settings, None, base_domain="example.com",
                               transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with TestClient(app) as client:
        data = client.get("/status").json()
        assert data["catalogSource"] == {"kind": "snapshot", "gitSha": SHA}
        assert [service["id"] for service in data["services"]] == ["kimai"]
        assert client.get("/applications/kimai").status_code == 200
        assert client.get("/applications/affine").status_code == 404
        write_snapshot(snapshot_dir, "new-app", "b" * 40)
        assert client.get("/status").json() == data
        collector._expires = 0
        refreshed = client.get("/status").json()
        assert refreshed["catalogSource"] == {"kind": "snapshot", "gitSha": "b" * 40}
        assert [service["id"] for service in refreshed["services"]] == ["kimai", "new-app"]


def test_status_exposes_image_source_without_snapshot(platform_root, monkeypatch):
    monkeypatch.delenv("OPS_CATALOG_SNAPSHOT_DIR", raising=False)
    app = FastAPI()
    install_status(app, Settings(platform_root=platform_root), None, base_domain="")
    with TestClient(app) as client:
        assert client.get("/status").json()["catalogSource"] == {"kind": "image", "gitSha": None}


@pytest.mark.parametrize("app_id,accepted", [("kimai", True), ("affine", False), ("unknown", False)])
def test_alert_intake_mapping_uses_snapshot(platform_root, snapshot_dir, app_id, accepted):
    body = {"schemaId": "azureMonitorCommonAlertSchema", "data": {"essentials": {
        "alertId": "alert-1", "alertRule": f"alert-{app_id}-staging-availability", "monitorCondition": "Fired",
    }}}
    event = normalize.azure_monitor(body, json.dumps(body).encode(), Catalog(platform_root))
    assert (event["mappingError"] is None) is accepted
    assert event["appId"] == app_id


def test_portal_validates_live_snapshot_before_storing(platform_root, snapshot_dir, monkeypatch):
    pool = MagicMock()
    pool.connection.return_value = nullcontext(pool)
    pool.transaction.return_value = nullcontext()
    monkeypatch.setattr(portal, "verify_code", lambda conn, code: "code-id")
    stored = []

    def store_message(conn, **fields):
        stored.append(fields)
        return {"status": "accepted"}

    monkeypatch.setattr(portal, "store_message", store_message)
    app = FastAPI()
    install_portal(app, Settings(platform_root=platform_root), pool)
    with TestClient(app) as client:
        def send(app_id):
            return client.post("/portal/conversations", headers={"X-Portal-Code": "valid"}, json={
                "appId": app_id, "text": "Hilfe", "reporter": "Team", "clientMessageId": app_id,
            })

        assert send("kimai").status_code == 202
        assert send("affine").status_code == 400
        assert send("unknown").status_code == 400
        write_snapshot(snapshot_dir, "new-app")
        assert send("new-app").status_code == 202
    assert [message["app_id"] for message in stored] == ["kimai", "new-app"]
    assert all(message["environment"] == "staging" for message in stored)


def test_snapshot_portal_and_intake_persist_new_app(db_url, platform_root, snapshot_dir):
    with connect(db_url) as conn:
        _, code = portal.create_code(conn, "Team", 30)
    settings = Settings(database_url=db_url, platform_root=platform_root, maintenance_api_key="maintenance-key")
    with TestClient(create_app(settings)) as client:
        response = client.post("/portal/conversations", headers={"X-Portal-Code": code}, json={
            "appId": "kimai", "reporter": "Team", "text": "Hilfe", "clientMessageId": "first",
        })
        assert response.status_code == 202
        assert client.post("/portal/conversations", headers={"X-Portal-Code": code}, json={
            "appId": "affine", "reporter": "Team", "text": "Hilfe", "clientMessageId": "absent",
        }).status_code == 400
        for app_id, mapped in [("kimai", True), ("affine", False)]:
            response = client.post("/events/maintenance", headers={"X-Api-Key": "maintenance-key"}, json={
                "appId": app_id, "environment": "staging", "deliveryId": f"test-{app_id}",
                "externalRef": f"test-{app_id}", "eventType": "availability-check-failed", "severity": "error",
            })
            assert response.status_code == 202
            assert (response.json()["mappingError"] is None) is mapped
    with connect(db_url) as conn:
        apps = conn.execute("SELECT app_id FROM jobs WHERE state = 'queued'").fetchall()
        assert len(apps) == 2 and all(row["app_id"] == "kimai" for row in apps)
