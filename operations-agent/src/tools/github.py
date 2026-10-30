"""GitHub-Adapter; Zugang über die GitHub App des Agenten oder den PAT aus ``opsGithubToken``."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

import httpx

from contracts import IssueRef
from tools.common import Conflict, NotFound, PermissionDenied, TimedOut, UpstreamError, ValidationFailed
from tools.github_auth import StaticToken, TokenProvider

AGENT_MARKER = "<!-- mmk-ops-agent -->"
API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | TokenProvider, repository: str, http: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self.repository = repository
        self.auth: TokenProvider = StaticToken(token) if isinstance(token, str) else token
        self._http = http or httpx.Client(base_url=API, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth.token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, what: str, **kw: Any) -> httpx.Response:
        try:
            r = self._http.request(method, path, headers=self._headers(), **kw)
        except httpx.TimeoutException as e:
            raise TimedOut(f"GitHub: {what} Zeitüberschreitung") from e
        except httpx.HTTPError as e:
            raise UpstreamError(f"GitHub: {what} fehlgeschlagen ({type(e).__name__})") from e
        if r.status_code < 300:
            return r
        message = _message(r)
        if r.status_code in (401, 403) and "rate limit" not in message.lower():
            missing = r.headers.get("X-Accepted-GitHub-Permissions")
            raise PermissionDenied(
                f"GitHub verweigert {what}" + (f"; benötigt: {missing}" if missing else ""),
                status=r.status_code,
                missing_permission=missing,
                github_message=message,
            )
        if r.status_code == 404:
            raise NotFound(f"GitHub: {what} nicht gefunden (oder PAT ohne Zugriff)", status=404)
        if r.status_code == 409:
            raise Conflict(f"GitHub: {what} Konflikt: {message}", status=409)
        if r.status_code == 422:
            raise ValidationFailed(f"GitHub lehnt {what} ab: {message}", status=422)
        raise UpstreamError(f"GitHub: {what} mit Status {r.status_code}", status=r.status_code, github_message=message)

    # Lesen

    def get_file(self, path: str, ref: str, repository: str | None = None) -> dict[str, Any]:
        repo = repository or self.repository
        r = self._request("GET", f"/repos/{repo}/contents/{path}", f"Datei {repo}:{path}@{ref} lesen", params={"ref": ref})
        body = r.json()
        if isinstance(body, list) or body.get("type") != "file":
            raise ValidationFailed(f"{path} ist keine Datei")
        return {"repository": repo, "path": path, "ref": ref, "sha": body["sha"], "content": base64.b64decode(body["content"]).decode()}

    def get_document(self, path: str, ref: str, repository: str | None = None, max_chars: int = 20_000) -> dict[str, Any]:
        """Datei oder Verzeichnis mit Markdown-Dateien (README zuerst), zusammengefügt und begrenzt."""
        repo = repository or self.repository
        r = self._request("GET", f"/repos/{repo}/contents/{path}", f"{repo}:{path}@{ref} lesen", params={"ref": ref})
        body = r.json()
        if not isinstance(body, list):
            return self.get_file(path, ref, repository)
        names = sorted(
            (e["name"] for e in body if e.get("type") == "file" and e["name"].lower().endswith(".md")),
            key=lambda n: (n.lower() != "readme.md", n),
        )
        if not names:
            raise ValidationFailed(f"{path} enthält keine Markdown-Dateien")
        parts, size = [], 0
        for name in names:
            text = self.get_file(f"{path.rstrip('/')}/{name}", ref, repository)["content"]
            parts.append(f"<!-- Datei: {name} -->\n{text}")
            size += len(text)
            if size >= max_chars:
                break
        return {"repository": repo, "path": path, "ref": ref, "files": names, "content": "\n\n".join(parts)[:max_chars]}

    def list_dir(self, path: str, ref: str) -> list[str]:
        r = self._request("GET", f"/repos/{self.repository}/contents/{path}", f"Verzeichnis {path}@{ref} lesen", params={"ref": ref})
        body = r.json()
        if not isinstance(body, list):
            raise ValidationFailed(f"{path} ist kein Verzeichnis")
        return [e["name"] for e in body if e.get("type") == "dir"]

    def last_commit(self, path: str, ref: str = "main") -> dict[str, Any]:
        r = self._request(
            "GET",
            f"/repos/{self.repository}/commits",
            f"Commits zu {path} lesen",
            params={"path": path, "sha": ref, "per_page": 1},
        )
        commits = r.json()
        if not commits:
            raise NotFound(f"Kein Commit auf {path}")
        sha = commits[0]["sha"]
        detail = self._request("GET", f"/repos/{self.repository}/commits/{sha}", f"Commit {sha} lesen").json()
        patch = next((f.get("patch") for f in detail.get("files", []) if f.get("filename") == path), None)
        author = detail["commit"]["author"]
        return {
            "sha": sha,
            "author": (detail.get("author") or {}).get("login") or author.get("name"),
            "authorName": author.get("name"),
            "date": author.get("date"),
            "message": detail["commit"]["message"],
            "url": detail.get("html_url"),
            "path": path,
            "diff": (patch or "")[:8000],
        }

    # Issues

    def comment_issue(self, issue: IssueRef, job_id: str, text: str) -> dict[str, Any]:
        if issue.repository != self.repository:
            raise ValidationFailed("Kommentare nur im eigenen Repository", repository=issue.repository)
        body = f"{AGENT_MARKER}\n<!-- mmk-ops-job: {job_id} -->\n{text}\n\n_Auftrag `{job_id}`_"
        r = self._request("POST", f"/repos/{self.repository}/issues/{issue.number}/comments", "Issue kommentieren", json={"body": body})
        c = r.json()
        return {"id": c["id"], "url": c.get("html_url")}

    # Branches und Pull Requests (nur über tools.git_repo)

    def default_branch(self, repository: str) -> str:
        return self._request("GET", f"/repos/{repository}", f"Repository {repository} lesen").json()["default_branch"]

    def branch_head(self, repository: str, branch: str) -> str | None:
        try:
            r = self._request("GET", f"/repos/{repository}/branches/{branch}", f"Branch {branch} lesen")
        except NotFound:
            return None
        return r.json()["commit"]["sha"]

    def find_pull_request(self, repository: str, branch: str) -> dict[str, Any] | None:
        owner = repository.split("/")[0]
        r = self._request("GET", f"/repos/{repository}/pulls", "Pull Requests lesen", params={"head": f"{owner}:{branch}", "state": "open"})
        prs = r.json()
        return {"number": prs[0]["number"], "url": prs[0]["html_url"]} if prs else None

    def create_pull_request(self, repository: str, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
        r = self._request(
            "POST",
            f"/repos/{repository}/pulls",
            "Pull Request anlegen",
            json={"title": title, "head": branch, "base": base, "body": body, "draft": False},
        )
        pr = r.json()
        return {"number": pr["number"], "url": pr["html_url"]}

    # Workflows

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> int | None:
        r = self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{workflow}/dispatches",
            f"Workflow {workflow} starten",
            json={"ref": ref, "inputs": inputs, "return_run_details": True},
        )
        if r.status_code == 200 and r.content:
            run_id = r.json().get("workflow_run_id")
            return int(run_id) if run_id else None
        return None

    def dispatch_runs(self, workflow: str, created_since: datetime, max_pages: int = 5) -> tuple[list[dict[str, Any]], bool]:
        """Läufe seit ``created_since``; ``True`` heißt vollständig gelesen."""
        runs: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            r = self._request(
                "GET",
                f"/repos/{self.repository}/actions/workflows/{workflow}/runs",
                f"Läufe von {workflow} lesen",
                params={
                    "event": "workflow_dispatch",
                    "created": f">={created_since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                    "per_page": 100,
                    "page": page,
                },
            )
            body = r.json()
            batch = body.get("workflow_runs", [])
            runs += [_run(x) for x in batch]
            if len(batch) < 100 or len(runs) >= body.get("total_count", 0):
                return runs, True
        return runs, False

    def get_run(self, run_id: int) -> dict[str, Any]:
        return _run(self._request("GET", f"/repos/{self.repository}/actions/runs/{run_id}", f"Lauf {run_id} lesen").json())


def _run(x: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": x["id"],
        "title": x.get("display_title") or x.get("name") or "",
        "status": x.get("status"),
        "conclusion": x.get("conclusion"),
        "url": x.get("html_url"),
        "createdAt": x.get("created_at"),
        "headSha": x.get("head_sha"),
    }


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message", ""))
    except ValueError:
        return r.text[:200]
