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

Voraussetzung: Python 3.12+, PostgreSQL 15+ (Homebrew `postgresql@17` oder Docker).

```bash
cd operations-agent
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'

# Tests: starten selbst einen temporären PostgreSQL-Cluster (initdb/pg_ctl)
pytest tests/state
# alternativ gegen einen vorhandenen Server, z. B. Docker:
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:17
#   OPS_TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5432/postgres pytest tests/state
```

## Konfiguration

| Variable | Bedeutung | Quelle im Cluster |
| --- | --- | --- |
| `OPS_DATABASE_URL` oder `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGSSLMODE` | Datenbank `operations` | Foundation-Ausgaben `postgres*` |
| `OPS_ALERT_WEBHOOK_KEY` | Query-Key für Azure Monitor | `alertWebhookKey` |
| `OPS_MAINTENANCE_API_KEY` | `X-Api-Key` für Wartung und `/runs` | `maintenanceApiKey` |
| `OPS_GITHUB_WEBHOOK_SECRET` | Signatur des Repo-Webhooks | `githubWebhookSecret` |
| `OPS_GITHUB_REPO_ALLOWLIST` | Kommagetrennt, Standard `mmkdigitalio/hackathon-platform-team` | – |
| `OPS_PLATFORM_ROOT` | Pfad zu `platform/` für die Katalogprüfung; ohne Angabe wird er ab dem Arbeitsverzeichnis gesucht | Image (D) |
| `OPS_AGENT_EXECUTOR`, `OPS_ACTION_RECONCILER` | `modul:fabrik` der Agentenausführung und des Abgleichs | Standard `agent.execution:build_executor`, `agent.reconcile:build_reconciler` |
| `OPS_LEASE_SECONDS`, `OPS_HEARTBEAT_SECONDS`, `OPS_AGENT_LOCK_TTL_SECONDS` | Lease 120 s, Heartbeat 30 s, Agenten-Sperre 300 s | – |
| `OPS_GIT_SHA` | Codestand für Laufnachweise | Image (D) |

## Laufnachweise

```bash
curl -s -H "X-Api-Key: $OPS_MAINTENANCE_API_KEY" https://$OPERATIONS_API_HOST/runs/<auftragsId>
ops runs show <auftragsId>          # direkt gegen PostgreSQL, auch ohne API
```

Das JSON enthält Auftrag, Vorfall, Ereignisse mit Quelldaten, alle angenommenen und abgelehnten Zustandsübergänge, Ausführungen mit Codestand, Modellkonfiguration und Verbrauch, Operationen, Aktionen mit externen Referenzen, Werkzeugaufrufe, Verifikationen sowie Befunde getrennt nach `confirmed` und `suspected`. Bekannte Secret-Werte und Felder wie `password`, `token` und `apiKey` erscheinen als `***`.
