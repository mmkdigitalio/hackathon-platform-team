#!/usr/bin/env python3
"""Local dashboard with real HTTP checks; database is optional, never simulated.

cd operations-agent && .venv/bin/python scripts/preview-status.py --base-domain <domain>
"""

import argparse
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from api.app import make_pool
from api.settings import Settings
from api.status import install_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-domain", default=os.environ.get("OPS_BASE_DOMAIN", ""))
    parser.add_argument("--environment", choices=("staging", "production"), default="staging")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    settings = Settings.from_env()
    pool = make_pool(settings) if settings.database_url or os.environ.get("POSTGRES_HOST") else None

    @asynccontextmanager
    async def lifespan(app):
        if pool:
            pool.open(wait=False)
        try:
            yield
        finally:
            if pool:
                pool.close()

    app = FastAPI(title="Platform status preview", lifespan=lifespan)
    install_status(app, settings, pool, base_domain=args.base_domain, environment=args.environment)
    print(f"Local: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
