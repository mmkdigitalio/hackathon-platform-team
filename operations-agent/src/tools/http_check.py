"""Eigene HTTP-Prüfung von ``<app-id>.<baseDomain><Prüfpfad>`` (Ersatz für Azure-Messwerte, E6)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from tools.common import utcnow
from tools.targets import TargetPolicy


class HttpProbe:
    def __init__(self, policy: TargetPolicy, http: httpx.Client | None = None, timeout: float = 10.0, scheme: str = "https") -> None:
        self.policy = policy
        self.scheme = scheme
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=False)

    def check(self, app_id: str, path: str) -> tuple[dict[str, Any], list[str]]:
        """Antwortstatus wie 503 ist ein Befund, kein Werkzeugfehler."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.policy.check_url(f"{self.scheme}://{self.policy.host(app_id)}{path}")
        at = utcnow()
        started = time.monotonic()
        result: dict[str, Any] = {"url": url, "checkedAt": at.isoformat()}
        try:
            r = self._http.get(url)
            result.update(statusCode=r.status_code, healthy=200 <= r.status_code < 300, errorType=None)
        except httpx.TimeoutException:
            result.update(statusCode=None, healthy=False, errorType="timeout")
        except httpx.ConnectError as e:
            result.update(statusCode=None, healthy=False, errorType="connect", detail=type(e).__name__)
        except httpx.HTTPError as e:
            result.update(statusCode=None, healthy=False, errorType="http", detail=type(e).__name__)
        result["durationMs"] = int((time.monotonic() - started) * 1000)
        return result, [f"probe:{url}@{at.isoformat()}"]
