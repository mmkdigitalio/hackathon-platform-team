# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md), Verhalten in [app-catalog](specs/app-catalog/spec.md), [app-delivery](specs/app-delivery/spec.md) und [app-onboarding](specs/app-onboarding/spec.md). Verbindlich sind die Hackathon-Entscheidungen E1 bis E10 und die Verträge in der [gemeinsamen Übersicht](../../PARALLEL-IMPLEMENTATION.md) sowie der Foundation-Ausgabevertrag in [shared-platform-runtime](../platform-foundation/specs/shared-platform-runtime/spec.md).

Prägend sind drei Vorgaben: Ein Monorepo (E1), AFFiNE als einzige Fach-App aus externer Lieferung (E2) und GitHub Actions ohne Azure-Identität (E4 bis E6). Der App-Pfad arbeitet deshalb nur mit Pulumi-State-Key, Passphrase, Admin-Kubeconfig, ACR-Admin-User und PostgreSQL-Admin-Login.

## Goals / Non-Goals

Goals:

- Ein App-Pfad für AFFiNE und den Betriebsagenten, ohne app-spezifischen Code im App-Programm. Die Status-UI `platform-status` nutzt ihn als dritte App (ergänzt 31.10.2026).
- Paketlieferung, Sollzustandsänderung, Rollout und fachliche Verifikation nachvollziehbar über `operationId` und Release-Referenz verbinden.
- Zwei reproduzierbare, datensichere Fehlerszenarien für die gemeinsame Abnahme.

Non-Goals:

- Keine Demo-App, keine AFFiNE-Paketierung, keine Agentenlogik.
- Keine Azure-Ressourcen, keine Sperrdatenbank, keine Foundation-Module.
- Keine neuen Datenbausteine ohne Umsetzung im App-Programm.

## Decisions

### 1. Bereiche im Monorepo

| Pfad | Inhalt |
| --- | --- |
| `platform/schemas/app-catalog/` | JSON-Schema v0.1 für `app.yaml` und Sollzustand, Beispiele, Validierungs-CLI |
| `platform/catalog/<app-id>/app.yaml` | Umgebungsunabhängiger Eintrag; `affine/` vom AFFiNE-Kollegen (übernommen durch `app-affine-sync.yml`), `operations-agent/` von B, `platform-status/` aus dem Change [platform-status-ui](../platform-status-ui/proposal.md) (ergänzt 31.10.2026) |
| `platform/environments/<env>/<app-id>.yaml` | Sollzustand je Umgebung |
| `platform/infra/app-runtime/` | Pulumi-Programm für alle App-Stacks, Datenbausteine, generisches Smoke-Test-Image `smoke-http/` |
| `platform/operations/apps/` | Betriebsverfahren des App-Pfads, Fehlerskripte unter `affine/` |
| `.github/workflows/app-*.yml` | `app-plan.yml`, `app-deploy.yml`, `app-update-desired-state.yml`, `app-smoke-http.yml` (Build des Smoke-Test-Images), `app-affine-sync.yml` (Übernahme der AFFiNE-Lieferung, ergänzt 31.10.2026) |

### 2. Katalog in zwei Dateien

`app.yaml` beschreibt, was eine App ist und braucht; die Sollzustandsdatei, welche Version mit welchen Werten in einer Umgebung läuft. Versionswechsel berühren so nur `platform/environments/`, und der Foundation-Stack liest für Verfügbarkeitstests nur `app.yaml`. Namespace und Host werden nach der Konvention aus `platform-bootstrap` abgeleitet und sind deshalb keine Felder; das verhindert Abweichungen zwischen Katalog, Ingress und Verfügbarkeitstest.

Skizze `app.yaml` (Feldnamen werden mit dem Schema festgelegt):

```yaml
schemaVersion: "0.1"
appId: affine
source: { repository: mmkdigitalio/hackathon-platform-affine, upstream: toeverything/AFFiNE }
chart: { acr: { repository: helm/affine } }   # (geändert 31.10.2026; war: oci: { url: oci://<registry>/<chart> }); weitere Quellen: oci: { url }, repository: { url, name }
ingress: { service: affine, port: 3010 }
dataBlocks: [postgres-database, redis, persistent-volume]
generatedSecrets: [smoke-user-password]   # (geändert 31.10.2026; war: affine-private-key)
secrets: { DATABASE_URL: app:postgres-url, REDIS_SERVER_PASSWORD: app:redis-password }
availability: { path: /info }
smokeTest: { image: acr:affine-smoke@sha256:..., env: { ... }, secrets: { SMOKE_USER_PASSWORD: app:smoke-user-password } }
runbook: { repository: ..., path: ..., commit: <sha> }
allowedActions: [restart, redeploy]
dataProcedures: { migration: ..., backup: ..., restore: not-applicable }
```

Konfigurationswerte für das Chart sind im Sollzustand frei, weil sie dem Chart gehören. Gegen ein vorhandenes `values.schema.json` des Charts wird geprüft. Das nicht reparierbare Fehlerszenario nutzt genau diese Lücke: ein schemagültiger, fachlich ungültiger Wert.

### 3. Datenbausteine und Secrets

| Baustein | Umsetzung | `app:`-Secrets |
| --- | --- | --- |
| `postgres-database` | Pulumi-postgresql-Provider mit `postgresAdminUser`/`postgresAdminPassword`, `sslmode=require`; Datenbank und Login nach App-ID (Bindestriche als Unterstriche) | `postgres-host`, `postgres-database`, `postgres-user`, `postgres-password`, `postgres-url` |
| `redis` | Kubernetes-Deployment und Service mit per Digest fixiertem Redis-Image im App-Namespace, ohne Persistenz. Kein Chart, weil die frei verfügbaren Bitnami-Charts und -Images nicht mehr verlässlich bezogen werden können | `redis-host`, `redis-password` |
| `persistent-volume` | PVC mit StorageClass `managed-csi`, ReadWriteOnce | keine |

Der App-Stack schreibt alle aufgelösten Referenzen, einschließlich der Test-Secrets aus `smokeTest.secrets`, in das Secret `<app-id>-platform`. Smoke-Test-Jobs erhalten daraus nur ihre eigenen Schlüssel per `secretKeyRef`. Generierte Werte entstehen mit `random`-Ressourcen und liegen verschlüsselt im Stack-State, sodass sie stabil bleiben. Datenbank, Login und PVC erhalten `protect`. Zusätzlich erhält der App-Login `CREATE` und `USAGE` auf dem Schema `public` seiner Datenbank (ergänzt 31.10.2026): Auf dem Azure Flexible Server gehört das Schema `azure_pg_admin`, und die Prisma-Migration von AFFiNE scheiterte sonst mit „permission denied for schema public“ (Commit 467412c; danach Lauf 41077703734 `manual-affine-grantfix` grün).

### 4. Helm-Ausrollung mit Hooks und Drift-Korrektur

Das Agenten-Chart braucht echte Helm-Hooks für Migrationen; das App-Programm nutzt deshalb `helm.v3.Release` statt einer in Pulumi-Ressourcen aufgelösten Chart-Variante. Ein `Release` aktualisiert aber nur bei geänderten Eingaben. Damit die bekannte Reparatur (0 Replicas) ohne Sollzustandsänderung greift, übergibt jeder Lauf die `operationId` als Chart-Wert `rolloutMarker`, der kein Pod-Template verändert. Helm führt dadurch ein Upgrade mit Drei-Wege-Merge aus und setzt manuell geänderte Felder auf den Sollzustand zurück. In Task 2.3 lokal gegen kind nachgewiesen: Nach `kubectl scale --replicas=0` stellt ein Lauf ohne Sollzustandsänderung die Replica-Zahl wieder her, ohne Pods neu zu starten, wenn nichts abweicht.

Werte-Vertrag mit den Charts (Konvention aus D's Agenten-Chart): Das App-Programm setzt `image.repository`, `image.digest`, `secretName`, `imagePullSecretName`, `rolloutMarker` und einen Block `platform` mit Host, URL, PVC-Name, Größen und Release-Referenz. Diese Schlüssel sind im Sollzustand reserviert. Die Release-Referenz des letzten erfolgreichen Rollouts steht in den Namespace-Annotationen `ops.mmk/release-git-sha`, `ops.mmk/release-image-digest` und `ops.mmk/release-chart-version`, die auch D's Kubernetes-Werkzeug liest.

### 5. Workflows und Versionsfluss

- `app-plan.yml` (Pull Request): Validierung, Abgleich der Secret-Referenzen mit `platform/schemas/foundation-outputs/`, `pulumi preview` der betroffenen Stacks. Vorgesehen als erforderlicher Status-Check für `main`; Stand 31.10.2026: im Repository nicht gesetzt (kein Branch-Schutz auf `main`) (ergänzt 31.10.2026).
- `app-deploy.yml` (Push auf `main` unter `platform/environments/**`, `workflow_dispatch` mit `appId`, `environment`, `operationId`, `actionId`, `force` und `clusterRebuilt`; Letzteres ergänzt 31.10.2026, setzt `PULUMI_K8S_DELETE_UNREACHABLE` für den Wiederanlauf nach Cluster-Verlust, siehe `platform/operations/apps/README.md`): `run-name` enthält App, Umgebung, `operationId` und `actionId`. Ablauf: Foundation-Ausgaben, Validierung, Host-Prüfung, Sperre, `pulumi up --refresh`, Rollout-Prüfung, Smoke-Job, Ergebnis, Freigabe. Eine Concurrency-Gruppe je App und Umgebung ergänzt die Sperre.
- `app-update-desired-state.yml` (`workflow_call`, Eingaben `appId`, `environment`, `imageDigest`, `chartVersion`, optional `imageRepository` mit Standard `acr:<appId>` und `sourceSha`): setzt Digest und Chart-Version, legt Branch, Commit und Pull Request mit einem Installations-Token der GitHub App des Agenten an (Schlüssel `githubAppPrivateKey` aus `foundation-outputs`, Rückfall `OPS_GITHUB_TOKEN` (geändert 2026-10-30 (Cornelius); war: mit `OPS_GITHUB_TOKEN`)) und aktiviert Auto-Merge (geändert 31.10.2026; war: „und aktiviert Auto-Merge“ ohne Bedingung). Der Workflow aktiviert Auto-Merge nur, wenn `allow_auto_merge` im Repository `true` ist; sonst endet er mit einem Hinweis, und der PR wird nach grünem `app-plan` manuell gemergt (Stand: deaktiviert, PRs #4 bis #6 manuell gemergt). Der Schlüssel kommt aus `githubAppPrivateKey` der Foundation-Ausgaben oder, solange dieser leer ist, aus dem Repo-Secret `OPS_GITHUB_APP_PRIVATE_KEY`; `OPS_GITHUB_TOKEN` ist nur der letzte Rückfall und im Repository nicht gesetzt (ergänzt 31.10.2026). Aufgerufen vom Agenten-Build (`agent-build.yml`). Mit `GITHUB_TOKEN` würden Pull Request und Merge keine weiteren Workflows auslösen.
- Änderungen an `platform/catalog/` oder `platform/infra/app-runtime/` lösen kein automatisches Deployment aus; sie wirken mit dem nächsten Lauf oder einem manuellen `workflow_dispatch`.

AFFiNE-Versionen kommen inzwischen durch `app-affine-sync.yml` (alle 15 Minuten und per `workflow_dispatch`): Der Workflow nimmt den neuesten grünen Lauf von `platform-package.yml` im Lieferrepository, baut Chart und Smoke-Image in die Foundation-ACR, setzt `platform/catalog/affine/app.yaml` und `platform/environments/staging/affine.yaml`, validiert, committet direkt auf `main` (ohne PR und ohne `app-plan`) und startet `app-deploy.yml` selbst, weil Pushes mit `GITHUB_TOKEN` keine Workflows auslösen (geändert 31.10.2026; war: „AFFiNE-Versionen kommen per manuellem Pull Request“). Ein manueller PR auf den Sollzustand bleibt möglich. Voraussetzung im Repository für den PR-Fluss: Auto-Merge erlaubt, `app-plan.yml` als erforderlicher Check, keine Pflicht-Reviews auf Sollzustands-PRs. Diese Einstellungen dokumentiert B unter `platform/operations/apps/`, gesetzt werden sie von einer Person mit Admin-Rechten; Stand 31.10.2026 sind sie nicht gesetzt.

Zugänge: Repo-Secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` und `OPS_GITHUB_APP_PRIVATE_KEY` (geschützter Rückfall für den GitHub-App-Schlüssel; Deploy und Plan reichen ihn nur für `operations-agent` als Stack-Konfiguration durch; ergänzt 31.10.2026); alles Weitere über `.github/actions/foundation-outputs` mit den Eingaben `backend-account`, `backend-key`, `config-passphrase` (wie D's `agent-build.yml`) und einer Step-Ausgabe je Vertragswert einschließlich `contractVersion`.

Stacks: Pulumi leitet das Projekt aus `Pulumi.yaml` ab. `platform/infra/app-runtime/scripts/pulumi-stack.sh` erzeugt deshalb je App ein Projekt `app-<app-id>`, das auf das gemeinsame Programm zeigt; die Stack-Konfiguration ist flüchtig, das Passphrase-Salz liest Pulumi aus dem State (lokal nachgewiesen). Der App-Stack legt je Namespace ein Image-Pull-Secret aus `acrUsername`/`acrPassword` an.

### 6. Sperre, Konfliktregel und Break-glass

Der Deploy-Workflow installiert C's Operations-Client aus `operations-agent/` und greift damit direkt auf PostgreSQL zu, nicht auf die Agenten-API. Er erwirbt die Sperre unter `operationId`, wartet bei Belegung bis zu 15 Minuten, erneuert während des Rollouts, prüft Besitz vor `pulumi up` und vor dem Smoke-Job und protokolliert Lauf-URL und Ergebnis als Aktion. Der Agent startet den Workflow, hält dabei keine Ziel-Sperre und übergibt seine `operationId`.

Break-glass (E9): Nur mit `force` und nur wenn ein Verbindungstest zu PostgreSQL scheitert. Die Zusammenfassung vermerkt Grund und `operationId`; der Nachtrag ins Aktionsprotokoll ist ein dokumentierter manueller Schritt mit C's CLI. Praktisch betrifft das vor allem den Agenten: Ein AFFiNE-Lauf scheitert ohne PostgreSQL ohnehin am Baustein `postgres-database`.

### 7. Smoke-Test-Vertrag

Der Deploy-Workflow startet den Smoke-Test als Job im App-Namespace mit `TARGET_URL=https://<app-id>.<baseDomain>`, den nicht geheimen Werten aus `smokeTest.env` und den Test-Secrets per `secretKeyRef`, wartet auf das Ende, liest die Ausgabe und ergänzt `appId`, `environment`, `release` aus den Namespace-Annotationen und dem Image-Digest der laufenden Pods sowie `checkRef` als Digest des Smoke-Test-Images (`sha256:…`) (geändert 31.10.2026; war: „als Image-Referenz mit Digest“; Commit 3f49334, abgestimmt mit C's `VerificationEvidence`). Das Ergebnis ist die letzte JSON-Zeile der Ausgabe, weil `kubectl logs` stdout und stderr mischt. Exit-Code und `success` müssen übereinstimmen. D's Verifikationswerkzeug startet den Job gleich und löst `acr:`-Referenzen mit `OPS_ACR_LOGIN_SERVER` auf. Für den Agenten liefert B ein generisches Image `smoke-http`, das `TARGET_URL` plus Prüfpfad abfragt und das Ergebnis im Vertragsformat schreibt.

### 8. AFFiNE als externe Lieferung

Der AFFiNE-Kollege liefert Chart, Werte, Smoke-Test-Image und Runbook und legt `platform/catalog/affine/app.yaml` nach Schema an. B validiert die Lieferung, legt den Sollzustand an und integriert sie. Tatsächlicher Weg (geändert 31.10.2026): Die GHCR-Pakete der Organisation dürfen nicht öffentlich sein, der Cluster zieht deshalb alles aus der ACR. `app-affine-sync.yml` baut Chart (`oci://<acrLoginServer>/helm/affine`, Katalog `acr: { repository: helm/affine }`, Version `<major>.<minor>` aus dem Lieferrepository plus Laufnummer) und Smoke-Image (`acr:affine-smoke@sha256:…`) aus dem getesteten Commit in die Foundation-ACR und trägt Digest und Runbook-Commit ein. AFFiNE speichert Dokumente in PostgreSQL und Anhänge im Dateisystem; Redis dient als Cache und Queue.

Fehlerszenarien:

| Fall | Auslösen | Erwartung | Rücksetzen |
| --- | --- | --- | --- |
| Reparierbar | Skript skaliert `affine` in `affine-staging` auf 0 | Verfügbarkeitsalarm, Agent startet `app-deploy.yml`, Smoke-Test grün | `app-deploy.yml` |
| Nicht reparierbar | Commit mit ungültigem Konfigurationswert in `platform/environments/staging/affine.yaml` | Rollout scheitert, Alarm, `restart`/`redeploy` wirkungslos, `needs_attention` mit Commit | Revert-PR |

Beide berühren weder Datenbank noch PVC noch Secrets. Damit der zweite Fall zu einem Ausfall statt zu einem hängenden Rolling Update führt, muss das AFFiNE-Chart wegen des ReadWriteOnce-Volumes die Strategie `Recreate` verwenden.

### 9. Agentenpaket

`platform/catalog/operations-agent/app.yaml` referenziert D's Chart in der veröffentlichten Version `oci://<acrLoginServer>/helm/operations-agent` (Katalog: `acr: { repository: helm/operations-agent }`, Ingress auf Service `operations-agent` Port 80), `smoke-http` mit Prüfpfad `/healthz` und ausschließlich `foundation:`-Secrets; einen Datenbaustein braucht der Agent nicht, weil er die Foundation-Datenbank `operations` nutzt. `allowedActions` ist leer (der Agent repariert sich nicht selbst, Ausfallmeldung an `ops-humans`). Der Sollzustand setzt zusätzlich `agent.appImagePullSecret: acr-pull` für D's Smoke-Test-Jobs und `workspace.azure` (Client-, Tenant- und Subscription-ID einer per `az` CLI angelegten Leseidentität für den Workspace-Pod; Übernahme in die Foundation offen) (ergänzt 31.10.2026). Der Host `operations-agent.<baseDomain>` wird vor dem Rollout mit `operationsApiHost` verglichen. ServiceAccount, ClusterRoleBinding und Migrations-Hook stammen aus dem Chart.

### 10. Status-UI als dritte App (ergänzt 31.10.2026)

`platform/catalog/platform-status/app.yaml` beschreibt die lesende Status-UI: Chart `acr: { repository: helm/platform-status }` (gebaut von `status-ui-package.yml`), Image aus `agent-build.yml` unter `acr:operations-agent`, kein Datenbaustein, `foundation:`-Secrets für den Zugang zur Datenbank `operations`, Prüfpfad `/` und `smoke-http` mit `CHECK_PATH: /status`. Sollzustand: `platform/environments/staging/platform-status.yaml`. Ausgerollt wird sie über denselben Push-Pfad von `app-deploy.yml`; Anforderungen an die UI selbst stehen im Change platform-status-ui.

## Risks / Trade-offs

- [Drift-Korrektur über erzwungenes Helm-Upgrade greift nicht für alle Felder] → Für die Abnahme zählt nur `replicas`; in Task 2.3 nachweisen, sonst setzt der Workflow die Replica-Zahl zusätzlich per `kubectl scale` auf den Sollwert.
- [Dynamisch erzeugte Azure-Disks liegen in der Node-Resource-Group und gehen mit dem Cluster verloren] → Akzeptiertes Staging-Risiko; im Datenverfahren von AFFiNE dokumentieren. Datenbankinhalte bleiben auf dem Foundation-Server erhalten.
- [Auto-Merge ohne Review] → Nur mit grünem Plan; Sollzustands-PRs ändern ausschließlich `platform/environments/`.
- [Externe AFFiNE-Lieferung verspätet sich] → Schema, App-Programm und Agenten-Deployment sind unabhängig davon; ein Platzhalter-Eintrag mit öffentlichem Image prüft den Pfad lokal.
- [C's Client ist beim ersten Cloud-Lauf nicht fertig] → Lokal gegen einen Stub entwickeln; mutierende Cloud-Läufe warten auf den echten Client.
- [Ein altes Image passt nicht zu migrierten Daten] → Rollback nur per PR nach Kompatibilitätsprüfung; Datenwiederherstellung bleibt manuell.

## Migration Plan

1. Schema v0.1 mit Beispielen und Validierung veröffentlichen; Lieferanforderungen an den AFFiNE-Kollegen übergeben.
2. App-Programm mit Datenbausteinen gegen einen lokalen Cluster und lokales PostgreSQL entwickeln.
3. Nach Foundation, C's Migrationen und Client: Workflows aktivieren, Repository-Einstellungen setzen lassen, ersten Agenten-Sollzustand ausrollen.
4. AFFiNE-Lieferung integrieren, Persistenznachweis führen, Fehlerszenarien einüben.
5. Fehlgeschlagene Releases behalten Workflow- und Operationsnachweise. Rückkehr nur per PR nach Datenkompatibilitätsprüfung.

## Open Questions

- Das Sperrschema muss vor dem ersten Erwerb existieren. Gelöst durch C's einmaligen Workflow `ops-migrate.yml` vor dem ersten Deployment; ohne Schema bricht der Sperrclient mit Hinweis ab. Stand 31.10.2026: `ops-migrate.yml` lief erfolgreich (Lauf 41070533875), seither erwerben alle Deploy-Läufe die Sperre.
- Geklärt: Laut gemeinsamer Übersicht nimmt A `operations-agent` vom Test an `ops-intake` aus; der Agent wird nur über `/healthz` an `ops-humans` überwacht.
- Form von A's Verträgen: Die Validierung erwartet `platform/schemas/conventions/conventions.json` mit `appId.pattern` und `environments` sowie `platform/schemas/foundation-outputs/foundation-outputs.schema.json` als JSON-Schema mit `properties`. Bis A sie liefert, scheitert `app-plan.yml` bei Änderungen am App-Pfad bewusst. Stand 31.10.2026: Beide Verträge liegen unter `platform/schemas/` vor; frühe Fehlschläge sind behoben (fehlende Backend-Eingaben der Composite Action in Lauf 41064377594, fehlendes Sperrschema in Lauf 41070187111 mit dem Hinweis auf `ops-migrate.yml`).
- Ob der Smoke-Test des AFFiNE-Kollegen Inhalte eines früheren Laufs wiederfinden kann (für Szenario 9) und welcher Konfigurationswert den nicht reparierbaren Fall auslöst, wird mit der Lieferung festgelegt. Stand 31.10.2026: Fehlerwert festgelegt (`values.affine.nodeOptions` = `--max-old-space-size=16`, `fault-invalid-config.yaml`); der Wiederfinde-Nachweis ist lokal auf kind geführt, in Staging offen.
- Ob das AFFiNE-Smoke-Test-Image aus der ACR oder öffentlich gezogen wird, hängt von der Lieferung ab. Geklärt 31.10.2026: aus der ACR (`acr:affine-smoke@sha256:…`), gebaut von `app-affine-sync.yml`.
