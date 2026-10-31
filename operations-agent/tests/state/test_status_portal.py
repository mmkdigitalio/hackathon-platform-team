"""Portal discovery remains read-only and follows the public URL configuration."""

import hashlib
import re
import shutil

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.status as status_module
from api.settings import Settings
from api.status import install_status


@pytest.mark.parametrize("domain,scheme,url,enabled", [
    ("example.com", "https", "https://operations-agent.example.com", True),
    ("EXAMPLE.COM", "http", "http://operations-agent.example.com", True),
    ("", "https", None, False),
    ("https://invalid.example.com", "https", None, False),
    ("example.com", "file", None, False),
])
def test_status_portal_discovery(platform_root, monkeypatch, domain, scheme, url, enabled):
    monkeypatch.setenv("OPS_BASE_DOMAIN", domain)
    monkeypatch.setenv("OPS_PUBLIC_SCHEME", scheme)
    app = FastAPI()
    install_status(app, Settings(platform_root=platform_root), None,
                   transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with TestClient(app) as client:
        assert client.get("/status").json()["portal"] == {"url": url, "enabled": enabled}
        # The standalone status service only discovers the agent API, never proxies writes.
        assert client.post("/portal/conversations", json={}).status_code == 404


def test_chat_assets_use_content_versions_on_detail_page(platform_root, tmp_path, monkeypatch):
    static = tmp_path / "static"
    shutil.copytree(status_module.STATIC, static)
    monkeypatch.setattr(status_module, "STATIC", static)

    def assets():
        app = FastAPI()
        install_status(app, Settings(platform_root=platform_root), None, base_domain="")
        with TestClient(app) as client:
            html = client.get("/applications/affine").text
            urls = dict(re.findall(r'"(/status-assets/(?:status\.(?:js|css)))\?v=([a-f0-9]+)"', html))
            assert len(urls) == 2
            for path, digest in urls.items():
                content = (static / path.rsplit("/", 1)[1]).read_bytes()
                assert digest == hashlib.sha256(content).hexdigest()[:16]
                assert client.get(f"{path}?v={digest}").content == content
            return urls

    original = assets()
    assert assets() == original
    for name in ("status.js", "status.css"):
        with (static / name).open("a") as asset:
            asset.write("\n/* New chat deployment */\n")
    updated = assets()
    assert all(digest != updated[path] for path, digest in original.items())
