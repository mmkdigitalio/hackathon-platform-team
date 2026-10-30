# Übergaben und Annahmen gegenüber A, B und C

Strang D hat gegen Lieferungen entwickelt, die es noch nicht gibt. Jede Annahme ist hier aufgeführt und im Code an einer Stelle gebündelt. Weicht eine Lieferung ab, wird nur diese Stelle angepasst.

## Strang C

| Punkt | Annahme von D | Stelle im Code |
| --- | --- | --- |
| Vertrag v0.1 | Entwurf von D unter `operations-agent/src/contracts/`; Besitz geht an C (C-Task 1.2), Abstimmung offen | `contracts/v0_1.py` |
| Aufruf | Worker ruft `agent.execute(invocation, services)` und persistiert das `AgentResult` | `agent/runner.py` |
| Aktionsprotokoll | `record_planned` vor jedem externen Aufruf, `update` mit Status `planned/started/succeeded/failed/unclear` und `external_refs` | `contracts.ActionLog` |
| Sperre | `try_acquire` ohne Warten, `holder`, `holds`, `release` | `contracts.OperationsLock` |
| Zustand | `request(job_id, "acting" \| "verifying", reason)`; `False` = abgelehnt | `contracts.StateTransitions` |
| Checkpoints | `checkpoints.checkpointer()` liefert `agent.persistence.checkpointer(conn)` oder `None` | `contracts.CheckpointProvider` |
| SDK-Migrationen | `ops migrate` übernimmt `agent.persistence.sdk_migrations()`; Schritte 6–8 mit Autocommit | `agent/persistence.py` |
| Einstiegspunkte | `ops-api`, `ops-worker`, `ops migrate` als Konsolenbefehle im selben Paket | `deploy/values.yaml` |
| Limits | C füllt `RunLimits` aus `agent.config.limits_from_env()` | `agent/config.py` |

## Strang B

**Stand 30.10.2026: geliefert und abgeglichen** (Commits `5596a58` bis `2a24167`). `tests/agent/test_contract_b.py` prüft D's Code gegen B's echte Dateien: Beispielkatalog und -sollzustand für AFFiNE, Katalog des Agenten, `app-deploy.yml` (Eingaben, `run-name`), `app-update-desired-state.yml` (Eingaben aus `agent-build.yml`). Abweichungen und Ergänzungen gegenüber den ursprünglichen Annahmen:

- Sollzustand: `image` ist eine Zeichenkette `<repo>@sha256:…` bzw. `acr:<repo>@sha256:…`, Chart-Version unter `chart.version`; beides liest D.
- `smokeTest.image` darf `acr:` verwenden; D löst es mit `OPS_ACR_LOGIN_SERVER` auf, `smokeTest.env` geht an den Job (Änderung von B in `b2eab34`).
- `runbook.path` darf ein Verzeichnis sein (Katalog des Agenten); D fügt dessen Markdown-Dateien zusammen, README zuerst, höchstens 20 000 Zeichen.
- Haupt-Deployment: Name aus `ingress.service` bzw. App-ID, sonst eindeutig über das Label `app.kubernetes.io/instance: <app-id>`.
- Chart-Werte: B übergibt zusätzlich `platform.*` (u. a. `replicas`); D's Chart steuert API und Worker weiter über `api.replicas`/`worker.replicas`.

| Punkt | Ursprüngliche Annahme von D (bestätigt, wo nicht oben abweichend) |
| --- | --- |
| Katalogfelder | `allowedActions`, `availability.path`, `smokeTest.image` (Digest), `smokeTest.secrets`, `runbook.{repository,path,commit}`, `ingress.service` oder `deployment` als Deployment-Name |
| Sollzustand | Replicas unter `sizes.replicas` (Fallbacks `replicas`, `values.replicaCount`), Digest unter `image.digest` oder `imageDigest` (`agent/context.py`) |
| Release-Annotationen am Namespace | `ops.mmk/release-git-sha`, `ops.mmk/release-image-digest`, `ops.mmk/release-chart-version` (`tools/kubernetes.py`) |
| Test-Secrets | Schlüssel im Secret `<app-id>-platform` heißen wie die Umgebungsvariablen in `smokeTest.secrets` |
| Pull-Secret in App-Namespaces | Name als Chart-Wert `agent.appImagePullSecret` |
| `app-deploy.yml` | `workflow_dispatch`-Eingaben `appId`, `environment`, `operationId`, `actionId`, `force`; `run-name` enthält `operationId` und `actionId` |
| `app-update-desired-state.yml` | `workflow_call`-Eingaben `appId`, `environment`, `imageDigest`, `chartVersion` |
| Secret des Agenten | Schlüssel wie in [paket-betrieb.md](paket-betrieb.md#erwartete-schlüssel-im-secret) |

## Strang A

| Punkt | Annahme von D |
| --- | --- |
| `.github/actions/foundation-outputs` | Eingaben `backend-account`, `backend-key`, `config-passphrase`; Ausgaben `acrLoginServer`, `acrUsername`, `acrPassword` (maskiert) |
| `modelApiKeys` | JSON-Objekt mit Schlüsseln `anthropic` und/oder `azure-openai` |
| Review | Abweichung von P1-07 ([abweichung-p1-07.md](abweichung-p1-07.md)) |

## AFFiNE-Kollege

Smoke-Test-Image nach B's Vertrag (Exit-Code, genau ein JSON auf stdout mit `success`), Runbook im Repository `mmkdigitalio/hackathon-platform-affine` mit Commit-Referenz im Katalog.
