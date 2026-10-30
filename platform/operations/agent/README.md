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
| `OPS_MODEL_PROVIDER` | `anthropic` oder `azure-openai` | `anthropic` |
| `OPS_MODEL_NAME` | Modell-ID bzw. Azure-Deploymentname | `claude-sonnet-5-5` (nur Anthropic) |
| `OPS_MODEL_ENDPOINT` | Azure-OpenAI-Endpunkt | Pflicht bei Azure |
| `OPS_MODEL_API_VERSION` | Azure-API-Version | `2024-10-21` |
| `OPS_MODEL_PRICE_INPUT_PER_MTOK`, `OPS_MODEL_PRICE_OUTPUT_PER_MTOK` | USD je 1 Mio. Tokens für die Kostengrenze | Platzhalter 3/15 (Anthropic), 2,5/10 (Azure) |
| `OPS_MODEL_API_KEYS` | Foundation-Ausgabe `modelApiKeys` als JSON, z. B. `{"anthropic": "…", "azure-openai": "…"}` | aus dem Secret des App-Stacks |
| `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY` | lokale Alternative zu `OPS_MODEL_API_KEYS` | – |

Die Preise sind Platzhalter und vor dem Staging-Betrieb mit der Preisliste des gewählten Modells abzugleichen. Keys erscheinen weder in Prompts noch in Logs noch im Protokoll; `ModelSettings.public()` liefert die protokollierbare Sicht.

**Standardanbieter:** Anthropic mit `claude-sonnet-5-5`, weil der Anthropic-Zugang samt Budget belegt ist (README) und die Azure-Deployments noch nicht inventarisiert sind. Die endgültige Wahl folgt nach dem ersten echten Prototyplauf mit dem Key aus `modelApiKeys` (offene Frage im Design).

## Prototyp starten (P1-02)

```bash
cd operations-agent
uv sync --frozen
export ANTHROPIC_API_KEY=…            # oder OPS_MODEL_API_KEYS
uv run ops-agent-prototype --url https://example.com
```

Azure OpenAI ohne Codeänderung:

```bash
OPS_MODEL_PROVIDER=azure-openai OPS_MODEL_NAME=<deployment> OPS_MODEL_ENDPOINT=https://<resource>.openai.azure.com \
AZURE_OPENAI_API_KEY=… uv run ops-agent-prototype --url https://example.com
```

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
| Agenten- und Adaptertests (`uv run pytest`) | 47 bestanden, davon 1 gegen lokales PostgreSQL 18 |
| Checkpoint gegen PostgreSQL, neue Verbindung liest Thread weiter | bestanden (`tests/agent/test_persistence.py`) |
| `helm lint` / `helm template` mit `deploy/ci-values.yaml` | bestanden |
| Echter Modellaufruf (Anthropic und Azure OpenAI) | **offen**: kein Modell-Key in der lokalen Umgebung; Protokoll nach dem ersten Lauf hier ergänzen |

## Umsetzungsstand je Task

Abgehakt sind in `tasks.md` nur Tasks, deren Abschlussprüfung lokal vollständig lief (1.1, 1.3, 2.3, 2.4, 2.5, 3.5). Für die übrigen ist der Code fertig und mit simulierten Gegenstellen getestet; es fehlt die genannte Prüfung:

| Task | Offene Abschlussprüfung | Wartet auf |
| --- | --- | --- |
| 1.2, 1.4 | echter Tool-Calling-Lauf, Anbieterwechsel, Start nach Anleitung | Modell-Key (`modelApiKeys`) |
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
