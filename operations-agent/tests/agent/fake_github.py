"""Simulierte GitHub-REST-API über ``httpx.MockTransport``."""

from __future__ import annotations

import base64
import itertools
import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from tools.github import API, GitHubClient

REPO = "mmkdigitalio/hackathon-platform-team"


class FakeGitHub:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], str] = {}
        self.catalog_apps: list[str] = []
        self.commits: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.dispatches: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        self.branches: dict[str, str] = {}
        self.pulls: list[dict[str, Any]] = []
        self.dispatch_mode = "ok"  # ok | timeout_after_accept | forbidden | no_run
        self.conclusion = "success"
        self.polls_until_done = 1
        self.on_dispatch: Callable[[dict[str, Any]], None] = lambda inputs: None
        self._ids = itertools.count(9001)

    def client(self) -> GitHubClient:
        return GitHubClient("test-token", REPO, http=httpx.Client(base_url=API, transport=httpx.MockTransport(self.handle)))

    def add_run(self, title: str, status: str = "in_progress", conclusion: str | None = None) -> dict[str, Any]:
        run = {
            "id": next(self._ids),
            "display_title": title,
            "status": status,
            "conclusion": conclusion,
            "html_url": "https://github.com/x/actions/runs/1",
            "created_at": "2026-10-30T10:00:00Z",
            "_polls": 0,
        }
        self.runs.append(run)
        return run

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        assert request.headers["Authorization"] == "Bearer test-token"
        if m := re.fullmatch(r"/repos/([^/]+/[^/]+)/contents/(.+)", path):
            repo, p = m.group(1), m.group(2)
            if p == "platform/catalog":
                return httpx.Response(200, json=[{"name": a, "type": "dir"} for a in self.catalog_apps])
            if (repo, p) not in self.files:
                children = sorted({f[len(p) + 1 :].split("/")[0] for r, f in self.files if r == repo and f.startswith(p + "/")})
                if children:
                    return httpx.Response(200, json=[{"name": c, "type": "file" if (repo, f"{p}/{c}") in self.files else "dir"} for c in children])
                return httpx.Response(404, json={"message": "Not Found"})
            content = base64.b64encode(self.files[(repo, p)].encode()).decode()
            return httpx.Response(200, json={"type": "file", "sha": "blob1", "content": content})
        if re.fullmatch(r"/repos/[^/]+/[^/]+", path):
            return httpx.Response(200, json={"default_branch": "main"})
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/branches/(.+)", path):
            sha = self.branches.get(m.group(1))
            return httpx.Response(200, json={"commit": {"sha": sha}}) if sha else httpx.Response(404, json={"message": "Branch not found"})
        if re.fullmatch(r"/repos/[^/]+/[^/]+/pulls", path):
            if request.method == "GET":
                head = request.url.params["head"].split(":", 1)[1]
                return httpx.Response(200, json=[p for p in self.pulls if p["head"] == head])
            data = json.loads(request.content)
            pr = {"number": len(self.pulls) + 1, "html_url": f"https://github.com/{REPO}/pull/{len(self.pulls) + 1}", "head": data["head"], "body": data["body"]}
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/commits", path):
            c = self.commits.get(request.url.params["path"])
            return httpx.Response(200, json=[{"sha": c["sha"]}] if c else [])
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/commits/(\w+)", path):
            c = next(c for c in self.commits.values() if c["sha"] == m.group(1))
            return httpx.Response(
                200,
                json={
                    "sha": c["sha"],
                    "html_url": f"https://github.com/{REPO}/commit/{c['sha']}",
                    "author": {"login": c["login"]},
                    "commit": {"author": {"name": c["name"], "date": c["date"]}, "message": c["message"]},
                    "files": [{"filename": c["path"], "patch": c["patch"]}],
                },
            )
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/issues/(\d+)/comments", path):
            body = json.loads(request.content)["body"]
            self.comments.append({"issue": int(m.group(1)), "body": body})
            return httpx.Response(201, json={"id": len(self.comments), "html_url": f"https://github.com/{REPO}/issues/{m.group(1)}#c"})
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/actions/workflows/([^/]+)/dispatches", path):
            if self.dispatch_mode == "forbidden":
                return httpx.Response(403, json={"message": "Resource not accessible by personal access token"}, headers={"X-Accepted-GitHub-Permissions": "actions=write"})
            inputs = json.loads(request.content)["inputs"]
            self.dispatches.append(inputs)
            self.on_dispatch(inputs)
            if self.dispatch_mode != "no_run":
                self.add_run(f"deploy {inputs['appId']} {inputs['environment']} op={inputs['operationId']} action={inputs['actionId']}")
            if self.dispatch_mode == "timeout_after_accept":
                raise httpx.ReadTimeout("lost response", request=request)
            return httpx.Response(204)
        if re.fullmatch(r"/repos/[^/]+/[^/]+/actions/workflows/([^/]+)/runs", path):
            per_page, page = int(request.url.params.get("per_page", 30)), int(request.url.params.get("page", 1))
            batch = self.runs[(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json={"total_count": len(self.runs), "workflow_runs": [self._public(r) for r in batch]})
        if m := re.fullmatch(r"/repos/[^/]+/[^/]+/actions/runs/(\d+)", path):
            run = next(r for r in self.runs if r["id"] == int(m.group(1)))
            run["_polls"] += 1
            if run["status"] != "completed" and run["_polls"] > self.polls_until_done:
                run["status"], run["conclusion"] = "completed", self.conclusion
            return httpx.Response(200, json=self._public(run))
        return httpx.Response(500, json={"message": f"unexpected {path}"})

    @staticmethod
    def _public(run: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in run.items() if not k.startswith("_")}

    def workflow_calls(self) -> int:
        return sum(1 for m, p in self.requests if m == "POST" and p.endswith("/dispatches"))
