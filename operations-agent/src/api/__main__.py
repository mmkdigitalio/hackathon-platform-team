"""Prozess-Einstiegspunkt der API: ``ops-api`` bzw. ``python -m api``.

Prüft beim Start nur die Schema-Version und beendet sich bei Abweichung; migriert nie.
"""

import argparse
import logging
import os
import sys

import uvicorn

from contracts.errors import OperationsError
from persistence.db import check_schema, connect

from .app import create_app
from .security import install_log_masking
from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ops-api")
    parser.add_argument("--host", default=os.environ.get("OPS_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPS_API_PORT", "8080")))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    install_log_masking(settings.secrets)
    log = logging.getLogger("ops.api")

    try:
        with connect(settings.database_url) as conn:
            version = check_schema(conn, include_sdk=True)
    except OperationsError as exc:
        log.error("Start abgebrochen: %s", exc)
        return 3
    log.info("Schema-Version %s, Katalog: %s", version, settings.platform_root or "nicht gefunden")

    # Zugriffslog von uvicorn aus: es enthielte ?key= im Klartext. Die App loggt maskiert.
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
