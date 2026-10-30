"""Repository-Arbeitskopien des Agenten: klonen, Dateien ändern, auf einen Agenten-Branch pushen, Pull Request öffnen.

Grenzen im Code: nur freigegebene Repositories, ein Branch ``<präfix><operationId>`` je Operation, kein Push auf den
Standard-Branch, kein Force-Push, keine Änderungen unter gesperrten Pfaden (z. B. ``.github/workflows/``), kein Merge.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from contracts import OperationsServices
from tools.common import NotAllowed, NotFound, ToolError, UnclearOutcome, UpstreamError, ValidationFailed, redact
from tools.github import AGENT_MARKER, GitHubClient

MAX_FILE_CHARS = 40_000
MAX_DIFF_CHARS = 20_000


@dataclass
class Clone:
    repository: str
    path: Path
    base: str
    branch: str
    pushed_sha: str | None = None
    pull_request: dict | None = None
    changed: set[str] = field(default_factory=set)


class GitWorkspace:
    def __init__(
        self,
        gh: GitHubClient,
        services: OperationsServices,
        operation_id: str,
        job_id: str,
        root: Path,
        allowed_repositories: frozenset[str],
        branch_prefix: str = "agent/",
        denied_paths: tuple[str, ...] = (".github/workflows/",),
        timeout: float = 120.0,
        remote_url: Callable[[str], str] = lambda repo: f"https://github.com/{repo}.git",
    ) -> None:
        self.remote_url = remote_url
        self.gh = gh
        self.s = services
        self.operation_id = operation_id
        self.job_id = job_id
        self.root = root
        self.allowed = allowed_repositories
        self.prefix = branch_prefix
        self.denied = denied_paths
        self.timeout = timeout
        self.clones: dict[str, Clone] = {}

    # Git-Aufrufe ohne Token in argv oder .git/config

    def _git(self, args: list[str], cwd: Path | None = None, auth: bool = False) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1"}
        if auth:
            basic = b64encode(f"x-access-token:{self.gh.auth.token()}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"})
        try:
            r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise UpstreamError(f"git {args[0]}: Zeitüberschreitung", timeout=True) from e
        if r.returncode != 0:
            raise UpstreamError(f"git {args[0]} fehlgeschlagen: {redact(r.stderr.strip())[-500:]}", returncode=r.returncode)
        return r.stdout

    def _clone(self, repository: str) -> Clone:
        c = self.clones.get(repository)
        if c is None:
            raise ValidationFailed(f"{repository} ist nicht geklont; zuerst clone_repository aufrufen")
        return c

    def _file(self, c: Clone, path: str, for_write: bool = False) -> Path:
        rel = path.lstrip("/")
        target = (c.path / rel).resolve()
        if not target.is_relative_to(c.path.resolve()) or rel.startswith(".git/") or rel == ".git":
            raise NotAllowed(f"Pfad {path!r} liegt außerhalb der Arbeitskopie")
        if for_write and any(rel.startswith(d) for d in self.denied):
            raise NotAllowed(f"Änderungen unter {path!r} sind gesperrt", denied=list(self.denied))
        return target

    # Werkzeuge

    def clone(self, repository: str, ref: str | None = None) -> Clone:
        if repository not in self.allowed:
            raise NotAllowed(f"Repository {repository} ist nicht freigegeben", allowed=sorted(self.allowed))
        if repository in self.clones:
            return self.clones[repository]
        base = ref or self.gh.default_branch(repository)
        path = self.root / self.operation_id / repository.replace("/", "__")
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["clone", "--depth", "50", "--branch", base, self.remote_url(repository), str(path)], auth=True)
        branch = f"{self.prefix}{re.sub(r'[^A-Za-z0-9._-]', '-', self.operation_id)}"
        # Liegt der Agenten-Branch schon remote (Wiederaufnahme), auf ihm weiterarbeiten statt ihn zu überschreiben.
        if self.gh.branch_head(repository, branch):
            self._git(["fetch", "--depth", "50", "origin", f"{branch}:{branch}"], cwd=path, auth=True)
            self._git(["checkout", branch], cwd=path)
        else:
            self._git(["checkout", "-b", branch], cwd=path)
        identity = self.gh.auth.identity()
        self._git(["config", "user.name", identity.name], cwd=path)
        self._git(["config", "user.email", identity.email], cwd=path)
        c = Clone(repository, path, base, branch)
        self.clones[repository] = c
        return c

    def read(self, repository: str, path: str) -> str:
        f = self._file(self._clone(repository), path)
        if not f.is_file():
            raise NotFound(f"{path} existiert nicht")
        return f.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]

    def list_dir(self, repository: str, path: str = "") -> list[str]:
        d = self._file(self._clone(repository), path or ".")
        if not d.is_dir():
            raise NotFound(f"{path} ist kein Verzeichnis")
        return sorted(p.name + ("/" if p.is_dir() else "") for p in d.iterdir() if p.name != ".git")[:500]

    def write(self, repository: str, path: str, content: str) -> None:
        c = self._clone(repository)
        f = self._file(c, path, for_write=True)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        c.changed.add(path.lstrip("/"))

    def replace(self, repository: str, path: str, old: str, new: str) -> None:
        text = self.read(repository, path)
        count = text.count(old)
        if count != 1:
            raise ValidationFailed(f"Suchtext kommt {count}-mal in {path} vor; genau ein Treffer nötig")
        self.write(repository, path, text.replace(old, new, 1))

    def diff(self, repository: str) -> str:
        c = self._clone(repository)
        self._git(["add", "-A"], cwd=c.path)
        return redact(self._git(["diff", "--cached", "--stat", "-p"], cwd=c.path))[:MAX_DIFF_CHARS]

    def commit_and_push(self, repository: str, message: str) -> Clone:
        self.s.ownership.assert_valid()
        c = self._clone(repository)
        if c.branch == c.base or not c.branch.startswith(self.prefix):
            raise NotAllowed(f"Push nur auf Branches mit Präfix {self.prefix}")
        self._git(["add", "-A"], cwd=c.path)
        staged = [p for p in self._git(["diff", "--cached", "--name-only"], cwd=c.path).splitlines() if p]
        if not staged:
            raise ValidationFailed("Keine Änderungen zum Committen")
        blocked = [p for p in staged if any(p.startswith(d) for d in self.denied)]
        if blocked:
            raise NotAllowed("Gesperrte Pfade geändert", paths=blocked)
        body = f"{message.strip()}\n\nAuftrag: {self.job_id}\nOperation: {self.operation_id}\n"
        self._git(["commit", "-m", body], cwd=c.path)
        sha = self._git(["rev-parse", "HEAD"], cwd=c.path).strip()
        action = self.s.actions.record_planned(
            self.operation_id, "git_push", f"{repository}:{c.branch}", {"repository": repository, "branch": c.branch, "commit": sha}
        )
        try:
            self._git(["push", "origin", f"HEAD:refs/heads/{c.branch}"], cwd=c.path, auth=True)
        except ToolError as e:
            # Ausgang klären: steht der Commit remote, war der Push erfolgreich.
            try:
                remote = self.gh.branch_head(repository, c.branch)
            except ToolError:
                remote = None
            if remote != sha:
                self.s.actions.update(action.action_id, "unclear" if e.details.get("timeout") else "failed", {"error": str(e)[:300]})
                if e.details.get("timeout"):
                    raise UnclearOutcome(f"Push von {sha[:12]} nach {c.branch} unklar", action.action_id) from e
                raise
        self.s.actions.update(action.action_id, "succeeded", {"commit": sha, "branch": c.branch})
        c.pushed_sha = sha
        c.changed.clear()
        return c

    def open_pull_request(self, repository: str, title: str, body: str) -> dict:
        self.s.ownership.assert_valid()
        c = self._clone(repository)
        if not c.pushed_sha:
            raise ValidationFailed("Erst commit_and_push ausführen")
        existing = self.gh.find_pull_request(repository, c.branch)
        if existing:
            c.pull_request = existing
            return existing
        action = self.s.actions.record_planned(self.operation_id, "pull_request", f"{repository}:{c.branch}", {"repository": repository, "branch": c.branch})
        text = f"{AGENT_MARKER}\n<!-- mmk-ops-job: {self.job_id} -->\n{body.strip()}\n\n_Auftrag `{self.job_id}`, Operation `{self.operation_id}`. Vom Betriebsagenten vorgeschlagen; Merge nur durch Menschen._"
        try:
            pr = self.gh.create_pull_request(repository, c.branch, c.base, title, text)
        except ToolError:
            pr = self.gh.find_pull_request(repository, c.branch)
            if pr is None:
                self.s.actions.update(action.action_id, "failed")
                raise
        self.s.actions.update(action.action_id, "succeeded", {"number": str(pr["number"]), "url": pr["url"]})
        c.pull_request = pr
        return pr
