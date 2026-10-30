"""Arbeitsverzeichnisse pro Operation, im Workspace-Pod oder lokal.

Deep Agents 0.7.32 benötigt für ls/read/write/edit/glob ``python3`` samt
Standardbibliothek, für grep GNU ``grep`` und bei Trefferlimits ``head``.
``rg`` ist optional; delete und die Edit-Bereinigung verwenden ``test``/``rm``.
``find``, ``base64`` und ``wc`` werden von diesen Basishilfen nicht aufgerufen.
Unser Transport braucht bash, sleep, timeout, mkdir, dirname, chmod, head,
base64 und rm sowie python3 zur Prüfung auf Symlinks außerhalb /workspace.
Capture-Offload bleibt aus. Dessen POSIX-Shell-Wrapper benötigt zusätzlich
cat, tail, wc und tr sowie ein beschreibbares Verzeichnis für Ausgabedateien.

Lokal bildet virtual_mode mit angepasstem Präfix /workspace auf root ab.
Shell-Befehle übersetzen literale /workspace-Pfade; dynamisch zusammengesetzte
absolute Pfade benötigen shell_path(). Das ist keine lokale Prozessisolation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

import kubernetes.stream
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox, _build_edit_tmpfile_cmd, _parse_edit_output
from kubernetes import client, config

from tools.common import Conflict, NotFound, TimedOut, ToolError, UpstreamError, ValidationFailed, redact, register_secret
from tools.kubernetes import _call

WORKDIR = "/workspace"
REPOS_DIR = "/workspace/repos"
CREDENTIALS_DIR = "/workspace/.ops"
_NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
# ``kubernetes.stream.stream`` ersetzt kurzzeitig ``api_client.request`` des übergebenen Clients. Parallele Werkzeugaufrufe
# des SDK würden sich dabei gegenseitig den Patch zurücksetzen; deshalb eigener Client für Exec und Aufbau unter Sperre.
_STREAM_LOCK = threading.Lock()
_TIMEOUT_NOTE = "\nZeitüberschreitung: Der Befehl hat das Zeitlimit erreicht."
_TRUNCATION = b"\n... [Ausgabe gekuerzt] ...\n"


@dataclass(frozen=True)
class WorkspaceSettings:
    mode: Literal["kubernetes", "local", "off"] = "off"
    namespace: str | None = None
    image: str | None = None
    service_account: str = "operations-agent-workspace"
    image_pull_secret: str | None = None
    cpu_request: str = "250m"
    memory_request: str = "512Mi"
    memory_limit: str = "2Gi"
    storage_limit: str = "5Gi"
    ttl_seconds: int = 3600
    command_timeout: int = 300
    max_command_timeout: int = 1800
    max_output_bytes: int = 100_000
    azure_client_id: str | None = None
    azure_tenant_id: str | None = None
    azure_subscription_id: str | None = None
    local_root: Path = Path("/tmp/ops-agent-workspaces")

    def __post_init__(self) -> None:
        if self.mode not in ("kubernetes", "local", "off"):
            raise ValidationFailed("OPS_WORKSPACE_MODE muss kubernetes, local oder off sein")
        for name in ("ttl_seconds", "command_timeout", "max_command_timeout", "max_output_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValidationFailed(f"OPS_WORKSPACE_{name.upper()} muss eine positive Ganzzahl sein")
        if self.command_timeout > self.max_command_timeout:
            raise ValidationFailed("command_timeout darf max_command_timeout nicht überschreiten")
        if self.mode == "kubernetes":
            if not self.image or not re.fullmatch(r"\S+@sha256:[a-fA-F0-9]{64}", self.image):
                raise ValidationFailed("OPS_WORKSPACE_IMAGE muss ein vollständiges Image mit @sha256-Digest sein")
            if self.namespace is None:
                try:
                    namespace = _NAMESPACE_FILE.read_text().strip()
                except OSError as exc:
                    raise ValidationFailed(
                        "Workspace-Namespace fehlt: OPS_WORKSPACE_NAMESPACE setzen oder ServiceAccount-Namespace-Datei bereitstellen"
                    ) from exc
                if not namespace:
                    raise ValidationFailed("Die ServiceAccount-Namespace-Datei ist leer")
                object.__setattr__(self, "namespace", namespace)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkspaceSettings:
        values = os.environ if env is None else env
        kwargs: dict[str, Any] = {}
        for field in fields(cls):
            value = values.get(f"OPS_WORKSPACE_{field.name.upper()}")
            if not value:
                continue
            if field.name in ("ttl_seconds", "command_timeout", "max_command_timeout", "max_output_bytes"):
                try:
                    kwargs[field.name] = int(value)
                except ValueError as exc:
                    raise ValidationFailed(f"OPS_WORKSPACE_{field.name.upper()} muss eine positive Ganzzahl sein") from exc
            else:
                kwargs[field.name] = Path(value) if field.name == "local_root" else value
        return cls(**kwargs)


def _safe_id(value: str, *, pod: bool = False) -> str:
    digest = hashlib.sha1(value.encode()).hexdigest()[:8]
    safe = re.sub(r"[^a-z0-9-]+" if pod else r"[^A-Za-z0-9_.-]+", "-", value.lower() if pod else value)
    safe = safe.strip("-_.") or digest
    limit = 63 - len("ops-ws-") if pod else 63
    if len(safe) > limit:
        safe = safe[: limit - 9].rstrip("-_.") + "-" + digest
    return safe


def _timeout(settings: WorkspaceSettings, timeout: int | None) -> int:
    return max(1, min(timeout or settings.command_timeout, settings.max_command_timeout))


class _Capture:
    """Begrenzt den Speicher auch bei endloser Ausgabe auf Anfang und Ende."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.head = b""
        self.tail = b""
        self.total = 0

    def add(self, text: str) -> None:
        data = text.encode("utf-8")
        self.total += len(data)
        remaining = self.limit - len(self.head)
        self.head += data[:remaining]
        self.tail = (self.tail + data[remaining:])[-self.limit :]

    def response(self, exit_code: int | None, note: str = "") -> ExecuteResponse:
        raw = self.head + self.tail
        output = redact(raw.decode("utf-8", errors="replace") + (_TIMEOUT_NOTE if exit_code == 124 else "") + note)
        data = output.encode("utf-8")
        truncated = self.total > self.limit or len(data) > self.limit
        if truncated:
            marker = _TRUNCATION[: self.limit]
            budget = self.limit - len(marker)
            head = (budget + 1) // 2
            tail = budget // 2
            output = data[:head].decode("utf-8", errors="ignore") + marker.decode() + (
                data[-tail:].decode("utf-8", errors="ignore") if tail else ""
            )
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)


def _validate_path(path: str) -> str | None:
    if not path or "\x00" in path or not path.startswith("/"):
        return "invalid_path"
    parsed = PurePosixPath(path)
    if ".." in parsed.parts or not parsed.is_relative_to(WORKDIR):
        return "permission_denied"
    return None


def _secret_path(name: str, content: str) -> str:
    if ".." in name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValidationFailed("Secret-Dateiname muss ein einfacher Name ohne Slash oder '..' sein")
    register_secret(content)
    return f"{CREDENTIALS_DIR}/{name}"


def _local_command(command: str, root: Path) -> str:
    """Ersetzt den Pfadpräfix unter Beachtung einfacher Shell-Anführungszeichen."""
    prefix = re.compile(r"/workspace(?=/|[^A-Za-z0-9_.-]|$)")
    parts = []
    quote = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if not escaped and prefix.match(command, index):
            replacement = str(root)
            if quote == "'":
                replacement = replacement.replace("'", "'\"'\"'")
            elif quote == '"':
                for symbol in ('\\', '"', '$', '`'):
                    replacement = replacement.replace(symbol, "\\" + symbol)
            else:
                replacement = shlex.quote(replacement)
            parts.append(replacement)
            index += len(WORKDIR)
            continue
        parts.append(char)
        if quote == "'":
            if char == "'":
                quote = None
        elif escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        index += 1
    return "".join(parts)


# Prüft auch Symlink-Eltern; reine Pfadnormalisierung würde aus /workspace führen.
_PATH_CHECK = """import os, sys
p = os.path.realpath(sys.argv[1])
if os.path.commonpath(['/workspace', p]) != '/workspace':
    sys.exit(43)
"""


class KubernetesWorkspace(BaseSandbox):
    """Persistenter Workspace-Pod für genau eine Operation."""

    enable_capture_offload = False
    repos_dir = REPOS_DIR
    credentials_dir = CREDENTIALS_DIR

    def __init__(
        self,
        settings: WorkspaceSettings,
        core: client.CoreV1Api,
        operation_id: str,
        job_id: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        start_timeout: float = 300,
    ) -> None:
        self.settings = settings
        self.core = core
        api_client = getattr(core, "api_client", None)
        self._exec_core = (
            client.CoreV1Api(client.ApiClient(api_client.configuration)) if isinstance(api_client, client.ApiClient) else core
        )
        self.operation_id = operation_id
        self.job_id = job_id
        self.pod_name = "ops-ws-" + _safe_id(operation_id, pod=True)
        self.started = False
        self._sleep = sleep
        self._clock = clock
        self._start_timeout = start_timeout

    @property
    def id(self) -> str:
        return self.pod_name

    def shell_path(self, model_path: str) -> str:
        if _validate_path(model_path):
            raise ValidationFailed("Pfad muss innerhalb /workspace liegen")
        return model_path

    def _pod_spec(self) -> dict[str, Any]:
        s = self.settings
        labels = {
            "app.kubernetes.io/name": "operations-agent-workspace",
            "app.kubernetes.io/part-of": "operations-agent",
            "ops.mmk/operation-id": _safe_id(self.operation_id),
            "ops.mmk/job-id": _safe_id(self.job_id),
        }
        env = {"HOME": "/workspace/.home", "OPS_OPERATION_ID": self.operation_id, "OPS_JOB_ID": self.job_id, "GIT_TERMINAL_PROMPT": "0"}
        for name in ("azure_client_id", "azure_tenant_id", "azure_subscription_id"):
            if value := getattr(s, name):
                env[name.upper()] = value
        if s.azure_client_id:
            labels["azure.workload.identity/use"] = "true"
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": self.pod_name,
                "labels": labels,
                "annotations": {"ops.mmk/operation-id": self.operation_id, "ops.mmk/job-id": self.job_id},
            },
            "spec": {
                "restartPolicy": "Never",
                "activeDeadlineSeconds": s.ttl_seconds,
                "serviceAccountName": s.service_account,
                "automountServiceAccountToken": True,
                "imagePullSecrets": [{"name": s.image_pull_secret}] if s.image_pull_secret else [],
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001},
                "containers": [{
                    "name": "workspace",
                    "image": s.image,
                    "command": ["sleep", str(s.ttl_seconds)],
                    "workingDir": WORKDIR,
                    "env": [{"name": key, "value": value} for key, value in env.items()],
                    "resources": {
                        "requests": {"cpu": s.cpu_request, "memory": s.memory_request},
                        "limits": {"memory": s.memory_limit, "ephemeral-storage": s.storage_limit},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumeMounts": [{"name": "workspace", "mountPath": WORKDIR}, {"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [{"name": "workspace", "emptyDir": {"sizeLimit": s.storage_limit}}, {"name": "tmp", "emptyDir": {}}],
            },
        }

    def _read_pod(self) -> Any | None:
        try:
            return _call("Workspace-Pod lesen", lambda: self.core.read_namespaced_pod(
                self.pod_name, self.settings.namespace, _request_timeout=10,
            ))
        except NotFound:
            return None

    def _reason(self, pod: Any | None) -> str:
        status = getattr(pod, "status", None)
        reasons = [getattr(status, "phase", None), getattr(status, "reason", None), getattr(status, "message", None)]
        for container in getattr(status, "container_statuses", None) or []:
            state = getattr(container, "state", None)
            for kind in ("waiting", "terminated"):
                detail = getattr(state, kind, None)
                reasons.extend([getattr(detail, "reason", None), getattr(detail, "message", None)])
        try:
            events = _call("Workspace-Events lesen", lambda: self.core.list_namespaced_event(
                self.settings.namespace, field_selector=f"involvedObject.name={self.pod_name}", _request_timeout=10,
            ))
            reasons.extend(f"{event.reason}: {event.message}" for event in events.items[-5:])
        except (ToolError, AttributeError):
            pass
        return redact("; ".join(str(reason) for reason in reasons if reason)) or "Pod fehlt"

    def start(self) -> None:
        if self.started:
            return
        deadline = self._clock() + self._start_timeout
        pod = self._read_pod()
        if pod and (pod.metadata.deletion_timestamp or pod.status.phase in ("Failed", "Succeeded")):
            if not pod.metadata.deletion_timestamp:
                self.close()
            while pod is not None:
                if self._clock() >= deadline:
                    raise TimedOut(f"Workspace-Pod konnte nicht gelöscht werden: {self._reason(pod)}")
                self._sleep(1)
                pod = self._read_pod()
        while True:
            if pod is None:
                try:
                    _call("Workspace-Pod anlegen", lambda: self.core.create_namespaced_pod(
                        self.settings.namespace, self._pod_spec(), _request_timeout=10,
                    ))
                except Conflict:
                    pass  # Ein anderer Worker kann denselben Pod gerade angelegt haben.
                pod = self._read_pod()
            if pod and pod.status.phase == "Running" and any(
                item.name == "workspace" and item.ready for item in (pod.status.container_statuses or [])
            ) and not pod.metadata.deletion_timestamp:
                break
            if self._clock() >= deadline:
                raise TimedOut(f"Workspace-Pod nicht bereit: {self._reason(pod)}")
            self._sleep(1)
            pod = self._read_pod()
        self.started = True  # execute darf start während der Initialisierung nicht erneut aufrufen.
        try:
            result = self.execute(f"mkdir -p {REPOS_DIR} {CREDENTIALS_DIR} {WORKDIR}/.home && chmod 700 {CREDENTIALS_DIR}")
            if result.exit_code != 0:
                raise UpstreamError(f"Workspace-Verzeichnisse nicht angelegt: {result.output}")
        except Exception:
            self.started = False
            raise

    def _collect(
        self, resp: Any, timeout: int, *, stdin: str | None = None, capture: _Capture | None = None,
    ) -> tuple[str, int | None, bool]:
        deadline = self._clock() + timeout + 15
        chunks: list[str] = []

        def drain() -> None:
            for text in (resp.read_stdout(timeout=0), resp.read_stderr(timeout=0)):
                if capture is None:
                    chunks.append(text)
                else:
                    capture.add(text)

        try:
            if stdin is not None:
                for offset in range(0, len(stdin), 16_384):
                    if self._clock() > deadline:
                        return "", None, True
                    resp.write_stdin(stdin[offset : offset + 16_384])
            while resp.is_open():
                if self._clock() > deadline:
                    return "".join(chunks), None, True
                resp.update(timeout=1)
                drain()
            drain()
            try:
                code = resp.returncode
            except (KeyError, TypeError, ValueError):
                code = None
            return "".join(chunks), code if isinstance(code, int) else None, False
        finally:
            resp.close()

    def _exec(
        self, command: list[str], *, timeout: int, stdin: str | None = None, capture: _Capture | None = None,
    ) -> tuple[str, int | None, bool]:
        def run() -> tuple[str, int | None, bool]:
            with _STREAM_LOCK:
                resp = kubernetes.stream.stream(
                    self._exec_core.connect_get_namespaced_pod_exec, self.pod_name, self.settings.namespace,
                    container="workspace", command=command, stdin=stdin is not None,
                    stdout=True, stderr=True, tty=False, _preload_content=False, _request_timeout=timeout + 15,
                )
            return self._collect(resp, timeout, stdin=stdin, capture=capture)

        try:
            return _call("Workspace-Befehl ausführen", run)
        except NotFound as exc:
            self.started = False
            raise UpstreamError("Workspace verloren: Der Pod fehlt; nicht committete Änderungen sind verloren.") from exc

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.start()
        effective = _timeout(self.settings, timeout)
        capture = _Capture(self.settings.max_output_bytes)
        _, code, expired = self._exec(
            ["bash", "-lc", f"cd {WORKDIR} && timeout -k 5 {effective} bash -lc {shlex.quote(command)} 2>&1"],
            timeout=effective, capture=capture,
        )
        return capture.response(code, "\nZeitüberschreitung beim Empfang; Exit-Code unbekannt." if expired else "")

    def _file_exec(self, path: str, script: str, *args: str, stdin: str | None = None) -> tuple[str, int | None, bool]:
        self.start()
        # Exit-Codes 41/42/43 unterscheiden Dateifehler ohne sprachabhängige stderr-Auswertung.
        check = f"python3 -c {shlex.quote(_PATH_CHECK)} \"$1\" || exit 43; "
        return self._exec(["bash", "-c", check + script, "_", path, *args], timeout=self.settings.command_timeout, stdin=stdin)

    @staticmethod
    def _file_error(code: int | None) -> str | None:
        return {0: None, 41: "file_not_found", 42: "is_directory", 43: "permission_denied"}.get(code, "permission_denied")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Überträgt Base64 über stdin, ohne Nutzdaten in Prozessargumenten.

        Client 37.0.1 schließt stdin nur bei WebSocket v5 mit close_channel(0).
        head liest stattdessen exakt die bekannte Bytezahl, auch bei v4. Damit
        braucht base64 kein EOF vom Client; printf-Fallback und Secret-Argumente
        entfallen. pipefail erkennt auch abgebrochene Transfers.
        """
        responses = []
        for path, content in files:
            error = _validate_path(path)
            if error is None:
                try:
                    encoded = base64.b64encode(content).decode("ascii")
                    _, code, _ = self._file_exec(
                        path,
                        'set -o pipefail; umask 077; [ ! -d "$1" ] || exit 42; '
                        'mkdir -p "$(dirname "$1")" && head -c "$2" | base64 -d > "$1"',
                        str(len(encoded)), stdin=encoded,
                    )
                    error = self._file_error(code)
                except Exception as exc:
                    error = redact(str(exc))
            responses.append(FileUploadResponse(path=path, error=error))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            error = _validate_path(path)
            content = None
            if error is None:
                try:
                    output, code, _ = self._file_exec(
                        path, '[ ! -d "$1" ] || exit 42; [ -e "$1" ] || exit 41; base64 -w0 -- "$1"',
                    )
                    error = self._file_error(code)
                    if error is None:
                        content = base64.b64decode(output, validate=True)
                except Exception as exc:
                    error = redact(str(exc))
            responses.append(FileDownloadResponse(path=path, content=content, error=error))
        return responses

    def write_secret_file(self, relative_name: str, content: str) -> None:
        path = _secret_path(relative_name, content)
        response = self.upload_files([(path, content.encode())])[0]
        if response.error:
            raise UpstreamError(f"Secret-Datei nicht geschrieben: {response.error}")
        result = self.execute(f"chmod 600 {shlex.quote(path)}")
        if result.exit_code != 0:
            raise UpstreamError(f"Secret-Dateirechte nicht gesetzt: {result.output}")

    def _edit_via_upload(self, file_path: str, old_string: str, new_string: str, replace_all: bool) -> EditResult:
        # BaseSandbox nutzt /tmp; Uploads sind hier bewusst auf /workspace begrenzt.
        prefix = f"{CREDENTIALS_DIR}/edit-{uuid4().hex}"
        old_tmp, new_tmp = prefix + "-old", prefix + "-new"
        try:
            for result in self.upload_files([(old_tmp, old_string.encode()), (new_tmp, new_string.encode())]):
                if result.error:
                    return EditResult(error=result.error)
            command = _build_edit_tmpfile_cmd(file_path, old_tmp, new_tmp, replace_all=replace_all)
            return _parse_edit_output(self.execute(command).output, file_path, old_string)
        finally:
            self.execute(f"rm -f {shlex.quote(old_tmp)} {shlex.quote(new_tmp)}")

    async def _aedit_via_upload(self, file_path: str, old_string: str, new_string: str, replace_all: bool) -> EditResult:
        return await asyncio.to_thread(self._edit_via_upload, file_path, old_string, new_string, replace_all)

    def close(self) -> None:
        try:
            _call("Workspace-Pod löschen", lambda: self.core.delete_namespaced_pod(
                self.pod_name, self.settings.namespace, grace_period_seconds=0, _request_timeout=10,
            ))
        except NotFound:
            pass
        self.started = False


class LocalWorkspace(LocalShellBackend):
    """Lokaler Entwicklungsmodus mit denselben sichtbaren Dateipfaden wie im Pod."""

    enable_capture_offload = False
    repos_dir = REPOS_DIR
    credentials_dir = CREDENTIALS_DIR

    def __init__(self, settings: WorkspaceSettings, operation_id: str, job_id: str) -> None:
        self.settings = settings
        self.root = (settings.local_root / _safe_id(operation_id, pod=True)).resolve()
        self.operation_id = operation_id
        self.job_id = job_id
        self.started = False
        super().__init__(
            root_dir=self.root, virtual_mode=True, timeout=settings.command_timeout,
            max_output_bytes=settings.max_output_bytes, inherit_env=False,
            env={"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.root / ".home"),
                 "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"},
        )

    @property
    def id(self) -> str:
        return f"local-{self.root.name}"

    def start(self) -> None:
        if self.started:
            return
        for name in ("repos", ".ops", ".home"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / ".ops").chmod(0o700)
        self.started = True

    def _resolve_path(self, key: str) -> Path:
        self.start()
        if key == ".":  # FilesystemBackend nutzt dies intern als Standard-Suchwurzel.
            return self.root
        if _validate_path(key):
            raise ValueError("Pfad muss absolut und innerhalb /workspace liegen")
        return super()._resolve_path(str(PurePosixPath(key).relative_to(WORKDIR)))

    def _to_virtual_path(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.root).as_posix()
        return WORKDIR if relative == "." else f"{WORKDIR}/{relative}"

    def _real_path(self, key: str) -> str:
        return self._to_virtual_path(self._resolve_path(key))

    def shell_path(self, model_path: str) -> str:
        return str(self._resolve_path(model_path))

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.start()
        effective = _timeout(self.settings, timeout)
        # Nur literale Präfixe ersetzen; /workspace-other bleibt unverändert.
        translated = _local_command(command, self.root)
        capture = _Capture(self.settings.max_output_bytes)
        # Eigener Prozessaufruf: LocalShellBackend kürzt nur den Anfang und
        # beendet bei Timeout nicht die gesamte Prozessgruppe.
        with subprocess.Popen(
            ["bash", "-c", translated], cwd=self.root, env=self._env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        ) as process:
            try:
                output, _ = process.communicate(timeout=effective)
                code = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                code = 124
        capture.add(output.decode("utf-8", errors="replace"))
        # Sichtbare Pfade auch bei pwd, HOME und Fehlerausgaben beibehalten.
        capture.head = capture.head.replace(str(self.root).encode(), WORKDIR.encode())
        capture.tail = capture.tail.replace(str(self.root).encode(), WORKDIR.encode())
        return capture.response(code)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self.start()
        return super().upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.start()
        return super().download_files(paths)

    def write_secret_file(self, relative_name: str, content: str) -> None:
        path = _secret_path(relative_name, content)
        self.start()
        target = self._resolve_path(path)
        # Schon beim Öffnen 0600, auch wenn die Datei vorher weiter lesbar war.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content.encode())

    def close(self) -> None:
        if os.environ.get("OPS_WORKSPACE_KEEP") != "1":
            shutil.rmtree(self.root, ignore_errors=True)
        self.started = False


def create_workspace(
    settings: WorkspaceSettings,
    operation_id: str,
    job_id: str,
    core_factory: Callable[[], client.CoreV1Api] | None = None,
) -> KubernetesWorkspace | LocalWorkspace | None:
    if settings.mode == "off":
        return None
    if settings.mode == "local":
        return LocalWorkspace(settings, operation_id, job_id)
    if core_factory is None:
        # KubernetesAdapter.from_environment ist an TargetPolicy gebunden.
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
        else:
            context = os.environ.get("OPS_KUBE_CONTEXT")
            if not context:
                raise ValidationFailed("OPS_KUBE_CONTEXT muss lokal gesetzt sein; der Standardkontext wird nie verwendet")
            config_file = os.environ.get("OPS_KUBECONFIG") or os.environ.get("KUBECONFIG") or None
            config.load_kube_config(config_file=config_file, context=context)
        core_factory = client.CoreV1Api
    return KubernetesWorkspace(settings, core_factory(), operation_id, job_id)
