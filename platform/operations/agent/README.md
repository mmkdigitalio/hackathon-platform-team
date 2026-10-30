# Betriebsagent (Strang D)

Agentenausführung, Betriebswerkzeuge, Image und Chart des MMK-Betriebsagenten. Code unter [`operations-agent/`](../../../operations-agent/), Change [`operations-agent-and-tools`](../../../openspec/changes/operations-agent-and-tools/).

| Dokument | Inhalt |
| --- | --- |
| [betriebsregeln.md](betriebsregeln.md) | Betriebsregeln im Systemprompt (einzige Regelquelle) |
| [werkzeuge.md](werkzeuge.md) | Werkzeug-Eingaben, Fehlerformat, Evidenzreferenzen |
| [massnahmen-und-abgleich.md](massnahmen-und-abgleich.md) | Grenzen, Maßnahmen, Sperre, Dispatch, Abgleich, Verifikation |
| [abweichung-p1-07.md](abweichung-p1-07.md) | Diagnose ohne Azure-Zugriff (E6) |
| [paket-betrieb.md](paket-betrieb.md) | Image, Chart, Secret-Schlüssel, Start, Stopp, Wiederanlauf, Rollback |
| [uebergaben.md](uebergaben.md) | Annahmen und offene Punkte gegenüber A, B, C |

## Paketstand

Gepinnt in `operations-agent/pyproject.toml`, aufgelöst in `operations-agent/uv.lock` (Python 3.12):

| Paket | Version |
| --- | --- |
| deepagents | 0.7.32 |
| langchain | 1.4.7 |
| langchain-anthropic | 1.7.7 |
| langchain-openai | 1.7.3 |
| langgraph | 1.2.18 |
| langgraph-checkpoint-postgres | 3.2.0 |
| psycopg | 3.3.6 |
| kubernetes | 37.0.1 |

Reproduzierbare Installation: `uv sync --frozen` in `operations-agent/`. `uv lock --check` schlägt fehl, wenn Manifest und Lockdatei auseinanderlaufen. C meldet neue Abhängigkeiten an D; D ändert Manifest und Lockdatei.

## Modellkonfiguration

Anbieter und Modell sind Konfiguration; ein Wechsel braucht keine Codeänderung, nur einen Neustart.

| Variable | Bedeutung | Standard |
| --- | --- | --- |
| `OPS_MODEL_PROVIDER` | `azure-openai` oder `anthropic` | `azure-openai` |
| `OPS_MODEL_NAME` | Azure-Deploymentname bzw. Anthropic-Modell-ID | Pflicht bei Azure; `claude-sonnet-5-5` bei Anthropic |
| `OPS_MODEL_ENDPOINT` | Azure-OpenAI-Endpunkt | Pflicht bei Azure |
| `OPS_MODEL_API_VERSION` | Azure-API-Version | `2025-04-01-preview` |
| `OPS_MODEL_RESPONSES_API` | Azure über die Responses API (`store=False`, verschlüsseltes Reasoning) | `true` |
| `OPS_MODEL_PRICE_INPUT_PER_MTOK`, `OPS_MODEL_PRICE_OUTPUT_PER_MTOK` | USD je 1 Mio. Tokens für die Kostengrenze | Platzhalter 3/15 (Anthropic), 2,5/10 (Azure) |
| `OPS_MODEL_API_KEYS` | Foundation-Ausgabe `modelApiKeys` als JSON, z. B. `{"anthropic": "…", "azure-openai": "…"}` | aus dem Secret des App-Stacks |
| `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY` | lokale Alternative zu `OPS_MODEL_API_KEYS` | – |

Die Preise sind Platzhalter und vor dem Staging-Betrieb mit der Preisliste des gewählten Modells abzugleichen. Keys erscheinen weder in Prompts noch in Logs noch im Protokoll; `ModelSettings.public()` liefert die protokollierbare Sicht.

**Standardanbieter (Entscheidung 30.10.2026):** Azure OpenAI, Ressource `mmk-hackathon-openai` (Subscription „MMK DIGITAL - Hackathon 2026“, `swedencentral`), Endpunkt `https://mmk-hackathon-openai.openai.azure.com/`, Deployment `gpt-6.1-terra`. Weitere Chat-Deployments: `gpt-6-luna`, `gpt-6.1-sol`. Damit ist die offene Frage im Design beantwortet. Anthropic bleibt per Konfiguration verfügbar.

**Responses API ist Pflicht:** Die GPT-6-Deployments lehnen Function Tools über `/chat/completions` ab („Function tools with reasoning_effort are not supported … use /v1/responses“). `build_chat_model` nutzt deshalb `use_responses_api=True` mit `api-version 2025-04-01-preview`; die Kurzform `preview` liefert 404. Mit `store=False` speichert Azure keine Antworten; der Reasoning-Zustand wird verschlüsselt im Verlauf mitgeführt, damit Checkpoint-Fortsetzungen funktionieren.

Den Key für lokale Läufe liefert die Azure CLI, ohne ihn auszugeben:

```bash
export AZURE_OPENAI_API_KEY="$(az cognitiveservices account keys list --subscription 'MMK DIGITAL - Hackathon 2026' \
  -g hackathon-openai -n mmk-hackathon-openai --query key1 -o tsv)"
```

## Prototyp starten (P1-02)

```bash
cd operations-agent
uv sync --frozen
export AZURE_OPENAI_API_KEY=…         # siehe oben, oder OPS_MODEL_API_KEYS
export OPS_MODEL_ENDPOINT=https://mmk-hackathon-openai.openai.azure.com/
OPS_MODEL_NAME=gpt-6.1-terra uv run ops-agent-prototype --url https://example.com
OPS_MODEL_NAME=gpt-6-luna    uv run ops-agent-prototype --url https://example.com   # Wechsel nur per Konfiguration
```

Anthropic ohne Codeänderung: `OPS_MODEL_PROVIDER=anthropic ANTHROPIC_API_KEY=… uv run ops-agent-prototype`.

Der Prototyp baut einen Deep Agent mit genau einem typisierten Werkzeug (`http_probe`, Eingabe `HttpUrl`), ruft das Modell echt auf und gibt ein JSON-Protokoll aus: Paketstand, öffentliche Modellkonfiguration, Werkzeugaufrufe, Werkzeugergebnis, Antwort, Verbrauch und Checkpoint-Angaben. Exit-Code 0 nur bei mindestens einem echten Werkzeugergebnis. Grenzen: 3 Werkzeugaufrufe, 0,20 USD, 120 Sekunden.

Mit Persistenz (setzt das von C migrierte Schema voraus):

```bash
uv run ops-agent-prototype --checkpoint-dsn postgresql://… --thread-id demo   # erster Lauf
uv run ops-agent-prototype --checkpoint-dsn postgresql://… --thread-id demo   # setzt den Thread fort
```

`checkpoint.messagesBefore` ist beim zweiten Lauf größer als 0.

## Ergebnisse

Stand 30.10.2026, lokal auf macOS mit Python 3.12.6:

| Nachweis | Ergebnis |
| --- | --- |
| Installation aus Lockdatei in leerer Umgebung (`uv sync --frozen --no-cache`) | erfolgreich, Versionen wie oben |
| Agenten- und Adaptertests (`uv run pytest`) | 62 bestanden, davon 1 gegen lokales PostgreSQL 18 (`OPS_TEST_POSTGRES_DSN`) |
| Unabhängiges Review mit Codex (`gpt-6.1-sol`, high) | 10 Befunde bestätigt und behoben, je ein Regressionstest in `tests/agent/test_review_regressions.py` |
| Checkpoint gegen PostgreSQL, neue Verbindung liest Thread weiter | bestanden (`tests/agent/test_persistence.py`) |
| `helm lint` / `helm template` mit `deploy/ci-values.yaml` | bestanden |
| Echter Tool-Calling-Lauf Azure OpenAI `gpt-6.1-terra` | bestanden: ein `http_probe`-Aufruf, Status 200, 2 Modellaufrufe, 361 Tokens |
| Deploymentwechsel nur per `OPS_MODEL_NAME` auf `gpt-6-luna` | bestanden, ohne Codeänderung |
| Checkpoint in PostgreSQL, zweiter Prozess setzt Thread fort (Azure) | bestanden: 4 → 8 Nachrichten im Thread |
| Gesamte Agentenausführung mit echtem `gpt-6.1-terra` gegen simulierte Infrastruktur, AFFiNE auf 0 Replicas | `resolved`: 12 Werkzeugaufrufe, ein Dispatch, Smoke-Test positiv, 7 Modellaufrufe, ca. 0,10 USD (Platzhalterpreise) |
| dasselbe, ungültiger Konfigurationswert | `needs_attention`: kein Redeploy, kein Neustart, Smoke-Test negativ, Verweis auf Sollzustands-Commit, ca. 0,08 USD |
| Echter Lauf mit Anthropic | nicht ausgeführt (kein Key; Azure ist Standard). Anbieterwechsel ist in `test_config.py` geprüft |

Protokoll ohne Secrets: [nachweise/2026-10-30-p1-02-azure.json](nachweise/2026-10-30-p1-02-azure.json).

## Umsetzungsstand je Task

Abgehakt sind in `tasks.md` nur Tasks, deren Abschlussprüfung lokal vollständig lief (1.1–1.4, 2.3, 2.4, 2.5, 3.5). Für die übrigen ist der Code fertig und mit simulierten Gegenstellen getestet; es fehlt die genannte Prüfung:

| Task | Offene Abschlussprüfung | Wartet auf |
| --- | --- | --- |
| 2.1, 2.2 | Aufruf durch C's Worker, echter AFFiNE-Kontext | C-Worker, B-Katalog, AFFiNE-Lieferung |
| 3.1, 3.2 | reale AFFiNE-Befunde, echter Staging-Alarm | Staging-Cluster, A-Alarm |
| 3.3, 3.4 | echter Commit und Issue-Kommentar über den PAT | `opsGithubToken`, B-Sollzustand |
| 3.6 | Review mit A | A |
| 4.1–4.5 | echter Neustart, echter `app-deploy.yml`-Lauf, Besitzverlust mit C | Staging, B-Workflow, C-Sperrclient |
| 5.1–5.5 | Läufe gegen AFFiNE in Staging | Staging, Smoke-Test-Image |
| 6.1 | Prozessstart aus dem Image | C's Einstiegspunkte, Docker-Build |
| 6.2–6.4 | Start gegen echtes Secret, Upgrade mit Migration, `kubectl auth can-i` | Staging, B-App-Stack, C-Migrationen |
| 6.5, 6.6 | Lauf auf `main`, Agenten-Update in Staging | A-Composite-Action, B-Workflow |
| 7.1–7.3 | gemeinsame Abnahme | alle Stränge |

## Persistenzbefund für C (Task 1.3)

- Checkpointer: `langgraph.checkpoint.postgres.PostgresSaver` 3.2.0 über eine psycopg-Verbindung mit `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` (`agent.persistence.connect`).
- D ruft `PostgresSaver.setup()` **nie** auf. `agent.persistence.checkpointer(conn)` prüft nur die SDK-Schema-Version (`checkpoint_migrations`, erwartet v9) und bricht sonst mit „`ops migrate` ausführen“ ab.
- C nimmt die SDK-Tabellen in `ops migrate` auf: `agent.persistence.sdk_migrations()` liefert die 10 Schritte der gepinnten Version, `apply_sdk_migrations(conn)` ist die geprüfte Referenz (idempotent).
- **Achtung:** Schritte 6 bis 8 enthalten `CREATE INDEX CONCURRENTLY` und laufen nicht in einem Transaktionsblock. `ops migrate` muss sie mit Autocommit ausführen; die Advisory-Lock bleibt auf Sitzungsebene gültig.
- Ein Store (threadübergreifendes Gedächtnis) wird in Phase 1 nicht verwendet. Runbook-Verbesserungen werden nicht automatisch gespeichert.
- Thread-ID ist `resume.threadId` aus dem Auftrag. Checkpoints ersetzen weder Aktionsprotokoll noch Sperre.
