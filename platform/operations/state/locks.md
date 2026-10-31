# Operations-Sperre und Aktionsprotokoll

Eine exklusive Sperre je App und Umgebung in PostgreSQL (Tabelle `operation_locks`), gemeinsam für Agent und Deploy-Workflows (E9). Der Client greift direkt auf die Datenbank zu und braucht keine laufende Agenten-API. Der Foundation-Stack nimmt keine Sperre.

Begriffe:

- **Operation** (`operationId`): ein ändernder Ablauf für eine App/Umgebung, Korrelationsmerkmal, kein Besitznachweis.
- **Aktion** (`actionId`): eine einzelne externe Maßnahme mit Ziel, Art, Status (`planned` → `started` → `succeeded`/`failed`, oder `unclear`) und externen Referenzen. Sie wird **vor** dem externen Aufruf gespeichert.
- **Sperrbesitz**: Besitzerreferenz + Sperrversion + Ablaufzeit. Getrennt vom Auftrags-Lease des Workers.

## Installation auf dem Runner

```yaml
- id: foundation
  uses: ./.github/actions/foundation-outputs
  with: { … }
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: pip install ./operations-agent
- env:
    PGHOST: ${{ steps.foundation.outputs.postgresHost }}
    PGUSER: ${{ steps.foundation.outputs.postgresAdminUser }}
    PGPASSWORD: ${{ steps.foundation.outputs.postgresAdminPassword }}
    PGDATABASE: ${{ steps.foundation.outputs.postgresOperationsDatabase }}
    PGSSLMODE: require
  run: ops lock acquire …
```

B's `app-deploy.yml` ruft den Client ausschließlich über [`platform/infra/app-runtime/scripts/ops-lock.sh`](../../infra/app-runtime/scripts/ops-lock.sh) auf (Erwerb mit 15 Minuten Wartezeit, Erneuerung jede Minute, Besitzprüfung, eigene Aktion `act-deploy-<run>-<attempt>-<app>-<env>` mit `agentActionId` als Korrelation, Freigabe); C testet das Skript in `tests/state/test_deploy_wrapper.py`.

Keine Azure-Anmeldung. Fehlt das Schema, bricht jeder Befehl mit Exit-Code 3 und Verweis auf `ops-migrate.yml` ab, ohne Zieländerung. Die Sperrbefehle verlangen nur die Mindest-Schemaversion für Sperre und Aktionsprotokoll (`LOCK_SCHEMA_VERSION` in `persistence/cli.py`), nicht die neueste: Bringt ein Agenten-Release eine neue Migration mit, erwirbt der Deploy-Workflow die Sperre noch auf dem alten Schema, und der Helm-Hook desselben Deployments spielt die Migration ein. API und Worker prüfen beim Start weiter die genaue Version (geändert 31.10.2026; war: genaue Version auch für Sperrbefehle, ein Deploy mit neuer Migration brach ohne vorheriges `ops-migrate.yml` ab).

## CLI

| Befehl | Zweck | Exit-Codes |
| --- | --- | --- |
| `ops lock acquire --app A --env E [--operation-id O] [--force]` | Erwerb, wartet bis 15 min | 0, 4 belegt, 5 DB weg, 6 ungeklärt |
| `ops lock renew --app A --env E --lock-version V [--ttl S]` | Erneuern | 0, 7 |
| `ops lock check --app A --env E --lock-version V` | Besitzprüfung, z. B. vor `pulumi up` und vor dem Smoke-Job | 0, 7 |
| `ops lock release --app A --env E --lock-version V [--status completed\|failed]` | Freigabe, nur durch den Besitzer; `--status` schließt zusätzlich die Operation ab (nicht bei der Operation eines Agenten-Dispatchs) | 0, 7 |
| `ops lock status --app A --env E` | Wer hält die Sperre? | 0 |
| `ops lock backfill …` | Nachtrag nach Break-glass | 0 |
| `ops actions record --operation-id O --kind K [--action-id X] [--agent-action-id A] [--correlation K=V …] --lock-version V …` | Aktion vor dem Aufruf speichern | 0, 7 |
| `ops actions update --operation-id O --action-id X --status started\|succeeded\|failed\|unclear [--external-ref K=V …] --lock-version V …` | Ergebnis ergänzen, z. B. `--external-ref runUrl=https://…` | 0, 7 |
| `ops actions resolve --action-id X --status succeeded\|failed --reason …` | Ungeklärte Aktion nach manueller Prüfung abschließen | 0 |
| `ops runs show <auftragsId>` | Laufnachweis als JSON | 0, 1 |

`--owner` ist im Workflow automatisch `github-run:<repo>/<run_id>/<attempt>`. `acquire` schreibt `operation-id`, `lock-version`, `owner` und `break-glass` nach `$GITHUB_OUTPUT`. Standard-Gültigkeit einer Workflow-Sperre: 30 Minuten; bei längeren Rollouts mit `renew` verlängern.

## Konfliktregel

| Wer will | Sperre belegt durch | Verhalten |
| --- | --- | --- |
| Deploy-Workflow | Agent oder anderen Workflow | `ops lock acquire` versucht alle 15 s, höchstens 15 Minuten; danach Exit-Code 4 ohne Zieländerung. |
| Agent | Workflow oder anderen Auftrag | Kein Warten. Der Auftrag endet sofort in `needs_attention` mit Grund „gesperrt durch Operation <operationId>“. |

Eine **abgelaufene** Sperre wird nur neu vergeben, wenn ihre Operation keine offene ändernde Aktion (`planned`, `started`, `unclear`) für dieses Ziel hat. Sonst Exit-Code 6 bzw. `UnresolvedActions` mit den Aktions-IDs. Klärung: externen Vorgang prüfen (Workflow-Lauf, Kubernetes-Zustand), dann

```bash
ops actions resolve --action-id <id> --status failed --reason "Lauf abgebrochen, Ziel unverändert" --by <name>
```

Ein Besitzer mit abgelaufener oder fremder Sperre kann weder erneuern noch prüfen, freigeben oder Aktionen schreiben (Exit-Code 7).

## Break-glass (E9)

Nur wenn PostgreSQL **nicht erreichbar** ist:

```bash
ops lock acquire --app affine --env staging --force
```

- Ohne `--force`: Abbruch mit Exit-Code 5 vor jeder Zieländerung.
- Mit `--force`: Exit-Code 0, Ausgabe `break-glass=true`, Vermerk in `$GITHUB_STEP_SUMMARY` mit App, Umgebung, Git-SHA, Workflow-Lauf, Zeitpunkt und dem fertigen Nachtragsbefehl. Das Deployment läuft ohne Sperre; folgende `ops`-Befehle des Workflows bei `break-glass == 'true'` überspringen.
- Ist PostgreSQL erreichbar, ist `--force` wirkungslos und die normale Konfliktregel gilt.
- Der Agent verwendet `--force` nie; die Bibliothek bietet für ihn keinen solchen Weg.

**Nachtrag** nach Wiederherstellung: Den Befehl aus dem Vermerk der Workflow-Zusammenfassung ausführen (lokal mit Datenbankzugang aus den Foundation-Ausgaben):

```bash
ops lock backfill --app affine --env staging --git-sha <sha> \
  --workflow-run https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/<id> \
  --at 2026-10-30T10:00:00+00:00 [--outcome failed] [--note "…"]
```

Er legt eine Operation `break-glass-…` (Art `break-glass`, Vermerk `break-glass`) mit einer Aktion `deploy` an und ist idempotent je Workflow-Lauf.

## Dispatch und Workflow-Zuständigkeit

Der Agent ändert Apps entweder direkt (z. B. Skalieren) oder über `app-deploy.yml` (B):

**Direkte Maßnahme:** Ziel-Sperre ohne Warten erwerben → Aktion speichern → externer Aufruf → Ergebnis ergänzen → Sperre freigeben. Ändernde Agentenaktionen ohne gehaltene Ziel-Sperre werden abgewiesen.

**Workflow-Dispatch:**

1. Agent gibt eine eventuell gehaltene Ziel-Sperre frei. Eine Aktion `workflow_dispatch` bei gehaltener Ziel-Sperre wird abgewiesen (`NestedTargetLock`), weil der Workflow sonst 15 Minuten warten und dann abbrechen würde.
2. Agent speichert die Aktion `workflow_dispatch` (`planned`) und startet `app-deploy.yml` mit `appId`, `environment`, `operationId`, `actionId`.
3. Der Workflow erwirbt die Ziel-Sperre unter derselben `operationId` (`ops lock acquire --operation-id <operationId>`), protokolliert eigene Aktionen mit `--agent-action-id <actionId>`, gibt die Sperre am Ende frei.
4. Agent setzt seine Dispatch-Aktion mit der Lauf-URL auf `started` und verfolgt den Lauf nur lesend, ohne Ziel-Sperre. Nach dem Lauf: `succeeded`/`failed`.
5. Neustart dazwischen: D's Abgleich findet den Lauf über die `actionId` im `run-name` und verfolgt ihn weiter (siehe [worker.md](worker.md#wiederanlauf)).

Beispiel ohne verschachtelte Ziel-Sperre (aus `test_dispatch_requires_released_target_lock`; mit D's echtem Agenten in `test_agent_integration.py`):

```text
agent    acquire affine/staging (job:<id>)        -> ok
agent    record workflow_dispatch                 -> abgewiesen: Ziel-Sperre vor Dispatch freigeben
agent    release affine/staging                   -> ok
agent    record workflow_dispatch act-1 (planned) -> ok, Agent hält keine Sperre
workflow ops lock acquire --operation-id <op>     -> ok, Besitzer github-run:…
workflow ops actions record --kind pulumi-up --agent-action-id act-1
workflow ops actions update … --status succeeded
workflow ops lock release                         -> ok
```
