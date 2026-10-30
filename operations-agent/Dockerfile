# syntax=docker/dockerfile:1.7
# Build-Kontext ist das Repository-Root, damit die Betriebsregeln aus platform/operations/agent/ ins Image kommen:
#   docker build -f operations-agent/Dockerfile .
# Ein Image für alle Prozesse; den Startbefehl (api, worker, migrate) setzt das Helm-Chart.

FROM python:3.12.16-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY operations-agent/pyproject.toml operations-agent/uv.lock operations-agent/.python-version ./

FROM base AS build
RUN uv sync --frozen --no-dev --no-install-project
COPY operations-agent/src ./src
RUN uv sync --frozen --no-dev

# Prüfstufe für CI: docker build --target test ...
FROM base AS test
RUN uv sync --frozen --no-install-project
COPY operations-agent/src ./src
COPY operations-agent/tests ./tests
COPY platform/operations/agent/betriebsregeln.md /app/platform/operations/agent/betriebsregeln.md
RUN uv sync --frozen && uv run --frozen pytest -q

FROM python:3.12.16-slim-bookworm AS runtime
RUN useradd --uid 10001 --create-home ops
WORKDIR /app
COPY --from=build /app /app
COPY platform/operations/agent/betriebsregeln.md /app/platform/operations/agent/betriebsregeln.md
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    OPS_RULES_FILE=/app/platform/operations/agent/betriebsregeln.md
USER 10001
CMD ["python", "-c", "import sys; sys.exit('Startbefehl kommt aus dem Helm-Chart (api, worker oder migrate)')"]
