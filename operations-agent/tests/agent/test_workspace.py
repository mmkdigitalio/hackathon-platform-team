"""Workspace-Verträge ohne Cluster, Netzwerk oder externe Werkzeuge prüfen."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import shlex
import stat
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace as NS

import kubernetes.stream
import pytest
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.sandbox import BaseSandbox
from kubernetes.client.exceptions import ApiException

from tools.common import Conflict, PermissionDenied, TimedOut, UpstreamError, ValidationFailed
from tools.workspace import (
    CREDENTIALS_DIR,
    REPOS_DIR,
    WORKDIR,
    KubernetesWorkspace,
    LocalWorkspace,
    WorkspaceSettings,
    create_workspace,
)

IMAGE = "registry.example/toolbox@sha256:" + "a" * 64


def settings(**kwargs) -> WorkspaceSettings:
    return WorkspaceSettings(mode="kubernetes", namespace="ops", image=IMAGE, **kwargs)


def pod(phase="Running", *, ready=True, reason=None, deleting=False):
    waiting = NS(reason=reason, message="Image konnte nicht geladen werden") if reason else None
    return NS(
        metadata=NS(deletion_timestamp="now" if deleting else None),
        status=NS(phase=phase, reason=None, message=None, container_statuses=[
            NS(name="workspace", ready=ready, state=NS(waiting=waiting, terminated=None)),
        ]),
    )


class FakeCore:
    def __init__(self, existing=None):
        self.pod = existing
        self.calls = []
        self.created = []
        self.deleted = []

    def read_namespaced_pod(self, name, namespace, **kwargs):
        self.calls.append(("read", name, namespace))
        if self.pod is None:
            raise ApiException(status=404)
        return self.pod

    def create_namespaced_pod(self, namespace, body, **kwargs):
        self.calls.append(("create", body["metadata"]["name"], namespace))
        self.created.append(body)
        self.pod = pod()

    def delete_namespaced_pod(self, name, namespace, **kwargs):
        self.calls.append(("delete", name, namespace))
        self.deleted.append(kwargs)
        self.pod = None

    def list_namespaced_event(self, namespace, **kwargs):
        return NS(items=[NS(reason="Failed", message="ImagePullBackOff")])

    def connect_get_namespaced_pod_exec(self, *args, **kwargs):
        raise AssertionError("Nur der gepatchte Stream darf exec aufrufen")


class FakeWS:
    def __init__(self, stdout="", stderr="", code=0, *, stuck=False):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = code
        self.open = True
        self.closed = False
        self.writes = []
        self.stuck = stuck

    def is_open(self):
        return self.open

    def update(self, timeout=0):
        assert timeout == 1
        self.open = self.stuck

    def read_stdout(self, timeout=0):
        result, self.stdout = self.stdout, ""
        return result

    def read_stderr(self, timeout=0):
        result, self.stderr = self.stderr, ""
        return result

    def write_stdin(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True
        self.open = False


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def streams(monkeypatch):
    calls, responses = [], []

    def stream(method, *args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0) if responses else FakeWS()

    monkeypatch.setattr(kubernetes.stream, "stream", stream)
    return calls, responses


@pytest.fixture
def local(tmp_path):
    workspace = LocalWorkspace(WorkspaceSettings(mode="local", local_root=tmp_path), "op-1", "job-1")
    workspace.start()
    yield workspace
    workspace.close()


def test_local_execution_and_paths(local):
    assert isinstance(local, LocalShellBackend)
    result = local.execute("echo hello; echo error >&2; exit 7")
    assert result.output == "hello\nerror\n"
    assert result.exit_code == 7
    assert not result.truncated
    assert local.execute("pwd; echo $HOME").output == "/workspace\n/workspace/.home\n"
    assert local.repos_dir == REPOS_DIR
    assert local.credentials_dir == CREDENTIALS_DIR
    assert local.execute("echo ok > /workspace/repos/x; cat /workspace/repos/x").output == "ok\n"
    assert local.shell_path("/workspace/repos/x") == str(local.root / "repos/x")
    assert local.execute("printf '%s' /workspace-other").output == "/workspace-other"


def test_local_minimal_environment(local, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TEST_PARENT_SECRET", "must-not-be-inherited")
    assert local.execute("printf '%s' \"${WORKSPACE_TEST_PARENT_SECRET-unset}\"").output == "unset"
    assert local.execute("printf '%s' \"$LANG:$GIT_TERMINAL_PROMPT\"").output == "C.UTF-8:0"
    assert local._env["PATH"] == os.environ["PATH"]


def test_local_root_with_shell_characters(tmp_path):
    workspace = LocalWorkspace(WorkspaceSettings(mode="local", local_root=tmp_path / "space 'quote' $name"), "op", "job")
    try:
        assert workspace.upload_files([("/workspace/repos/x", b"ok")])[0].error is None
        for path in ("/workspace/repos/x", "'/workspace/repos/x'", '"/workspace/repos/x"'):
            assert workspace.execute(f"cat {path}").output == "ok"
    finally:
        workspace.close()


def test_local_timeout_terminates_process_group(local):
    result = local.execute("echo before; sleep 30; echo after", timeout=1)
    assert result.exit_code == 124
    assert "Zeitüberschreitung" in result.output
    assert "before" in result.output
    assert "after" not in result.output


def test_local_truncation_keeps_head_and_tail(local):
    local.settings = replace(local.settings, max_output_bytes=80)
    result = local.execute("printf 'HEAD'; printf '%0200d' 0; printf 'TAIL'")
    assert result.truncated
    assert result.output.startswith("HEAD")
    assert result.output.endswith("TAIL")
    assert "gekuerzt" in result.output
    assert len(result.output.encode()) <= 80


def test_local_file_roundtrip_and_errors(local):
    content = bytes(range(256)) * 3
    result = local.upload_files([("/workspace/repos/a/b.bin", content), ("/etc/no", b"x")])
    assert result[0].error is None
    assert result[1].error in ("permission_denied", "invalid_path")
    downloaded = local.download_files(["/workspace/repos/a/b.bin", "/workspace/no", "/workspace/repos"])
    assert downloaded[0].content == content
    assert downloaded[1].error == "file_not_found"
    assert downloaded[2].error == "is_directory"
    for path in ("relative", "/workspace/../outside", "/workspace-other/x", "/workspace/\x00bad"):
        assert local.upload_files([(path, b"x")])[0].error
        assert local.download_files([path])[0].error


def test_local_symlink_escape_is_rejected(local, tmp_path):
    (local.root / "outside").symlink_to(tmp_path)
    assert local.upload_files([("/workspace/outside/no", b"x")])[0].error
    assert not (tmp_path / "no").exists()


def test_local_secret_permissions_and_redaction(local):
    secret = "unique-workspace-secret-238971"
    local.write_secret_file("gh-token", secret)
    path = local.root / ".ops/gh-token"
    assert path.read_text() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert local.execute("cat /workspace/.ops/gh-token").output == "***"
    for name in ("../escape", "a/b", "a..b", "", ".hidden"):
        with pytest.raises(ValidationFailed):
            local.write_secret_file(name, secret)


def test_local_filesystem_helpers(local):
    path = "/workspace/repos/note.txt"
    assert local.write(path, "alpha\nbeta\n").error is None
    assert local.read(path).file_data["content"] == "alpha\nbeta\n"
    assert local.edit(path, "beta", "gamma").occurrences == 1
    assert "gamma" in local.read(path).file_data["content"]
    assert [entry["path"] for entry in local.ls(REPOS_DIR).entries] == [path]
    assert [match["path"] for match in local.grep("gamma", REPOS_DIR).matches] == [path]
    assert [match["path"] for match in local.grep("gamma").matches] == [path]
    assert [entry["path"] for entry in local.glob("*.txt", REPOS_DIR).matches] == [path]
    assert [entry["path"] for entry in local.glob("**/*.txt").matches] == [path]
    assert local._real_path(path) == path


def _gnu_grep() -> bool:
    try:
        return "GNU grep" in subprocess.run(["grep", "--version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return False


# Die grep-Hilfe des SDK erwartet GNU grep (NUL-getrennte Treffer); das Toolbox-Image bringt es mit, macOS nicht.
@pytest.mark.skipif(not _gnu_grep(), reason="GNU grep fehlt (z. B. macOS)")
def test_base_sandbox_helpers_execute_through_local_backend(local):
    class Sandbox(BaseSandbox):
        id = "local-test-transport"

        def execute(self, command, *, timeout=None):
            return local.execute(command, timeout=timeout)

        def upload_files(self, files):
            return local.upload_files([(local._to_virtual_path(Path(path)), content) for path, content in files])

        def download_files(self, paths):
            return local.download_files([local._to_virtual_path(Path(path)) for path in paths])

    sandbox = Sandbox()
    directory = local.shell_path(REPOS_DIR)
    path = directory + "/base.txt"
    assert sandbox.write(path, "first\nsecond\n").error is None
    assert sandbox.read(path).file_data["content"] == "first\nsecond"
    assert sandbox.edit(path, "second", "third").occurrences == 1
    visible = "/workspace/repos/base.txt"
    assert [entry["path"] for entry in sandbox.ls(directory).entries] == [visible]
    assert [match["path"] for match in sandbox.grep("third", directory).matches] == [visible]
    assert [entry["path"] for entry in sandbox.glob("*.txt", directory).matches] == [path]


def test_local_lifecycle_and_keep(tmp_path, monkeypatch):
    workspace = LocalWorkspace(WorkspaceSettings(mode="local", local_root=tmp_path), "A / B", "job")
    workspace.start()
    workspace.execute("echo work > /workspace/repos/work")
    workspace.start()
    assert (workspace.root / "repos/work").read_text() == "work\n"
    monkeypatch.setenv("OPS_WORKSPACE_KEEP", "1")
    workspace.close()
    assert workspace.root.exists()
    monkeypatch.delenv("OPS_WORKSPACE_KEEP")
    workspace.close()
    assert not workspace.root.exists()
    assert not workspace.started


@pytest.mark.parametrize("azure", [False, True])
def test_kubernetes_pod_spec(streams, azure):
    core = FakeCore()
    clock = FakeClock()
    workspace = KubernetesWorkspace(
        settings(image_pull_secret="pull", azure_client_id="client" if azure else None,
                 azure_tenant_id="tenant" if azure else None, azure_subscription_id="sub" if azure else None),
        core, "Op: 123 / X", "Job: 789", sleep=clock.sleep, clock=clock,
    )
    workspace.start()
    workspace.start()
    assert workspace.started
    assert len(core.created) == 1
    body = core.created[0]
    spec = body["spec"]
    assert body["metadata"]["annotations"] == {"ops.mmk/operation-id": "Op: 123 / X", "ops.mmk/job-id": "Job: 789"}
    assert body["metadata"]["labels"]["app.kubernetes.io/name"] == "operations-agent-workspace"
    assert body["metadata"]["labels"]["app.kubernetes.io/part-of"] == "operations-agent"
    for key in ("ops.mmk/operation-id", "ops.mmk/job-id"):
        assert re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?", body["metadata"]["labels"][key])
    assert spec["activeDeadlineSeconds"] == 3600
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is True
    assert spec["serviceAccountName"] == "operations-agent-workspace"
    assert spec["imagePullSecrets"] == [{"name": "pull"}]
    assert spec["securityContext"] == {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001}
    container = spec["containers"][0]
    assert container["image"] == IMAGE
    assert container["workingDir"] == WORKDIR
    assert container["command"] == ["sleep", "3600"]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["resources"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "2Gi", "ephemeral-storage": "5Gi"},
    }
    assert spec["volumes"][:2] == [{"name": "workspace", "emptyDir": {"sizeLimit": "5Gi"}}, {"name": "tmp", "emptyDir": {}}]
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["HOME"] == "/workspace/.home"
    assert env["OPS_OPERATION_ID"] == "Op: 123 / X"
    assert env["OPS_JOB_ID"] == "Job: 789"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # Kein Webhook-Label: das projizierte Token legt der Code selbst an.
    assert "azure.workload.identity/use" not in body["metadata"]["labels"]
    if azure:
        assert env["AZURE_CLIENT_ID"] == "client"
        assert env["AZURE_TENANT_ID"] == "tenant"
        assert env["AZURE_SUBSCRIPTION_ID"] == "sub"
        assert env["AZURE_FEDERATED_TOKEN_FILE"] == "/var/run/secrets/azure/tokens/azure-identity-token"
        assert env["AZURE_AUTHORITY_HOST"] == "https://login.microsoftonline.com/"
        token = spec["volumes"][2]["projected"]["sources"][0]["serviceAccountToken"]
        assert token == {"audience": "api://AzureADTokenExchange", "expirationSeconds": 3600, "path": "azure-identity-token"}
        assert {"name": "azure-identity-token", "mountPath": "/var/run/secrets/azure/tokens", "readOnly": True} in container["volumeMounts"]
    else:
        assert not any(name.startswith("AZURE_") for name in env)
        assert len(spec["volumes"]) == 2
    assert not workspace.enable_capture_offload
    assert workspace.repos_dir == REPOS_DIR
    assert workspace.credentials_dir == CREDENTIALS_DIR


def test_kubernetes_resume_running_pod(streams):
    core = FakeCore(pod())
    workspace = KubernetesWorkspace(settings(), core, "resume", "job")
    workspace.execute("echo hello")
    assert core.created == []
    assert core.deleted == []
    assert workspace.started


@pytest.mark.parametrize("phase", ["Failed", "Succeeded"])
def test_kubernetes_recreates_finished_pod(streams, phase):
    core = FakeCore(pod(phase))
    clock = FakeClock()
    workspace = KubernetesWorkspace(settings(), core, "resume", "job", sleep=clock.sleep, clock=clock)
    workspace.start()
    assert len(core.deleted) == len(core.created) == 1
    actions = [call[0] for call in core.calls]
    assert actions.index("delete") < actions.index("create")


def test_kubernetes_waits_for_terminating_pod(streams):
    core = FakeCore(pod(deleting=True))
    clock = FakeClock()

    def sleep(seconds):
        clock.sleep(seconds)
        core.pod = None

    workspace = KubernetesWorkspace(settings(), core, "resume", "job", sleep=sleep, clock=clock)
    workspace.start()
    assert core.deleted == []
    assert len(core.created) == 1


def test_kubernetes_create_conflict_reuses_pod(streams, monkeypatch):
    core = FakeCore()

    def create(*args, **kwargs):
        core.pod = pod()
        raise ApiException(status=409)

    monkeypatch.setattr(core, "create_namespaced_pod", create)
    KubernetesWorkspace(settings(), core, "race", "job").start()
    assert core.created == []


def test_kubernetes_start_timeout_includes_waiting_reason(streams):
    clock = FakeClock()
    core = FakeCore(pod("Pending", ready=False, reason="ImagePullBackOff"))
    workspace = KubernetesWorkspace(settings(), core, "pending", "job", sleep=clock.sleep, clock=clock, start_timeout=2)
    with pytest.raises(TimedOut, match="ImagePullBackOff"):
        workspace.start()
    assert clock.now == 2
    assert not workspace.started


@pytest.mark.parametrize(("requested", "expected"), [(9999, 1800), (-10, 1), (None, 300), (0, 300)])
def test_kubernetes_exec_command_and_timeout_clamp(streams, requested, expected):
    calls, responses = streams
    workspace = KubernetesWorkspace(settings(), FakeCore(), "exec", "job")
    workspace.started = True
    response = FakeWS("hello\n", "stderr\n", 7)
    responses.append(response)
    command = "echo 'hello'; exit 7"
    result = workspace.execute(command, timeout=requested)
    assert result.output == "hello\nstderr\n"
    assert result.exit_code == 7
    assert response.closed
    args, kwargs = calls[-1]
    assert args == (workspace.pod_name, "ops")
    assert kwargs["command"] == ["bash", "-lc", f"cd /workspace && timeout -k 5 {expected} bash -lc {shlex.quote(command)} 2>&1"]
    assert kwargs["stdin"] is False
    assert kwargs["stdout"] is kwargs["stderr"] is True
    assert kwargs["tty"] is kwargs["_preload_content"] is False
    assert kwargs["container"] == "workspace"


def test_kubernetes_timeout_note_truncation_and_redaction(streams):
    _, responses = streams
    workspace = KubernetesWorkspace(settings(max_output_bytes=90), FakeCore(), "exec", "job")
    workspace.started = True
    responses.append(FakeWS("before", code=124))
    assert "Zeitüberschreitung" in workspace.execute("sleep 10").output
    responses.append(FakeWS("HEAD" + "a" * 200 + "TAIL"))
    result = workspace.execute("anything")
    assert result.truncated
    assert result.output.startswith("HEAD") and result.output.endswith("TAIL")
    assert len(result.output.encode()) <= 90
    responses.append(FakeWS("token=super-sensitive-value"))
    assert "super-sensitive-value" not in workspace.execute("anything").output


def test_kubernetes_wall_clock_guard(streams):
    _, responses = streams
    clock = FakeClock()
    response = FakeWS("partial", stuck=True)
    original_update = response.update

    def update(timeout):
        original_update(timeout)
        clock.sleep(10)

    response.update = update
    responses.append(response)
    workspace = KubernetesWorkspace(settings(), FakeCore(), "exec", "job", clock=clock)
    workspace.started = True
    result = workspace.execute("hang", timeout=1)
    assert result.exit_code is None
    assert "partial" in result.output
    assert "Zeitüberschreitung" in result.output
    assert response.closed


@pytest.mark.parametrize(("status", "error"), [(404, UpstreamError), (401, PermissionDenied), (403, PermissionDenied),
                                               (409, Conflict), (500, UpstreamError)])
def test_kubernetes_exec_maps_api_errors(monkeypatch, status, error):
    def stream(*args, **kwargs):
        raise ApiException(status=status)

    monkeypatch.setattr(kubernetes.stream, "stream", stream)
    workspace = KubernetesWorkspace(settings(), FakeCore(), "exec", "job")
    workspace.started = True
    with pytest.raises(error) as exc:
        workspace.execute("echo hi")
    if status == 404:
        assert "Workspace verloren" in str(exc.value)
        assert "nicht committete Änderungen" in str(exc.value)


@pytest.mark.parametrize("operation", ["UPPER / spaces: ä?", "x" * 100, "---", "", "a" * 56 + "?" + "z" * 20])
def test_kubernetes_pod_name(operation):
    workspace = KubernetesWorkspace(settings(), FakeCore(), operation, "job!" * 80)
    assert len(workspace.pod_name) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", workspace.pod_name)
    if len(operation) > 63:
        assert workspace.pod_name.endswith(hashlib.sha1(operation.encode()).hexdigest()[:8])
    assert workspace.id == workspace.pod_name
    for label in workspace._pod_spec()["metadata"]["labels"].values():
        assert len(label) <= 63


def test_kubernetes_upload_download_and_secret_transport(streams):
    calls, responses = streams
    workspace = KubernetesWorkspace(settings(), FakeCore(), "files", "job")
    workspace.started = True
    content = bytes(range(256)) * 500
    upload = FakeWS()
    responses.append(upload)
    assert workspace.upload_files([("/workspace/repos/binary", content)])[0].error is None
    assert base64.b64decode("".join(upload.writes)) == content
    assert all(len(chunk) <= 16_384 for chunk in upload.writes)
    assert calls[-1][1]["stdin"] is True
    assert "head -c" in calls[-1][1]["command"][2]
    responses.append(FakeWS(base64.b64encode(content).decode()))
    assert workspace.download_files(["/workspace/repos/binary"])[0].content == content
    assert 'base64 -w0 -- "$1"' in calls[-1][1]["command"][2]
    secret = "unique-kubernetes-credential-95185"
    upload = FakeWS()
    responses.extend([upload, FakeWS()])
    workspace.write_secret_file("gh-token", secret)
    assert base64.b64decode("".join(upload.writes)).decode() == secret
    assert all(secret not in str(kwargs["command"]) for _, kwargs in calls)
    assert all(base64.b64encode(secret.encode()).decode() not in str(kwargs["command"]) for _, kwargs in calls)
    responses.append(FakeWS(secret))
    assert workspace.execute("cat /workspace/.ops/gh-token").output == "***"


def test_upload_receiver_finishes_without_stdin_eof(tmp_path, monkeypatch):
    workspace = KubernetesWorkspace(settings(), FakeCore(), "files", "job")
    content = bytes(range(256)) * 200
    target = tmp_path / "nested/binary"

    def file_exec(path, script, *args, stdin=None):
        with subprocess.Popen(
            ["bash", "-c", script, "_", str(target), *args], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            # Der Schreibkanal bleibt bis nach dem Prozessende ausdrücklich offen.
            for offset in range(0, len(stdin), 16_384):
                process.stdin.write(stdin[offset : offset + 16_384].encode())
                process.stdin.flush()
            code = process.wait(timeout=2)
            assert not process.stdin.closed
            return process.stdout.read().decode(), code, False

    monkeypatch.setattr(workspace, "_file_exec", file_exec)
    assert workspace.upload_files([("/workspace/nested/binary", content)])[0].error is None
    assert target.read_bytes() == content
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize(("code", "error"), [(41, "file_not_found"), (42, "is_directory"), (43, "permission_denied"),
                                            (1, "permission_denied"), (None, "permission_denied")])
def test_kubernetes_per_file_errors(streams, code, error):
    _, responses = streams
    workspace = KubernetesWorkspace(settings(), FakeCore(), "files", "job")
    workspace.started = True
    responses.extend([FakeWS(code=code), FakeWS(code=0)])
    result = workspace.download_files(["/workspace/one", "/workspace/two"])
    assert result[0].error == error
    assert result[1].content == b""
    for path in ("relative", "/etc/passwd", "/workspace-other/a", "/workspace/../bad", "/workspace/\x00bad"):
        assert workspace.upload_files([(path, b"x")])[0].error
        assert workspace.download_files([path])[0].error


def test_kubernetes_large_edit_stays_in_workspace(streams):
    calls, responses = streams
    workspace = KubernetesWorkspace(settings(), FakeCore(), "edit", "job")
    workspace.started = True
    responses.extend([FakeWS(), FakeWS(), FakeWS('{"count": 1}'), FakeWS()])
    assert workspace.edit("/workspace/repos/x", "a" * 60_000, "b").occurrences == 1
    uploads = [kwargs["command"] for _, kwargs in calls if kwargs["stdin"]]
    assert len(uploads) == 2
    assert all(command[4].startswith(CREDENTIALS_DIR + "/edit-") for command in uploads)


def test_kubernetes_async_large_edit(streams):
    _, responses = streams
    workspace = KubernetesWorkspace(settings(), FakeCore(), "edit", "job")
    workspace.started = True
    responses.extend([FakeWS(), FakeWS(), FakeWS('{"count": 1}'), FakeWS()])
    assert asyncio.run(workspace.aedit("/workspace/repos/x", "a" * 60_000, "b")).occurrences == 1
    assert isinstance(workspace, BaseSandbox)


def test_kubernetes_close_ignores_missing_pod(monkeypatch):
    core = FakeCore()

    def delete(*args, **kwargs):
        assert kwargs["grace_period_seconds"] == 0
        raise ApiException(status=404)

    monkeypatch.setattr(core, "delete_namespaced_pod", delete)
    workspace = KubernetesWorkspace(settings(), core, "close", "job")
    workspace.started = True
    workspace.close()
    assert not workspace.started


@pytest.mark.parametrize("env", [
    {"OPS_WORKSPACE_MODE": "bad"},
    {"OPS_WORKSPACE_MODE": "kubernetes"},
    {"OPS_WORKSPACE_TTL_SECONDS": "abc"},
    {"OPS_WORKSPACE_TTL_SECONDS": "0"},
    {"OPS_WORKSPACE_COMMAND_TIMEOUT": "-1"},
    {"OPS_WORKSPACE_MAX_COMMAND_TIMEOUT": "1.2"},
    {"OPS_WORKSPACE_MAX_OUTPUT_BYTES": "0"},
    {"OPS_WORKSPACE_COMMAND_TIMEOUT": "400", "OPS_WORKSPACE_MAX_COMMAND_TIMEOUT": "300"},
    {"OPS_WORKSPACE_MODE": "kubernetes", "OPS_WORKSPACE_IMAGE": "toolbox:latest", "OPS_WORKSPACE_NAMESPACE": "ops"},
])
def test_settings_validation(env):
    with pytest.raises(ValidationFailed):
        WorkspaceSettings.from_env(env)


def test_settings_defaults_empty_values_and_frozen(monkeypatch):
    assert WorkspaceSettings.from_env({}) == WorkspaceSettings()
    assert WorkspaceSettings.from_env({"OPS_WORKSPACE_MODE": "", "OPS_WORKSPACE_TTL_SECONDS": ""}) == WorkspaceSettings()
    assert WorkspaceSettings().namespace is None
    assert WorkspaceSettings().local_root == Path("/tmp/ops-agent-workspaces")
    monkeypatch.setenv("OPS_WORKSPACE_MODE", "local")
    assert WorkspaceSettings.from_env().mode == "local"
    with pytest.raises(FrozenInstanceError):
        WorkspaceSettings().mode = "local"


def test_settings_all_environment_fields(tmp_path):
    env = {f"OPS_WORKSPACE_{name}": value for name, value in {
        "MODE": "kubernetes", "NAMESPACE": "custom", "IMAGE": IMAGE, "SERVICE_ACCOUNT": "account",
        "IMAGE_PULL_SECRET": "pull", "CPU_REQUEST": "500m", "MEMORY_REQUEST": "1Gi", "MEMORY_LIMIT": "3Gi",
        "STORAGE_LIMIT": "9Gi", "TTL_SECONDS": "100", "COMMAND_TIMEOUT": "2", "MAX_COMMAND_TIMEOUT": "10",
        "MAX_OUTPUT_BYTES": "32", "AZURE_CLIENT_ID": "client", "AZURE_TENANT_ID": "tenant",
        "AZURE_SUBSCRIPTION_ID": "sub", "LOCAL_ROOT": str(tmp_path),
    }.items()}
    result = WorkspaceSettings.from_env(env)
    for name in result.__dataclass_fields__:
        value = getattr(result, name)
        assert str(value) == env[f"OPS_WORKSPACE_{name.upper()}"]


def test_settings_namespace_resolution(monkeypatch, tmp_path):
    import tools.workspace as module

    namespace = tmp_path / "namespace"
    monkeypatch.setattr(module, "_NAMESPACE_FILE", namespace)
    with pytest.raises(ValidationFailed, match="OPS_WORKSPACE_NAMESPACE"):
        WorkspaceSettings.from_env({"OPS_WORKSPACE_MODE": "kubernetes", "OPS_WORKSPACE_IMAGE": IMAGE})
    namespace.write_text("current-pod-namespace\n")
    assert WorkspaceSettings(mode="kubernetes", image=IMAGE).namespace == "current-pod-namespace"
    namespace.write_text("")
    with pytest.raises(ValidationFailed, match="leer"):
        WorkspaceSettings(mode="kubernetes", image=IMAGE)


def test_factory_modes_and_injected_core(tmp_path):
    assert create_workspace(WorkspaceSettings(), "op", "job") is None
    assert isinstance(create_workspace(WorkspaceSettings(mode="local", local_root=tmp_path), "op", "job"), LocalWorkspace)
    core = FakeCore()
    workspace = create_workspace(settings(), "op", "job", lambda: core)
    assert workspace.core is core
    assert not workspace.started


def test_factory_requires_explicit_local_context(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("OPS_KUBE_CONTEXT", raising=False)
    with pytest.raises(ValidationFailed, match="OPS_KUBE_CONTEXT"):
        create_workspace(settings(), "op", "job")


@pytest.mark.parametrize("incluster", [False, True])
def test_factory_configuration(monkeypatch, incluster):
    import tools.workspace as module

    calls = []
    core = FakeCore()
    monkeypatch.setattr(module.config, "load_incluster_config", lambda: calls.append("cluster"))
    monkeypatch.setattr(module.config, "load_kube_config", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(module.client, "CoreV1Api", lambda: core)
    monkeypatch.setenv("OPS_KUBE_CONTEXT", "explicit")
    monkeypatch.setenv("OPS_KUBECONFIG", "/custom/config")
    if incluster:
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "cluster")
    else:
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    assert create_workspace(settings(), "op", "job").core is core
    assert calls == (["cluster"] if incluster else [{"config_file": "/custom/config", "context": "explicit"}])


def test_exec_uses_own_api_client():
    """Exec patcht den API-Client; normale Aufrufe (lesen, löschen) dürfen ihn nie teilen."""
    from kubernetes import client as k8s

    core = k8s.CoreV1Api(k8s.ApiClient(k8s.Configuration()))
    ws = KubernetesWorkspace(
        WorkspaceSettings(mode="kubernetes", namespace="ns", image="r/t@sha256:" + "a" * 64), core, "op-1", "job-1"
    )
    assert ws._exec_core is not core and ws._exec_core.api_client is not core.api_client
