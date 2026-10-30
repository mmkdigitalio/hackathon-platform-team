"""GitHub-App-Authentifizierung mit erzeugtem Testschlüssel: JWT-Signatur, Token-Cache, Erneuerung, Fehler."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

from agent.config import ConfigError, agent_settings
from tools.common import PermissionDenied, redact
from tools.github_auth import GitHubAppAuth, StaticToken

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def github(clock: Clock, calls: list, status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        jwt = request.headers["Authorization"].removeprefix("Bearer ")
        head, body, sig = jwt.split(".")
        KEY.public_key().verify(b64d(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        claims = json.loads(b64d(body))
        assert claims["iss"] == "5542094" and claims["exp"] - claims["iat"] <= 600
        if status != 200:
            return httpx.Response(status, json={"message": "nope"})
        if request.url.path.endswith("/installation"):
            return httpx.Response(200, json={"id": 176901698})
        expires = datetime.fromtimestamp(clock.t, UTC) + timedelta(hours=1)
        return httpx.Response(201, json={"token": f"ghs_token_{len(calls)}_abcdefgh", "expires_at": expires.isoformat().replace("+00:00", "Z")})

    return httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle))


def test_installation_token_is_cached_and_renewed() -> None:
    clock, calls = Clock(), []
    auth = GitHubAppAuth("5542094", SecretStr(PEM), "mmkdigitalio/hackathon-platform-team", http=github(clock, calls), clock=clock)
    first = auth.token()
    assert auth.token() == first and calls == [("GET", "/repos/mmkdigitalio/hackathon-platform-team/installation"), ("POST", "/app/installations/176901698/access_tokens")]
    clock.t += 3600 - 200  # innerhalb der Erneuerungsmarge
    assert auth.token() != first and len(calls) == 3
    assert redact(f"x {auth.token()} y") == "x *** y", "Token wird als Secret maskiert"


def test_app_errors_are_structured() -> None:
    auth = GitHubAppAuth("5542094", SecretStr(PEM), "mmkdigitalio/x", installation_id="1", http=github(Clock(), [], status=401), clock=Clock())
    with pytest.raises(PermissionDenied):
        auth.token()


def test_settings_choose_app_over_pat(tmp_path) -> None:
    key_file = tmp_path / "key.pem"
    key_file.write_text(PEM)
    base = {"OPS_BASE_DOMAIN": "1-2-3-4.sslip.io"}
    s = agent_settings({**base, "OPS_GITHUB_APP_ID": "5542094", "OPS_GITHUB_APP_PRIVATE_KEY_FILE": str(key_file)})
    assert isinstance(s.github_auth(), GitHubAppAuth) and PEM[40:80] not in repr(s)
    escaped = PEM.replace("\n", "\\n")
    assert agent_settings({**base, "OPS_GITHUB_APP_ID": "1", "OPS_GITHUB_APP_PRIVATE_KEY": escaped}).github_app_private_key.get_secret_value() == PEM
    assert isinstance(agent_settings({**base, "OPS_GITHUB_TOKEN": "pat"}).github_auth(), StaticToken)
    assert isinstance(agent_settings({**base, "OPS_GITHUB_APP_ID": "1", "OPS_GITHUB_TOKEN": "pat"}).github_auth(), StaticToken)
    with pytest.raises(ConfigError):
        agent_settings({**base, "OPS_GITHUB_APP_ID": "1"})
    with pytest.raises(ConfigError):
        agent_settings({**base, "OPS_GITHUB_APP_PRIVATE_KEY": escaped, "OPS_GITHUB_TOKEN": "pat"})
    with pytest.raises(ConfigError):
        agent_settings(base)
