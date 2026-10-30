"""Git-Werkzeuge gegen ein lokales Bare-Repository: Freigaben, Pfadsperren, Agenten-Branch, Push, Pull Request."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.common import NotAllowed, ValidationFailed
from tools.git_repo import GitWorkspace
from tools.github_auth import GitIdentity

from .conftest import World

REPO = "mmkdigitalio/hackathon-platform-team"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FakeAuth:
    def token(self) -> str:
        return "ghs_testtoken_not_real_123"

    def identity(self) -> GitIdentity:
        return GitIdentity("hackathonplatformagent[bot]", "350301036+hackathonplatformagent[bot]@users.noreply.github.com")


class FakeGh:
    def __init__(self, bare: Path) -> None:
        self.auth = FakeAuth()
        self.bare = bare
        self.prs: list[dict] = []

    def default_branch(self, repository: str) -> str:
        return "main"

    def branch_head(self, repository: str, branch: str) -> str | None:
        out = git("ls-remote", str(self.bare), f"refs/heads/{branch}")
        return out.split()[0] if out else None

    def find_pull_request(self, repository: str, branch: str) -> dict | None:
        return next((p for p in self.prs if p["branch"] == branch), None)

    def create_pull_request(self, repository: str, branch: str, base: str, title: str, body: str) -> dict:
        pr = {"number": len(self.prs) + 1, "url": f"https://github.com/{repository}/pull/{len(self.prs) + 1}", "branch": branch, "body": body}
        self.prs.append(pr)
        return pr


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "platform/environments/staging").mkdir(parents=True)
    (seed / "platform/environments/staging/affine.yaml").write_text("values:\n  affine:\n    nodeOptions: --max-old-space-size=16\n")
    (seed / ".github/workflows").mkdir(parents=True)
    (seed / ".github/workflows/app-deploy.yml").write_text("name: x\n")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=seed)
    b = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(seed), str(b))
    return b


def workspace(world: World, bare: Path, tmp_path: Path, op: str = "op-1") -> tuple[GitWorkspace, FakeGh]:
    gh = FakeGh(bare)
    ws = GitWorkspace(gh, world.services, op, "job-1", tmp_path / "work", frozenset({REPO}), remote_url=lambda r: str(bare))
    return ws, gh


def test_clone_only_allowed_repositories(world: World, bare: Path, tmp_path: Path) -> None:
    ws, _ = workspace(world, bare, tmp_path)
    with pytest.raises(NotAllowed):
        ws.clone("someone/else")
    assert not (tmp_path / "work").exists()


def test_edit_commit_push_and_pull_request(world: World, bare: Path, tmp_path: Path) -> None:
    ws, gh = workspace(world, bare, tmp_path)
    c = ws.clone(REPO)
    assert c.branch == "agent/op-1" and c.base == "main"
    ws.replace(REPO, "platform/environments/staging/affine.yaml", "--max-old-space-size=16", "--max-old-space-size=1536")
    assert "+    nodeOptions: --max-old-space-size=1536" in ws.diff(REPO)
    ws.commit_and_push(REPO, "affine: nodeOptions auf den Sollwert zurücksetzen")

    assert git("rev-parse", "refs/heads/agent/op-1", cwd=bare).strip() == c.pushed_sha
    assert "16" in git("show", "main:platform/environments/staging/affine.yaml", cwd=bare), "main bleibt unverändert"
    author = git("log", "-1", "--format=%an <%ae>%n%B", "agent/op-1", cwd=bare)
    assert author.startswith("hackathonplatformagent[bot] <350301036+") and "Operation: op-1" in author
    push = world.actions.of_kind("git_push")[0]
    assert push.status == "succeeded" and push.external_refs["commit"] == c.pushed_sha

    pr = ws.open_pull_request(REPO, "affine: Konfiguration korrigieren", "Befund: Heap 16 MB")
    assert pr["number"] == 1 and gh.prs[0]["body"].startswith("<!-- mmk-ops-agent -->")
    assert ws.open_pull_request(REPO, "nochmal", "x") == gh.prs[0], "zweiter Aufruf öffnet keinen weiteren PR"
    assert len(world.actions.of_kind("pull_request")) == 1


def test_path_guards(world: World, bare: Path, tmp_path: Path) -> None:
    ws, _ = workspace(world, bare, tmp_path)
    c = ws.clone(REPO)
    with pytest.raises(NotAllowed):
        ws.write(REPO, ".github/workflows/app-deploy.yml", "name: hacked\n")
    with pytest.raises(NotAllowed):
        ws.read(REPO, "../../etc/passwd")
    with pytest.raises(NotAllowed):
        ws.write(REPO, ".git/config", "x")
    with pytest.raises(ValidationFailed):
        ws.replace(REPO, "platform/environments/staging/affine.yaml", "nicht vorhanden", "x")
    # Umgehung über das Dateisystem wird beim Commit erkannt
    (c.path / ".github/workflows/app-deploy.yml").write_text("name: hacked\n")
    with pytest.raises(NotAllowed):
        ws.commit_and_push(REPO, "x")
    assert git("ls-remote", str(bare), "refs/heads/agent/op-1") == ""


def test_no_token_in_clone_config(world: World, bare: Path, tmp_path: Path) -> None:
    ws, _ = workspace(world, bare, tmp_path)
    c = ws.clone(REPO)
    assert "ghs_testtoken" not in (c.path / ".git/config").read_text()


def test_resume_continues_existing_agent_branch(world: World, bare: Path, tmp_path: Path) -> None:
    ws, _ = workspace(world, bare, tmp_path)
    ws.clone(REPO)
    ws.write(REPO, "notes.md", "erster Lauf\n")
    first = ws.commit_and_push(REPO, "erster Lauf").pushed_sha
    ws2, _ = workspace(world, bare, tmp_path / "second")
    c2 = ws2.clone(REPO)
    assert git("rev-parse", "HEAD", cwd=c2.path).strip() == first
    ws2.write(REPO, "notes.md", "zweiter Lauf\n")
    ws2.commit_and_push(REPO, "zweiter Lauf")
    log = git("log", "--format=%s", "agent/op-1", cwd=bare).splitlines()
    assert log[:2] == ["zweiter Lauf", "erster Lauf"], "kein Force-Push, Historie bleibt"


def test_nothing_to_commit(world: World, bare: Path, tmp_path: Path) -> None:
    ws, _ = workspace(world, bare, tmp_path)
    ws.clone(REPO)
    with pytest.raises(ValidationFailed):
        ws.commit_and_push(REPO, "leer")


def test_runner_proposes_fix_as_pull_request_but_stays_unresolved(world: World, bare: Path, tmp_path: Path) -> None:
    from .fakes import ScriptedModel, call, say, script
    from .test_runner import broken_config

    broken_config(world)
    world.github.on_dispatch = lambda _: None
    path = "platform/environments/staging/affine.yaml"
    model = ScriptedModel(
        responder=script(
            call("compare_desired_actual"),
            call("clone_repository", {"repository": REPO}),
            call("repo_replace", {"repository": REPO, "path": path, "old": "--max-old-space-size=16", "new": "--max-old-space-size=1536"}),
            call("repo_write_file", {"repository": REPO, "path": ".github/workflows/app-deploy.yml", "content": "x"}),
            call("commit_and_push", {"repository": REPO, "message": "affine: nodeOptions zurücksetzen"}),
            call("open_pull_request", {"repository": REPO, "title": "affine: Konfiguration korrigieren", "body": "Heap 16 MB bricht den Start ab"}),
            say("PR geöffnet."),
        )
    )
    from agent import Dependencies, execute

    from .conftest import MODEL, settings

    result = execute(
        world.invocation(),
        world.services,
        settings=settings(git_workdir=tmp_path / "work", git_repositories=frozenset({REPO})),
        model=MODEL,
        deps=Dependencies(
            github=world.github.client(), kubernetes=lambda p: world.k8s(p), http=world.probe(), chat_model=model,
            sleep=world.clock.sleep, clock=world.clock, git_remote_url=lambda r: str(bare),
        ),
    )
    errors = {c.name: c.error_kind for c in result.tool_calls if c.status == "error"}
    assert errors == {"repo_write_file": "not_allowed"}
    assert result.proposal == "needs_attention", "ein PR behebt den Vorfall nicht"
    assert result.references["pullRequests"][0]["number"] == 1
    assert result.references["gitPushes"][0]["branch"] == "agent/op-1"
    assert world.github.pulls[0]["body"].startswith("<!-- mmk-ops-agent -->")
    assert "1536" in git("show", f"agent/op-1:{path}", cwd=bare) and "16\n" in git("show", f"main:{path}", cwd=bare)
