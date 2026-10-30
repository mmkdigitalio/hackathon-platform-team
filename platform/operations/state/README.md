# Betriebsdokumentation: Eingang, Aufträge, Sperre

Strang C. Code unter `operations-agent/src/{contracts,api,persistence,worker}/`, Tests unter `operations-agent/tests/state/`.

| Dokument | Inhalt |
| --- | --- |
| [intake.md](intake.md) | Eingänge, Ereignisvertrag, GitHub-Webhook-Einrichtung, Beispielzustellungen, Fehlerdiagnose |
| [worker.md](worker.md) | Prozesse, Start, Stopp, Lease, Wiederanlauf, Heartbeat, `/healthz`, Migrationen |
| [locks.md](locks.md) | Operations-Sperre, CLI `ops`, Konfliktregel, Break-glass mit Nachtrag, Dispatch-Zuständigkeit |

## Überblick

```text
Azure Monitor (ops-intake) ─┐
GitHub-Webhook (Issues) ────┼─> API ──(eine Transaktion)──> PostgreSQL operations
health-check.yml ───────────┘                                   ▲
                                         Worker ── Lease ───────┤
                                           └─> D's Agentenausführung (Vertrag src/contracts/)
Deploy-Workflows (B) ─── ops lock / ops actions ────────────────┘   (ohne laufende API)
```

## Lokale Entwicklung

Voraussetzung: [uv](https://docs.astral.sh/uv/) (Python 3.12 laut `.python-version`), PostgreSQL 15+ (Homebrew `postgresql@17` oder Docker). Paketmanifest und Lockdatei gehören D.

```bash
cd operations-agent
uv sync --frozen

# Tests: starten selbst einen temporären PostgreSQL-Cluster (initdb/pg_ctl)
uv run --frozen pytest tests/state      # C; tests/ insgesamt inkl. D
# alternativ gegen einen vorhandenen Server, z. B. Docker:
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:17
#   OPS_TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5432/postgres uv run --frozen pytest tests/state
```

## Konfiguration

| Variable | Bedeutung | Quelle im Cluster |
| --- | --- | --- |
| `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE` (TLS erzwungen) | Datenbank `operations` im Pod | Foundation-Ausgaben `postgres*` über das Secret des Agenten |
| `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGSSLMODE` | Datenbank auf GitHub-Runnern (`ops`) | Foundation-Ausgaben über `foundation-outputs` |
| `OPS_DATABASE_URL` | Datenbank lokal; hat Vorrang | – |
| `ALERT_WEBHOOK_KEY` | Query-Key für Azure Monitor | `alertWebhookKey` |
| `MAINTENANCE_API_KEY` | `X-Api-Key` für Wartung und `/runs` | `maintenanceApiKey` |
| `GITHUB_WEBHOOK_SECRET` | Signatur des Repo-Webhooks | `githubWebhookSecret` |
| `OPS_GITHUB_REPO_ALLOWLIST` | Kommagetrennt, Standard `mmkdigitalio/hackathon-platform-team` | – |
| `OPS_PLATFORM_ROOT` | Pfad zu `platform/` für die Katalogprüfung; ohne Angabe wird er ab dem Arbeitsverzeichnis gesucht | Image (D) |
| `OPS_AGENT_EXECUTOR` | `modul:funktion` der Agentenausführung | Standard `agent:execute` (D) |
| `OPS_CONTEXT_GIT_REF` | Git-Stand, zu dem D Katalog und Sollzustand liest | Standard `main` |
| `OPS_LIMIT_*` | Laufgrenzen (`RunLimits`), gelesen über `agent.config.limits_from_env` | ConfigMap (D) |
| `OPS_LEASE_SECONDS`, `OPS_HEARTBEAT_SECONDS`, `OPS_AGENT_LOCK_TTL_SECONDS` | Lease 120 s, Heartbeat 30 s, Agenten-Sperre 300 s | – |
| `OPS_GIT_SHA` | Codestand für Laufnachweise | Image (D) |

Die Schnittstelle zur Agentenausführung ist D's Vertrag `contracts.v0_1` (`agent.execute(invocation, services)`). C stellt die Dienste in `worker/services.py` bereit; `contracts/actions.py`, `contracts/states.py` und `contracts/errors.py` sind C's Verträge für Persistenz und Deploy-Workflows.

## Laufnachweise

```bash
curl -s -H "X-Api-Key: $MAINTENANCE_API_KEY" https://$OPERATIONS_API_HOST/runs/<auftragsId>
ops runs show <auftragsId>          # direkt gegen PostgreSQL, auch ohne API
```

Das JSON enthält Auftrag, Vorfall, Ereignisse mit Quelldaten, alle angenommenen und abgelehnten Zustandsübergänge, Ausführungen mit Codestand, Modellkonfiguration und Verbrauch, Operationen, Aktionen mit externen Referenzen, Werkzeugaufrufe, Verifikationen sowie Befunde getrennt nach `confirmed` und `suspected`. Bekannte Secret-Werte und Felder wie `password`, `token` und `apiKey` erscheinen als `***`.
