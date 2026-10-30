"""Simulierte Gegenstellen für Adapter- und Ausführungstests (Design D, Entscheidung 10)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from urllib3.exceptions import ReadTimeoutError

from contracts import ActionRecord, LockAcquisition, OwnershipLost


def now() -> datetime:
    return datetime.now(UTC)


# Kubernetes


@dataclass
class FakeDeployment:
    replicas: int
    ready: int
    digest: str = "sha256:" + "a" * 64
    waiting: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    generation: int = 1
    restarts: int = 0


class FakeCluster:
    def __init__(self) -> None:
        self.deployments: dict[tuple[str, str], FakeDeployment] = {}
        self.events: dict[str, list[tuple[str, str]]] = {}
        self.logs: dict[str, str] = {}
        self.ns_annotations: dict[str, dict[str, str]] = {}
        self.jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.forbid: set[tuple[str, str]] = set()
        self.timeouts: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []
        self.smoke: Callable[[FakeCluster], tuple[int, str] | None] = lambda c: (0, '{"success": true, "startedAt": "x", "finishedAt": "y", "evidence": {}}')
        self.on_patch: Callable[[FakeCluster, str, str], None] = lambda c, ns, name: None
        self.apps = _Apps(self)
        self.core = _Core(self)
        self.batch = _Batch(self)

    def add(self, ns: str, name: str, dep: FakeDeployment, release: dict[str, str] | None = None) -> None:
        self.deployments[(ns, name)] = dep
        self.ns_annotations[ns] = release or {
            "ops.mmk/release-git-sha": "abc123",
            "ops.mmk/release-image-digest": dep.digest,
            "ops.mmk/release-chart-version": "1.0.0",
        }

    def gate(self, op: str, ns: str) -> None:
        self.calls.append((op, ns))
        if (op, ns) in self.forbid:
            raise ApiException(status=403, reason="Forbidden")
        if (op, ns) in self.timeouts:
            raise ReadTimeoutError(None, "/", "read timed out")

    def namespaces_touched(self) -> set[str]:
        return {ns for _, ns in self.calls}


def _deployment(name: str, ns: str, d: FakeDeployment) -> client.V1Deployment:
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name=name, namespace=ns, generation=d.generation),
        spec=client.V1DeploymentSpec(
            replicas=d.replicas,
            selector=client.V1LabelSelector(match_labels={"app": name}),
            template=client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(annotations=dict(d.annotations))),
        ),
        status=client.V1DeploymentStatus(
            observed_generation=d.generation,
            replicas=d.replicas,
            updated_replicas=d.ready,
            available_replicas=d.ready,
            ready_replicas=d.ready,
            conditions=[],
        ),
    )


class _Apps:
    def __init__(self, c: FakeCluster) -> None:
        self.c = c

    def _get(self, name: str, ns: str) -> FakeDeployment:
        if (ns, name) not in self.c.deployments:
            raise ApiException(status=404, reason="NotFound")
        return self.c.deployments[(ns, name)]

    def read_namespaced_deployment(self, name: str, ns: str, **_: Any) -> client.V1Deployment:
        self.c.gate("read_deployment", ns)
        return _deployment(name, ns, self._get(name, ns))

    def patch_namespaced_deployment(self, name: str, ns: str, body: dict, **_: Any) -> client.V1Deployment:
        self.c.gate("patch_deployment", ns)
        d = self._get(name, ns)
        d.annotations.update(body["spec"]["template"]["metadata"]["annotations"])
        d.generation += 1
        self.c.on_patch(self.c, ns, name)
        return _deployment(name, ns, d)


class _Core:
    def __init__(self, c: FakeCluster) -> None:
        self.c = c

    def list_namespaced_pod(self, ns: str, label_selector: str = "", **_: Any) -> client.V1PodList:
        self.c.gate("list_pods", ns)
        if label_selector.startswith("job-name="):
            job = self.c.jobs.get((ns, label_selector.split("=", 1)[1]))
            if not job or job["result"] is None:
                return client.V1PodList(items=[])
            exit_code, _ = job["result"]
            return client.V1PodList(
                items=[
                    client.V1Pod(
                        metadata=client.V1ObjectMeta(name=job["name"] + "-pod"),
                        status=client.V1PodStatus(
                            container_statuses=[
                                client.V1ContainerStatus(
                                    name="smoke",
                                    image="smoke",
                                    image_id="smoke",
                                    ready=False,
                                    restart_count=0,
                                    state=client.V1ContainerState(terminated=client.V1ContainerStateTerminated(exit_code=exit_code)),
                                )
                            ]
                        ),
                    )
                ]
            )
        name = label_selector.split("=", 1)[1]
        d = self.c.deployments[(ns, name)]
        pods = []
        for i in range(max(d.replicas, 1 if d.waiting else 0)):
            waiting = client.V1ContainerStateWaiting(reason=d.waiting) if d.waiting else None
            pods.append(
                client.V1Pod(
                    metadata=client.V1ObjectMeta(name=f"{name}-{i}"),
                    status=client.V1PodStatus(
                        phase="Running" if not d.waiting else "Pending",
                        container_statuses=[
                            client.V1ContainerStatus(
                                name=name,
                                image=name,
                                image_id=f"registry/{name}@{d.digest}",
                                ready=not d.waiting,
                                restart_count=d.restarts,
                                state=client.V1ContainerState(waiting=waiting),
                            )
                        ],
                    ),
                )
            )
        return client.V1PodList(items=pods)

    def list_namespaced_event(self, ns: str, **_: Any) -> client.CoreV1EventList:
        self.c.gate("list_events", ns)
        items = [
            client.CoreV1Event(
                metadata=client.V1ObjectMeta(name=f"e{i}", creation_timestamp=now()),
                involved_object=client.V1ObjectReference(kind="Pod", name="x"),
                reason=reason,
                message=msg,
                type="Warning",
                last_timestamp=now(),
                count=1,
            )
            for i, (reason, msg) in enumerate(self.c.events.get(ns, []))
        ]
        return client.CoreV1EventList(items=items)

    def read_namespaced_pod_log(self, name: str, ns: str, **_: Any) -> str:
        self.c.gate("read_logs", ns)
        for (jns, jname), job in self.c.jobs.items():
            if jns == ns and name == jname + "-pod":
                return job["result"][1]
        return self.c.logs.get(ns, "")

    def read_namespace(self, ns: str, **_: Any) -> client.V1Namespace:
        self.c.gate("read_namespace", ns)
        return client.V1Namespace(metadata=client.V1ObjectMeta(name=ns, annotations=self.c.ns_annotations.get(ns, {})))


class _Batch:
    def __init__(self, c: FakeCluster) -> None:
        self.c = c

    def create_namespaced_job(self, ns: str, body: client.V1Job, **_: Any) -> None:
        self.c.gate("create_job", ns)
        self.c.jobs[(ns, body.metadata.name)] = {"name": body.metadata.name, "body": body, "result": self.c.smoke(self.c), "deleted": False}

    def read_namespaced_job(self, name: str, ns: str, **_: Any) -> client.V1Job:
        self.c.gate("read_job", ns)
        job = self.c.jobs[(ns, name)]
        res = job["result"]
        status = client.V1JobStatus(
            succeeded=1 if res and res[0] == 0 else None,
            failed=1 if res and res[0] != 0 else None,
        )
        return client.V1Job(metadata=client.V1ObjectMeta(name=name), status=status)

    def delete_namespaced_job(self, name: str, ns: str, **_: Any) -> None:
        self.c.gate("delete_job", ns)
        self.c.jobs[(ns, name)]["deleted"] = True


# Strang-C-Dienste (vertragskonforme Testausführung)


class MemoryActions:
    def __init__(self) -> None:
        self.items: dict[str, ActionRecord] = {}
        self._ids = itertools.count(1)

    def record_planned(self, operation_id: str, kind: str, target: str, correlation: dict[str, str]) -> ActionRecord:
        aid = f"act-{next(self._ids):04d}"
        rec = ActionRecord(action_id=aid, operation_id=operation_id, kind=kind, target=target, status="planned", correlation=correlation, created_at=now())
        self.items[aid] = rec
        return rec

    def update(self, action_id: str, status: str, external_refs: dict[str, str] | None = None) -> ActionRecord:
        rec = self.items[action_id]
        rec = rec.model_copy(update={"status": status, "external_refs": {**rec.external_refs, **(external_refs or {})}, "updated_at": now()})
        self.items[action_id] = rec
        return rec

    def get(self, action_id: str) -> ActionRecord | None:
        return self.items.get(action_id)

    def list_for_operation(self, operation_id: str) -> list[ActionRecord]:
        return [a for a in self.items.values() if a.operation_id == operation_id]

    def of_kind(self, kind: str) -> list[ActionRecord]:
        return [a for a in self.items.values() if a.kind == kind]


class MemoryLock:
    def __init__(self) -> None:
        self.owners: dict[tuple[str, str], str] = {}
        self.acquire_calls = 0

    def try_acquire(self, app_id: str, environment: str, operation_id: str) -> LockAcquisition:
        self.acquire_calls += 1
        holder = self.owners.get((app_id, environment))
        if holder and holder != operation_id:
            return LockAcquisition(False, holder)
        self.owners[(app_id, environment)] = operation_id
        return LockAcquisition(True, operation_id)

    def holder(self, app_id: str, environment: str) -> str | None:
        return self.owners.get((app_id, environment))

    def holds(self, app_id: str, environment: str, operation_id: str) -> bool:
        return self.owners.get((app_id, environment)) == operation_id

    def release(self, app_id: str, environment: str, operation_id: str) -> None:
        if self.holds(app_id, environment, operation_id):
            del self.owners[(app_id, environment)]


class RecordingState:
    def __init__(self, reject: set[str] | None = None) -> None:
        self.requests: list[tuple[str, str]] = []
        self.reject = reject or set()

    def request(self, job_id: str, state: str, reason: str) -> bool:
        self.requests.append((state, reason))
        return state not in self.reject


class Ownership:
    def __init__(self) -> None:
        self.valid = True

    def assert_valid(self) -> None:
        if not self.valid:
            raise OwnershipLost("Lease abgelaufen")


class NoCheckpoints:
    def __init__(self, saver: Any = None) -> None:
        self.saver = saver

    def checkpointer(self) -> Any:
        return self.saver


# Modell


class ScriptedModel(BaseChatModel):
    """Echtes LangChain-Modellinterface mit vorgegebenen Antworten (nur für Pfadtests)."""

    responder: Callable[[list[BaseMessage], int], AIMessage]
    _calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> int:
        return self._calls

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        msg = self.responder(messages, self._calls)
        self._calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name: str, args: dict[str, Any] | None = None, n: int = 0, usage: tuple[int, int] = (1000, 200)) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": f"call-{name}-{n}"}],
        usage_metadata={"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage)},
    )


def say(text: str, usage: tuple[int, int] = (500, 100)) -> AIMessage:
    return AIMessage(content=text, usage_metadata={"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage)})


def script(*steps: AIMessage) -> Callable[[list[BaseMessage], int], AIMessage]:
    def respond(_: list[BaseMessage], i: int) -> AIMessage:
        return steps[i] if i < len(steps) else say("Fertig.")

    return respond


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def past(minutes: int) -> datetime:
    return now() - timedelta(minutes=minutes)
