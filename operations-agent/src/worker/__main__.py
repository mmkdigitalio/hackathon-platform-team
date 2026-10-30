"""Prozess-Einstiegspunkt des Workers: ``ops-worker`` bzw. ``python -m worker``.

D's Ausführung und Abgleich werden über ``modul:fabrik`` geladen:
``OPS_AGENT_EXECUTOR`` (Standard ``agent.execution:build_executor``) und
``OPS_ACTION_RECONCILER`` (Standard ``agent.reconcile:build_reconciler``).
"""

import importlib
import logging
import os
import signal
import sys
import threading

from contracts.errors import OperationsError
from persistence.db import check_schema, connect
from persistence.runs import known_secrets
from api.security import install_log_masking

from .runner import Worker, WorkerConfig

DEFAULT_EXECUTOR = "agent.execution:build_executor"
DEFAULT_RECONCILER = "agent.reconcile:build_reconciler"


def load(spec: str):
    module_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module_name), attr)()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_log_masking(known_secrets())
    log = logging.getLogger("ops.worker")

    try:
        with connect() as conn:
            version = check_schema(conn)
    except OperationsError as exc:
        log.error("Start abgebrochen: %s", exc)
        return 3

    executor_spec = os.environ.get("OPS_AGENT_EXECUTOR", DEFAULT_EXECUTOR)
    reconciler_spec = os.environ.get("OPS_ACTION_RECONCILER", DEFAULT_RECONCILER)
    try:
        executor = load(executor_spec)
        reconciler = load(reconciler_spec)
    except (ImportError, AttributeError) as exc:
        log.error("Agentenausführung nicht ladbar (%s / %s): %s", executor_spec, reconciler_spec, exc)
        return 4

    config = WorkerConfig(
        lease_seconds=int(os.environ.get("OPS_LEASE_SECONDS", "120")),
        heartbeat_interval_seconds=float(os.environ.get("OPS_HEARTBEAT_SECONDS", "30")),
        lock_ttl_seconds=int(os.environ.get("OPS_AGENT_LOCK_TTL_SECONDS", "300")),
    )
    log.info("Schema-Version %s, Ausführung %s, Worker-ID %s", version, executor_spec, config.worker_id)

    stop = threading.Event()

    def on_signal(signum, _frame):
        # Keine neuen Aufträge mehr; ein laufender Auftrag läuft weiter oder wird nach
        # Ablauf seines Lease von einem anderen Worker wiederaufgenommen.
        log.info("Signal %s: beende nach dem laufenden Auftrag", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    Worker(connect, executor, reconciler, config).run_forever(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
