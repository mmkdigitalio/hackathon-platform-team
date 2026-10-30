"""Tasks 3.3 und 5.1: C's Worker ruft D's echtes ``agent.execute`` auf.

Echt sind Datenbank, Auftragsverwaltung, C's Dienste und der LangGraph-Checkpointer;
GitHub, Kubernetes und das Modell kommen aus D's Test-Doubles (tests/agent/).
"""

import uuid

from agent import Dependencies, execute
from agent.persistence import checkpointer
from agent.persistence import connect as sdk_connect
from contracts import RunLimits
from persistence.db import connect
from persistence.runs import get_run
from worker.runner import Worker, WorkerConfig

from ..agent.conftest import MODEL, settings, world  # noqa: F401 - Fixture
from ..agent.fakes import ScriptedModel
from ..agent.test_runner import broken_config, broken_scaled_to_zero, call, say, script
from .conftest import connector
from .test_worker_and_states import enqueue, job


def run_with_d(db_url, world, model):  # noqa: F811
    def d_execute(invocation, services):
        return execute(
            invocation,
            services,
            settings=settings(),
            model=MODEL,
            deps=Dependencies(
                github=world.github.client(),
                kubernetes=lambda policy: world.k8s(policy),
                http=world.probe(),
                chat_model=model,
                sleep=world.clock.sleep,
                clock=world.clock,
            ),
        )

    worker = Worker(connector(db_url), d_execute,
                    WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", limits=RunLimits))
    return worker.run_once()


def test_known_repair_through_c_worker(db_url, platform_root, world):  # noqa: F811
    broken_scaled_to_zero(world)
    job_id = enqueue(db_url, platform_root)
    model = ScriptedModel(
        responder=script(
            call("compare_desired_actual"),
            call("redeploy_desired_state", {"reason": "0 Replicas, Soll 1"}),
            call("run_smoke_test"),
            say("AFFiNE war auf 0 Replicas; Sollzustand wiederhergestellt, Smoke-Test positiv."),
        )
    )
    assert run_with_d(db_url, world, model) == job_id

    j = job(db_url, job_id)
    assert j["state"] == "resolved", j["state_reason"]
    assert world.github.workflow_calls() == 1
    with connect(db_url) as conn:
        run = get_run(conn, job_id)
        dispatch = conn.execute("SELECT * FROM actions WHERE kind = 'workflow_dispatch'").fetchone()
        locks_left = conn.execute("SELECT count(*) AS n FROM operation_locks").fetchone()["n"]
    # Der Workflow bekam C's operationId und die von C vergebene actionId.
    inputs = world.github.dispatches[0]
    assert inputs["operationId"] == dispatch["operation_id"] and inputs["actionId"] == dispatch["id"]
    assert dispatch["status"] == "succeeded" and dispatch["external_refs"]["runId"]
    assert locks_left == 0, "Dispatch hält keine Ziel-Sperre"
    path = [t["to_state"] for t in run["transitions"] if t["accepted"]]
    assert path == ["queued", "investigating", "acting", "verifying", "resolved"]
    assert {v["kind"] for v in run["verifications"]} == {"smoke-test", "k8s-state"}
    assert run["executions"][0]["usage"]["model_calls"] == 4
    assert run["findings"]["confirmed"]


def test_unrepairable_config_through_c_worker(db_url, platform_root, world):  # noqa: F811
    broken_config(world)
    job_id = enqueue(db_url, platform_root)
    model = ScriptedModel(
        responder=script(
            call("compare_desired_actual"),
            call("get_logs"),
            call("redeploy_desired_state", {"reason": "versuchen"}),
            call("restart_workload", {"reason": "Pods hängen"}),
            call("run_smoke_test"),
            say("Alles wieder gut."),
        )
    )
    run_with_d(db_url, world, model)
    j = job(db_url, job_id)
    assert j["state"] == "needs_attention"
    assert "c0ffee1234567890" in j["state_reason"]  # nötige Änderung mit Commit
    with connect(db_url) as conn:
        restart = conn.execute("SELECT * FROM actions WHERE kind = 'restart'").fetchone()
    assert restart is not None and restart["mutating"]  # Neustart nur mit Ziel-Sperre möglich


def test_checkpoint_survives_process_restart(db_url, platform_root, world):  # noqa: F811
    """5.1: Checkpoint der gepinnten LangGraph-Version in den von ops migrate angelegten Tabellen."""
    broken_scaled_to_zero(world)
    job_id = enqueue(db_url, platform_root)
    model = ScriptedModel(responder=script(call("compare_desired_actual"), say("Nur geprüft.")))
    run_with_d(db_url, world, model)

    # Neue Verbindung wie nach einem Prozessneustart.
    with sdk_connect(db_url) as conn:
        saved = checkpointer(conn).get_tuple({"configurable": {"thread_id": job_id}})
    assert saved is not None
    assert saved.metadata["lc_agent_name"] == "mmk-ops-agent" and saved.metadata["step"] > 0
    # Nachrichten liegen als Delta-Snapshot neben dem Checkpoint.
    assert saved.metadata["counters_since_delta_snapshot"]["messages"][0] > 0
