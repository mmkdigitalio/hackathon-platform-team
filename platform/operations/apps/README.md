# Betrieb des App-Pfads

Verfahren für Release, Rollback, Stopp, Wiederanlauf, Break-glass und Außerbetriebnahme von Apps in Phase 1. Gilt für AFFiNE und den Betriebsagenten gleichermaßen. Fehlerszenarien für AFFiNE stehen unter [affine/](affine/README.md).

## Bausteine

| Baustein | Ort |
| --- | --- |
| Katalog und Sollzustand | `platform/catalog/<app-id>/app.yaml`, `platform/environments/<env>/<app-id>.yaml`, Schema in [platform/schemas/app-catalog](../../schemas/app-catalog/README.md) |
| App-Programm (Pulumi) | [platform/infra/app-runtime](../../infra/app-runtime/README.md), Stack `organization/app-<app-id>/<env>` |
| Plan-Prüfung | `.github/workflows/app-plan.yml`, Check „app-plan / Plan-Ergebnis“ |
| Deployment | `.github/workflows/app-deploy.yml` |
| Sollzustand per PR | `.github/workflows/app-update-desired-state.yml` (wiederverwendbar) |
| Generischer Smoke-Test | `.github/workflows/app-smoke-http.yml`, Image `acr:platform/smoke-http` |

GitHub Actions ist in Phase 1 der einzige automatische Weg, den Git-Sollzustand auszurollen. Kein Lauf braucht eine Azure-Anmeldung.

## Voraussetzungen im Repository (einmalig, Admin)

| Einstellung | Wert |
| --- | --- |
| Repo-Secrets | `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` (A), optional `OPS_GITHUB_TOKEN` (PAT, nur Rückfall ohne App-Schlüssel) |
| Allow auto-merge | empfohlen, **nicht aktiv** (`allow_auto_merge` ist `false`; Stand 31.10.2026, geändert 31.10.2026) |
| Branch-Schutz `main` | empfohlen, **nicht aktiv** (`main` hat keinen Branch-Schutz; Stand 31.10.2026): erforderlicher Status-Check **app-plan / Plan-Ergebnis**; „Require branches to be up to date“ empfohlen |
| Reviews | keine Pflicht-Reviews für PRs, die nur `platform/environments/` ändern; sonst blockiert Auto-Merge, sobald er aktiv ist. Wird eine Review-Pflicht gebraucht, CODEOWNERS so setzen, dass `platform/environments/` ausgenommen ist |
| Actions | „Read and write permissions“ ist nicht nötig; Commits und PRs laufen über ein Token der GitHub App des Agenten |

Vor dem ersten Deployment: Foundation ausgerollt (A), `ops-migrate.yml` einmal gelaufen (C), A's Verträge unter `platform/schemas/conventions/` und `platform/schemas/foundation-outputs/` sowie die Composite Action `.github/actions/foundation-outputs` vorhanden.

## Release

**Agent (geändert 31.10.2026):** `agent-build.yml` (D) baut Image, Toolbox und Chart und ruft `app-update-desired-state.yml` auf. Es entsteht ein PR auf `platform/environments/staging/operations-agent.yaml`. Auto-Merge ist nicht aktiv (siehe oben): Ein Mensch prüft den PR nach grünem `app-plan.yml` und merged ihn manuell; danach startet `app-deploy.yml`.

**AFFiNE (geändert 31.10.2026):** `.github/workflows/app-affine-sync.yml` läuft alle 15 Minuten, baut Chart und Smoke-Image aus dem neuesten getesteten Stand von `mmkdigitalio/hackathon-platform-affine` in die ACR, setzt Katalogeintrag und Sollzustand `platform/environments/staging/affine.yaml`, committet direkt auf `main` und startet `app-deploy.yml`. Ein manueller PR ist dafür nicht nötig.

**Andere Apps:** manueller PR auf `platform/environments/<env>/<app-id>.yaml` mit neuem `image` (Digest) und `chart.version`. Datenmigrationen der neuen Version müssen vorher in `dataProcedures.migration` des Katalogeintrags beschrieben sein.

Ein Deployment gilt erst als erfolgreich, wenn Rollout und Smoke-Test erfolgreich waren und das laufende Release dem Sollzustand entspricht. Das Deployment-Ergebnis steht in der Zusammenfassung, als Artefakt `deployment-<app>-<env>` und im Aktionsprotokoll (`ops runs show` bzw. die Operation zur `operationId`).

Änderungen an `platform/catalog/` oder `platform/infra/app-runtime/` lösen kein Deployment aus. Sie wirken mit dem nächsten Lauf; sofort per `workflow_dispatch`:

```bash
gh workflow run app-deploy.yml -f appId=affine -f environment=staging
```

## Rollback

Ein Rollback ist ein PR auf einen früheren Sollzustand, kein Eingriff am Cluster. Bei AFFiNE setzt `app-affine-sync.yml` den Sollzustand beim nächsten Lauf (alle 15 Minuten) wieder auf das neueste Paket; für ein Rollback den Workflow vorher deaktivieren (geändert 31.10.2026).

1. Früheren Stand ermitteln: `git log -p -- platform/environments/<env>/<app-id>.yaml`.
2. **Datenkompatibilität prüfen und im PR dokumentieren:** Hat ein Release seit dem Zielstand Daten migriert (Runbook, `dataProcedures.migration`, Release-Notes)? Kann die alte Version das aktuelle Schema lesen? Ohne positive Antwort kein Merge.
3. PR mit `git revert <commit>` oder den alten Werten für `image` und `chart.version` anlegen; der PR-Text enthält das Ergebnis aus Schritt 2.
4. Nach Merge rollt `app-deploy.yml` aus und verifiziert.

Ein Image-Rollback ist keine Datenwiederherstellung. Datenwiederherstellung bleibt ein manuelles Verfahren nach dem Runbook der App.

## Stoppen und wieder starten

- **Stoppen:** PR mit `sizes.replicas: 0` im Sollzustand. Der Verfügbarkeitsalarm feuert dann erwartungsgemäß; vorher ankündigen oder den Agenten informieren. Datenbank, PVC und Secrets bleiben erhalten.
- **Wieder starten:** PR mit der vorherigen Replica-Zahl.
- Manuelles `kubectl scale` wird vom nächsten Deployment auf den Sollzustand zurückgesetzt und ist deshalb kein Stoppverfahren.

## Wiederanlauf nach Cluster-Verlust

1. A stellt den Cluster mit dem Foundation-Stack lokal wieder her; die Foundation-Ausgaben enthalten danach die neue Kubeconfig. `baseDomain` kann sich mit der Ingress-IP ändern; App-Datenbanken bleiben auf dem Foundation-Server erhalten.
2. Für jede App in jeder Umgebung `app-deploy.yml` **mit `clusterRebuilt=true`** starten, Agent zuerst:
   ```bash
   gh workflow run app-deploy.yml -f appId=operations-agent -f environment=staging -f clusterRebuilt=true
   gh workflow run app-deploy.yml -f appId=affine -f environment=staging -f clusterRebuilt=true
   ```
   Die Sperre braucht nur PostgreSQL, nicht den Agenten.
3. Ohne `clusterRebuilt` bricht der Lauf mit „failed to read resource state due to unreachable cluster“ ab, weil der Refresh noch den alten Cluster abfragt. Mit `clusterRebuilt` (setzt `PULUMI_K8S_DELETE_UNREACHABLE=true`) entfernt der Refresh die Objekte des alten Clusters aus dem State, auch geschützte, und der Lauf legt sie neu an. Generierte Passwörter stammen aus dem State und bleiben gleich, Datenbank und Login bleiben unverändert. `clusterRebuilt` nie nach bloßen Netzwerkstörungen verwenden: dann würden existierende Objekte aus dem State entfernt.
4. PVC-Inhalte (AFFiNE-Anhänge) gehen mit dem Cluster verloren, weil dynamische Azure-Disks in der Node-Resource-Group liegen (akzeptiertes Staging-Risiko, siehe Datenverfahren im Katalog).
5. Nach geänderter `baseDomain` rollt A den Foundation-Stack für die Verfügbarkeitstests erneut aus.

Lokal auf kind durchgespielt (30.10.2026): Cluster gelöscht und neu angelegt, ein Lauf mit `PULUMI_K8S_DELETE_UNREACHABLE=true` legte 10 Objekte neu an; Secret-Inhalt und Datenbank-OID unverändert, App wieder erreichbar.

## Sperre und Konflikte

`app-deploy.yml` erwirbt über C's Operations-Client die Sperre für App und Umgebung unter seiner `operationId`, erneuert sie minütlich, prüft den Besitz vor Plan, `pulumi up` und Smoke-Test und gibt sie am Ende frei. Ist die Sperre belegt, wartet der Lauf bis zu 15 Minuten und bricht dann ohne Änderung ab; die Zusammenfassung nennt die sperrende Operation. Die Aufrufe von `ops` sind in `platform/infra/app-runtime/scripts/ops-lock.sh` gekapselt.

Läufe sind über den `run-name` (`app-deploy <app>/<env> op=<operationId> action=<actionId>`) und die als externe Referenz protokollierte Lauf-URL auffindbar.

## Break-glass

Nur wenn PostgreSQL nicht erreichbar ist und trotzdem ausgerollt werden muss (E9):

```bash
gh workflow run app-deploy.yml -f appId=operations-agent -f environment=staging -f force=true \
  -f operationId=breakglass-$(date -u +%Y%m%d%H%M)
```

- Der Lauf prüft zuerst die Verbindung. Ist PostgreSQL erreichbar, wird `force` abgelehnt und die normale Sperre verwendet.
- Ohne Sperre vermerkt die Zusammenfassung „Break-glass“ mit App, Umgebung, `operationId`, Git-SHA, Lauf, Zeitpunkt und Grund. Aktionsnachweise entfallen in diesem Lauf.
- Ein AFFiNE-Lauf scheitert ohne PostgreSQL ohnehin am Baustein `postgres-database`; Break-glass betrifft praktisch den Agenten.

**Nachtrag nach Wiederherstellung (Pflicht):** Die Zusammenfassung des Laufs enthält den fertigen Befehl; `--outcome` nach dem Ergebnis des Laufs setzen und lokal mit Datenbankzugang aus den Foundation-Ausgaben (`PG*`-Variablen) ausführen. Verfahren laut C in `platform/operations/state/locks.md`:

```bash
ops lock backfill --app <app-id> --env <env> --git-sha <sha> \
  --workflow-run <Lauf-URL> --at <Zeitpunkt aus dem Vermerk> --outcome succeeded --note "operationId <operationId>"
```

Der Nachtrag legt eine Operation `break-glass-…` mit einer Aktion `deploy` an und ist je Workflow-Lauf idempotent.

## Außerbetriebnahme

Das Löschen einer Sollzustands- oder Katalogdatei ändert keinen Stack; `app-deploy.yml` meldet nur den nötigen separaten Vorgang. Datenbank, Login, Namespace und PVC sind mit `protect` gesichert.

1. Datenverfahren der App ausführen (Sicherung exportieren, falls gewünscht) und im PR dokumentieren.
2. Lokal mit Backend-Zugang:
   ```bash
   cd platform/infra/app-runtime
   scripts/pulumi-stack.sh <app-id> <env> state unprotect --all --yes
   scripts/pulumi-stack.sh <app-id> <env> destroy --yes
   scripts/pulumi-stack.sh <app-id> <env> stack rm --yes
   ```
3. Erst danach Katalogeintrag und Sollzustand per PR entfernen; A rollt den Foundation-Stack aus, um Verfügbarkeitstest und Alarm zu entfernen.

Lokal auf kind durchgespielt (30.10.2026): 18 Ressourcen gelöscht, Namespace und Datenbank entfernt, Stack entfernt.
