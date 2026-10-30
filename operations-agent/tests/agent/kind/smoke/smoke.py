"""Lokaler Smoke-Test nach B's Vertrag: GET ``$TARGET_URL$SMOKE_PATH``, ein JSON auf stdout, Exit-Code 0/1."""

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime

started = datetime.now(UTC).isoformat()
url = os.environ["TARGET_URL"].rstrip("/") + os.environ.get("SMOKE_PATH", "/")
evidence: dict = {"url": url}
try:
    with urllib.request.urlopen(url, timeout=10) as r:
        evidence["status"] = r.status
except Exception as e:  # noqa: BLE001
    evidence["error"] = f"{type(e).__name__}: {e}"
success = evidence.get("status") == 200
print(json.dumps({"success": success, "startedAt": started, "finishedAt": datetime.now(UTC).isoformat(), "evidence": evidence}))
sys.exit(0 if success else 1)
