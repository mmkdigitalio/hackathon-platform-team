"""Generischer Smoke-Test nach Vertrag v0.1 (platform/schemas/app-catalog/README.md).

Fragt TARGET_URL + CHECK_PATH ab und erwartet EXPECT_STATUS. Diagnose auf stderr,
genau eine JSON-Zeile als Ergebnis auf stdout; Exit-Code 0 genau bei Erfolg.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def check(url: str, expect: int, timeout: float, insecure: bool) -> dict:
    context = ssl._create_unverified_context() if insecure else None
    request = urllib.request.Request(url, headers={"User-Agent": "mmk-smoke-http/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = response.status
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return {"check": "http-status", "success": False, "detail": f"nicht erreichbar: {reason}", "ref": url}
    return {
        "check": "http-status",
        "success": status == expect,
        "detail": f"HTTP {status}, erwartet {expect}",
        "ref": url,
    }


def main() -> int:
    started = now()
    target = os.environ.get("TARGET_URL", "").rstrip("/")
    if not target:
        evidence = [{"check": "config", "success": False, "detail": "TARGET_URL fehlt"}]
    else:
        url = target + os.environ.get("CHECK_PATH", "/")
        attempts = max(1, int(os.environ.get("ATTEMPTS", "3")))
        # Wiederholung überbrückt kurze Verzögerungen des Ingress direkt nach einem Rollout
        for attempt in range(1, attempts + 1):
            print(f"prüfe {url} (Versuch {attempt}/{attempts})", file=sys.stderr)
            result = check(
                url,
                int(os.environ.get("EXPECT_STATUS", "200")),
                float(os.environ.get("TIMEOUT_SECONDS", "10")),
                os.environ.get("INSECURE_TLS", "") == "true",
            )
            if result["success"] or attempt == attempts:
                break
            time.sleep(float(os.environ.get("RETRY_DELAY_SECONDS", "5")))
        evidence = [result]
    success = all(e["success"] for e in evidence)
    print(json.dumps({"success": success, "startedAt": started, "finishedAt": now(), "evidence": evidence}), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
