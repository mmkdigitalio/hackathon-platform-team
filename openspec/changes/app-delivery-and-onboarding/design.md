# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md), Verhalten in [app-catalog](specs/app-catalog/spec.md), [app-delivery](specs/app-delivery/spec.md) und [app-onboarding](specs/app-onboarding/spec.md). Verbindlich sind die Hackathon-Entscheidungen E1 bis E10 und die Verträge in der [gemeinsamen Übersicht](../../PARALLEL-IMPLEMENTATION.md) sowie der Foundation-Ausgabevertrag in [shared-platform-runtime](../platform-foundation/specs/shared-platform-runtime/spec.md).

Prägend sind drei Vorgaben: Ein Monorepo (E1), AFFiNE als einzige Fach-App aus externer Lieferung (E2) und GitHub Actions ohne Azure-Identität (E4 bis E6). Der App-Pfad arbeitet deshalb nur mit Pulumi-State-Key, Passphrase, Admin-Kubeconfig, ACR-Admin-User und PostgreSQL-Admin-Login.

## Goals / Non-Goals

Goals:

- Ein App-Pfad für AFFiNE und den Betriebsagenten, ohne app-spezifischen Code im App-Programm.
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
| `platform/catalog/<app-id>/app.yaml` | Umgebungsunabhängiger Eintrag; `affine/` vom AFFiNE-Kollegen, `operations-agent/` von B |
| `platform/environments/<env>/<app-id>.yaml` | Sollzustand je Umgebung |
| `platform/infra/app-runtime/` | Pulumi-Programm für alle App-Stacks, Datenbausteine, generisches Smoke-Test-Image `smoke-http/` |
| `platform/operations/apps/` | Betriebsverfahren des App-Pfads, Fehlerskripte unter `affine/` |
| `.github/workflows/app-*.yml` | `app-plan.yml`, `app-deploy.yml`, `app-update-desired-state.yml`, Build des Smoke-Test-Images |

### 2. Katalog in zwei Dateien

`app.yaml` beschreibt, was eine App ist und braucht; die Sollzustandsdatei, welche Version mit welchen Werten in einer Umgebung läuft. Versionswechsel berühren so nur `platform/environments/`, und der Foundation-Stack liest für Verfügbarkeitstests nur `app.yaml`. Namespace und Host werden nach der Konvention aus `platform-bootstrap` abgeleitet und sind deshalb keine Felder; das verhindert Abweichungen zwischen Katalog, Ingress und Verfügbarkeitstest.

Skizze `app.yaml` (Feldnamen werden mit dem Schema festgelegt):

```yaml
schemaVersion: "0.1"
appId: affine
source: { repository: mmkdigitalio/hackathon-platform-affine, upstream: toeverything/AFFiNE }
chart: { oci: <registry>/<chart> }          # oder git: { repository, path }
ingress: { service: affine, port: 3010 }
dataBlocks: [postgres-database, redis, persistent-volume]
generatedSecrets: [affine-private-key]
secrets: { DATABASE_PASSWORD: app:postgres-password, AFFINE_PRIVATE_KEY: app:affine-private-key }
availability: { path: /info }
smokeTest: { image: <image>@sha256:..., secrets: { ... } }
runbook: { repository: ..., path: ..., commit: <sha> }
allowedActions: [restart, redeploy]
dataProcedures: { migration: ..., backup: ..., restore: not-applicable }
```

Konfigurationswerte für das Chart sind im Sollzustand frei, weil sie dem Chart gehören. Gegen ein vorhandenes `values.schema.json` des Charts wird geprüft. Das nicht reparierbare Fehlerszenario nutzt genau diese Lücke: ein schemagültiger, fachlich ungültiger Wert.

### 3. Datenbausteine und Secrets

| Baustein | Umsetzung | `app:`-Secrets |
| --- | --- | --- |
| `postgres-database` | Pulumi-postgresql-Provider mit `postgresAdminUser`/`postgresAdminPassword`, `sslmode=require`; Datenbank und Login nach App-ID (Bindestriche als Unterstriche) | `postgres-host`, `postgres-database`, `postgres-user`, `postgres-password`, `postgres-url` |
| `redis` | Redis-Chart mit fester Version im App-Namespace, ohne Persistenz | `redis-host`, `redis-password` |
| `persistent-volume` | PVC mit StorageClass `managed-csi`, ReadWriteOnce | keine |

Der App-Stack schreibt alle aufgelösten Referenzen in das Secret `<app-id>-platform`, dessen Name dem Chart als Wert übergeben wird. Generierte Werte entstehen mit `random`-Ressourcen und liegen verschlüsselt im Stack-State, sodass sie stabil bleiben. Datenbank, Login und PVC erhalten `protect`.

### 4. Helm-Ausrollung mit Hooks und Drift-Korrektur

Das Agenten-Chart braucht echte Helm-Hooks für Migrationen; das App-Programm nutzt deshalb `helm.v3.Release` statt einer in Pulumi-Ressourcen aufgelösten Chart-Variante. Ein `Release` aktualisiert aber nur bei geänderten Eingaben. Damit die bekannte Reparatur (0 Replicas) ohne Sollzustandsänderung greift, übergibt jeder Lauf eine Rollout-Markierung mit `operationId` als Release-Wert, die kein Pod-Template verändert. Helm führt dadurch ein Upgrade mit Drei-Wege-Merge aus und setzt manuell geänderte Felder auf den Sollzustand zurück. Die Markierung wird in Task 2.3 gegen genau dieses Verhalten geprüft.

### 5. Workflows und Versionsfluss

- `app-plan.yml` (Pull Request): Validierung, Abgleich der Secret-Referenzen mit `platform/schemas/foundation-outputs/`, `pulumi preview` der betroffenen Stacks. Erforderlicher Status-Check für `main`.
- `app-deploy.yml` (Push auf `main` unter `platform/environments/**`, `workflow_dispatch` mit `appId`, `environment`, `operationId`, `actionId`, `force`): `run-name` enthält App, Umgebung, `operationId` und `actionId`. Ablauf: Foundation-Ausgaben, Validierung, Host-Prüfung, Sperre, `pulumi up --refresh`, Rollout-Prüfung, Smoke-Job, Ergebnis, Freigabe. Eine Concurrency-Gruppe je App und Umgebung ergänzt die Sperre.
- `app-update-desired-state.yml` (`workflow_call`): setzt Digest und Chart-Version, legt Branch, Commit und Pull Request mit `OPS_GITHUB_TOKEN` an und aktiviert Auto-Merge. Aufgerufen vom Agenten-Build (`agent-*.yml`). Mit `GITHUB_TOKEN` würden Pull Request und Merge keine weiteren Workflows auslösen.
- Änderungen an `platform/catalog/` oder `platform/infra/app-runtime/` lösen kein automatisches Deployment aus; sie wirken mit dem nächsten Lauf oder einem manuellen `workflow_dispatch`.

AFFiNE-Versionen kommen per manuellem Pull Request. Voraussetzung im Repository: Auto-Merge erlaubt, `app-plan.yml` als erforderlicher Check, keine Pflicht-Reviews auf Sollzustands-PRs. Diese Einstellungen dokumentiert B unter `platform/operations/apps/`, gesetzt werden sie von einer Person mit Admin-Rechten.

Zugänge: Repo-Secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE`; alles Weitere über `.github/actions/foundation-outputs`. Der App-Stack legt je Namespace ein Image-Pull-Secret aus `acrUsername`/`acrPassword` an.

### 6. Sperre, Konfliktregel und Break-glass

Der Deploy-Workflow installiert C's Operations-Client aus `operations-agent/` und greift damit direkt auf PostgreSQL zu, nicht auf die Agenten-API. Er erwirbt die Sperre unter `operationId`, wartet bei Belegung bis zu 15 Minuten, erneuert während des Rollouts, prüft Besitz vor `pulumi up` und vor dem Smoke-Job und protokolliert Lauf-URL und Ergebnis als Aktion. Der Agent startet den Workflow, hält dabei keine Ziel-Sperre und übergibt seine `operationId`.

Break-glass (E9): Nur mit `force` und nur wenn ein Verbindungstest zu PostgreSQL scheitert. Die Zusammenfassung vermerkt Grund und `operationId`; der Nachtrag ins Aktionsprotokoll ist ein dokumentierter manueller Schritt mit C's CLI. Praktisch betrifft das vor allem den Agenten: Ein AFFiNE-Lauf scheitert ohne PostgreSQL ohnehin am Baustein `postgres-database`.

### 7. Smoke-Test-Vertrag

Der Deploy-Workflow startet den Smoke-Test als Job im App-Namespace mit `TARGET_URL=https://<app-id>.<baseDomain>` und den Test-Secrets aus dem Katalog, wartet auf das Ende, liest stdout und ergänzt `appId`, `environment`, `release` aus Namespace-Annotationen und Pod-Status sowie `checkRef` als Image-Digest. Exit-Code und `success` müssen übereinstimmen. Für den Agenten liefert B ein generisches Image `smoke-http`, das `TARGET_URL` plus Prüfpfad abfragt und das Ergebnis im Vertragsformat schreibt.

### 8. AFFiNE als externe Lieferung

Der AFFiNE-Kollege liefert Chart, Werte, Smoke-Test-Image und Runbook und legt `platform/catalog/affine/app.yaml` nach Schema an. B validiert die Lieferung, legt den Sollzustand an und integriert sie. AFFiNE speichert Dokumente in PostgreSQL und Anhänge im Dateisystem; Redis dient als Cache und Queue.

Fehlerszenarien:

| Fall | Auslösen | Erwartung | Rücksetzen |
| --- | --- | --- | --- |
| Reparierbar | Skript skaliert `affine` in `affine-staging` auf 0 | Verfügbarkeitsalarm, Agent startet `app-deploy.yml`, Smoke-Test grün | `app-deploy.yml` |
| Nicht reparierbar | Commit mit ungültigem Konfigurationswert in `platform/environments/staging/affine.yaml` | Rollout scheitert, Alarm, `restart`/`redeploy` wirkungslos, `needs_attention` mit Commit | Revert-PR |

Beide berühren weder Datenbank noch PVC noch Secrets. Damit der zweite Fall zu einem Ausfall statt zu einem hängenden Rolling Update führt, muss das AFFiNE-Chart wegen des ReadWriteOnce-Volumes die Strategie `Recreate` verwenden.

### 9. Agentenpaket

`platform/catalog/operations-agent/app.yaml` referenziert D's Chart aus `operations-agent/deploy/` bzw. dessen veröffentlichte Version, `smoke-http` mit Prüfpfad `/healthz` und ausschließlich `foundation:`-Secrets; einen Datenbaustein braucht der Agent nicht, weil er die Foundation-Datenbank `operations` nutzt. Der Host `operations-agent.<baseDomain>` wird vor dem Rollout mit `operationsApiHost` verglichen. ServiceAccount, ClusterRoleBinding und Migrations-Hook stammen aus dem Chart.

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

- Das Sperrschema muss vor dem ersten Erwerb existieren. Gelöst durch C's einmaligen Workflow `ops-migrate.yml` vor dem ersten Deployment; ohne Schema bricht der Sperrclient mit Hinweis ab.
- Laut `shared-platform-runtime` erzeugt der Foundation-Stack für jeden Katalogeintrag einen Test an `ops-intake`, also auch für `operations-agent`, zusätzlich zum `/healthz`-Test an `ops-humans`. Mit A zu klären, ob `operations-agent` ausgenommen wird.
- Ob der Smoke-Test des AFFiNE-Kollegen Inhalte eines früheren Laufs wiederfinden kann (für Szenario 9) und welcher Konfigurationswert den nicht reparierbaren Fall auslöst, wird mit der Lieferung festgelegt.
- Ob das AFFiNE-Smoke-Test-Image aus der ACR oder öffentlich gezogen wird, hängt von der Lieferung ab.
