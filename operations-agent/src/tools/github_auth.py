"""GitHub-Zugang des Agenten: GitHub App (Installations-Token, 1 h gültig) oder Fine-grained PAT als Rückfall."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import SecretStr

from tools.common import PermissionDenied, TimedOut, UpstreamError, register_secret

API = "https://api.github.com"
_REFRESH_MARGIN = 300  # Sekunden vor Ablauf erneuern


class TokenProvider(Protocol):
    def token(self) -> str: ...


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str


class StaticToken:
    """Fine-grained PAT aus ``opsGithubToken``; Commits laufen unter einer neutralen Agentenidentität."""

    def __init__(self, token: str) -> None:
        self._token = token
        register_secret(token)

    def token(self) -> str:
        return self._token

    def identity(self) -> GitIdentity:
        return GitIdentity("mmk-ops-agent", "mmk-ops-agent@users.noreply.github.com")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class GitHubAppAuth:
    """Erzeugt ein App-JWT (RS256) und tauscht es gegen ein Installations-Token für das Monorepo."""

    def __init__(
        self,
        app_id: str,
        private_key: SecretStr,
        repository: str,
        installation_id: str | None = None,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app_id = app_id
        self._key = serialization.load_pem_private_key(private_key.get_secret_value().encode(), password=None)
        self.repository = repository
        self.installation_id = installation_id
        self._http = http or httpx.Client(base_url=API, timeout=15)
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0
        self._identity: GitIdentity | None = None

    def _jwt(self) -> str:
        now = int(self._clock())
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.app_id}).encode())
        signature = self._key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{_b64(signature)}"

    def _app_request(self, method: str, path: str, what: str) -> dict:
        headers = {"Authorization": f"Bearer {self._jwt()}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            r = self._http.request(method, path, headers=headers)
        except httpx.TimeoutException as e:
            raise TimedOut(f"GitHub App: {what} Zeitüberschreitung") from e
        if r.status_code in (401, 403, 404):
            raise PermissionDenied(f"GitHub App: {what} verweigert ({r.status_code})", status=r.status_code)
        if r.status_code >= 300:
            raise UpstreamError(f"GitHub App: {what} mit Status {r.status_code}", status=r.status_code)
        return r.json()

    def token(self) -> str:
        if self._token and self._clock() < self._expires - _REFRESH_MARGIN:
            return self._token
        if not self.installation_id:
            self.installation_id = str(self._app_request("GET", f"/repos/{self.repository}/installation", "Installation ermitteln")["id"])
        body = self._app_request("POST", f"/app/installations/{self.installation_id}/access_tokens", "Installations-Token anfordern")
        self._token = body["token"]
        register_secret(self._token)
        self._expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
        return self._token

    def identity(self) -> GitIdentity:
        """Commit-Autor ``<slug>[bot]`` mit der noreply-Adresse der App."""
        if self._identity is None:
            slug = self._app_request("GET", "/app", "App lesen")["slug"]
            try:
                r = self._http.get(f"/users/{slug}[bot]", headers={"Accept": "application/vnd.github+json"})
                user_id = r.json()["id"] if r.status_code == 200 else None
            except httpx.HTTPError:
                user_id = None
            email = f"{user_id}+{slug}[bot]@users.noreply.github.com" if user_id else f"{slug}[bot]@users.noreply.github.com"
            self._identity = GitIdentity(f"{slug}[bot]", email)
        return self._identity
