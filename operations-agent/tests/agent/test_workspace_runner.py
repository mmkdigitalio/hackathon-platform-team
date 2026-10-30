"""Runner mit lokalem Workspace: Shell- und Dateiwerkzeuge des SDK, Arbeitskopie im Workspace, Push nur über das Werkzeug."""

from __future__ import annotations

from pathlib import Path

from tools.workspace import LocalWorkspace, WorkspaceSettings

from .conftest import World
from .test_git_repo import REPO, bare, git  # noqa: F401 - Fixture

PATH = "platform/environments/staging/affine.yaml"
CLONE = f"/workspace/repos/{REPO.replace('/', '__')}"


def run(world: World, bare: Path, tmp_path: Path, *steps):  # noqa: F811 - Fixture-Parameter
    from agent import Dependencies, execute

    from .conftest import MODEL, settings
    from .fakes import ScriptedModel, script
    from .test_runner import broken_config

    broken_config(world)
    world.github.on_dispatch = lambda _: None
    created: list[LocalWorkspace] = []

    def workspace(op: str, job: str) -> LocalWorkspace:
        ws = LocalWorkspace(WorkspaceSettings(mode="local", local_root=tmp_path / "ws"), op, job)
        created.append(ws)
        return ws

    result = execute(
        world.invocation(),
        world.services,
        settings=settings(git_repositories=frozenset({REPO})),
        model=MODEL,
        deps=Dependencies(
            github=world.github.client(), kubernetes=lambda p: world.k8s(p), http=world.probe(),
            chat_model=ScriptedModel(responder=script(*steps)), sleep=world.clock.sleep, clock=world.clock,
            git_remote_url=lambda r: str(bare), workspace=workspace,
        ),
    )
    return result, created


def test_fix_in_workspace_with_shell_and_pull_request(world: World, bare: Path, tmp_path: Path) -> None:  # noqa: F811
    from .fakes import call, say

    result, created = run(
        world, bare, tmp_path,
        call("clone_repository", {"repository": REPO}),
        call("execute", {"command": f"grep -n max-old-space {CLONE}/{PATH}"}),
        call("edit_file", {"file_path": f"{CLONE}/{PATH}", "old_string": "--max-old-space-size=16", "new_string": "--max-old-space-size=1536"}),
        call("execute", {"command": f"cd {CLONE} && git status --short && git rev-parse --abbrev-ref HEAD"}, n=1),
        call("record_finding", {"statement": "Heap-Grenze 16 MB im Sollzustand", "certainty": "confirmed",
                                "evidence_refs": ["workspace://local-op-1/execute/2"]}),
        call("commit_and_push", {"repository": REPO, "message": "affine: nodeOptions zurücksetzen"}),
        call("open_pull_request", {"repository": REPO, "title": "affine: Konfiguration korrigieren", "body": "Heap 16 MB bricht den Start ab"}),
        say("PR geöffnet."),
    )
    names = [c.name for c in result.tool_calls]
    assert names[:4] == ["clone_repository", "execute", "edit_file", "execute"]
    assert "repo_replace" not in names
    grep_call = result.tool_calls[1]
    assert grep_call.status == "ok" and "max-old-space" in (grep_call.target or "")
    finding = next(f for f in result.findings if f.provenance == "model")
    assert finding.certainty == "confirmed", "Shell-Ausgabe ist zitierbare Evidenz"
    assert result.references["gitPushes"][0]["branch"] == "agent/op-1"
    assert "1536" in git("show", f"agent/op-1:{PATH}", cwd=bare)
    assert "16\n" in git("show", f"main:{PATH}", cwd=bare), "main bleibt unverändert"
    assert result.proposal == "needs_attention"
    assert result.references["workspace"] == {"id": created[0].id, "kept": False}
    assert not created[0].root.exists(), "Workspace wird am Laufende gelöscht"


def test_read_only_token_file_in_workspace(world: World, bare: Path, tmp_path: Path) -> None:  # noqa: F811
    from .fakes import call, say

    result, _ = run(
        world, bare, tmp_path,
        call("execute", {"command": "test -s /workspace/.ops/gh-token && stat -c %a /workspace/.ops/gh-token || stat -f %Lp /workspace/.ops/gh-token"}),
        say("ok"),
    )
    assert result.tool_calls[0].name == "execute" and result.tool_calls[0].status == "ok"
