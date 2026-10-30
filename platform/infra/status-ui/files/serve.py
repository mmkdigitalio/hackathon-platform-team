"""Run only the status UI and its read-only projection from the tested agent image.

No event intake, worker, model, GitHub authentication or Kubernetes client is started.
This bootstrap is versioned with the Helm chart and mounted read-only.
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.settings import Settings
from api.status import install_status
from persistence.db import conninfo

settings = Settings.from_env()
pool = ConnectionPool(conninfo(settings.database_url), min_size=1, max_size=3, open=False,
                      timeout=3, kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 3})


@asynccontextmanager
async def lifespan(app):
    pool.open(wait=False)
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Platform status", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_status(app, settings, pool)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    # UI process health only. Component health and unknowns are reported separately in /status.
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
