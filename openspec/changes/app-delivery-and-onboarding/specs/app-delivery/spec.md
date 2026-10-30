# Spec Delta

## Purpose

Apps werden aus versioniertem Sollzustand über eigene App-Stacks und einen gemeinsamen Deploy-Workflow ohne Azure-Identität bereitgestellt, über die gemeinsame Operations-Sperre koordiniert und fachlich geprüft.

## ADDED Requirements

### Requirement: Ein App-Stack je App und Umgebung
Jede App SHALL je Umgebung einen Pulumi-Stack `organization/app-<app-id>/<env>` aus dem gemeinsamen Programm unter `platform/infra/app-runtime/` besitzen. Der Stack SHALL Foundation-Werte ausschließlich per `StackReference` auf `organization/foundation/<env>` lesen. Eine App-Aktualisierung SHALL weder einen Foundation-Lauf noch Änderungen an anderen App-Stacks auslösen.

#### Scenario: AFFiNE unabhängig aktualisieren
- **WHEN** AFFiNE eine neue Version erhält
- **THEN** aktualisiert der Deploy-Workflow nur `organization/app-affine/staging`
- **AND** bleiben Foundation-Stack und `organization/app-operations-agent/staging` unverändert

### Requirement: Keine Azure-Ressourcen im App-Stack
Ein App-Stack SHALL nur Kubernetes-Objekte, Helm-Releases und Datenbankobjekte über das PostgreSQL-Protokoll verwalten. Er SHALL weder einen Azure-Provider noch eine Azure-Anmeldung verwenden. Verfügbarkeitstests und Alarmregeln erzeugt der Foundation-Stack aus dem Katalog.

#### Scenario: Preview eines App-Stacks
- **WHEN** ein Preview für `organization/app-affine/staging` läuft
- **THEN** enthält es nur Ressourcen der Kubernetes-, PostgreSQL- und Random-Provider
- **AND** keine Ressource eines Azure-Providers

### Requirement: Namespace, Image-Pull-Secret und Ingress
Der App-Stack SHALL den Namespace `<app-id>-<env>`, darin ein Image-Pull-Secret aus dem ACR-Admin-User der Foundation-Ausgaben und eine Ingress-Regel für `<app-id>.<baseDomain>` mit Let's-Encrypt-Zertifikat auf das Ingress-Ziel des Katalogeintrags anlegen. Schlägt die Zertifikatsausstellung fehl, ist HTTP über denselben Host der dokumentierte Fallback für Staging.

#### Scenario: Image aus der ACR
- **WHEN** ein Chart ein Image aus der ACR der Foundation referenziert
- **THEN** startet der Pod mit dem Image-Pull-Secret seines Namespace ohne Rollenzuweisung

#### Scenario: App erreichbar
- **WHEN** der App-Stack für AFFiNE ausgerollt ist
- **THEN** ist AFFiNE unter `https://affine.<baseDomain>` ohne DNS-Änderung erreichbar

### Requirement: Secrets im Namespace
Der App-Stack SHALL alle Secret-Referenzen eines Katalogeintrags auflösen und als Kubernetes-Secret `<app-id>-platform` im Namespace anlegen. `foundation:`-Referenzen SHALL aus der `StackReference` stammen, `app:`-Referenzen aus Datenbausteinen oder vom App-Stack generierten Zufallswerten. Test-Secrets aus `smokeTest.secrets` SHALL im selben Secret liegen; ein Smoke-Test-Job SHALL nur diese Schlüssel erhalten. Generierte Werte SHALL über Läufe hinweg stabil bleiben.

#### Scenario: Erneuter Lauf
- **WHEN** der App-Stack von AFFiNE ohne Sollzustandsänderung erneut ausgerollt wird
- **THEN** bleiben generierte Passwörter unverändert
- **AND** behält AFFiNE den Zugriff auf Datenbank und Redis

### Requirement: Datenbaustein postgres-database
Für `postgres-database` SHALL der App-Stack über das PostgreSQL-Protokoll, z. B. mit dem Pulumi-postgresql-Provider, mit dem Admin-Login aus den Foundation-Ausgaben eine eigene Datenbank und einen eigenen Login auf dem gemeinsamen PostgreSQL-Server anlegen. Verbindungen SHALL TLS verwenden. Zugangsdaten SHALL als `app:`-Secrets bereitstehen. Die Datenbank `operations` SHALL unverändert bleiben.

#### Scenario: AFFiNE-Datenbank anlegen
- **WHEN** der App-Stack von AFFiNE erstmals ausgerollt wird
- **THEN** existieren Datenbank und Login für AFFiNE auf dem Foundation-Server
- **AND** wurde dafür keine Azure-Anmeldung benötigt

### Requirement: Datenbaustein redis
Für `redis` SHALL der App-Stack Redis mit fester, per Digest referenzierter Image-Version im App-Namespace bereitstellen und das Passwort als `app:`-Secret liefern. Redis SHALL als flüchtig gelten; dauerhafte App-Daten SHALL in `postgres-database` oder `persistent-volume` liegen.

#### Scenario: Redis neu gestartet
- **WHEN** der Redis-Pod von AFFiNE neu startet
- **THEN** bleiben Dokumente und Anhänge erhalten, weil sie nicht in Redis liegen

### Requirement: Datenbaustein persistent-volume
Für `persistent-volume` SHALL der App-Stack einen PersistentVolumeClaim mit Azure-Disk-StorageClass des Clusters (ReadWriteOnce) und der Größe aus dem Sollzustand anlegen. Den Datenträger SHALL der CSI-Treiber des Clusters erzeugen, nicht eine Azure-Ressource im App-Stack.

#### Scenario: Anhänge nach Pod-Neustart
- **WHEN** der AFFiNE-Pod gelöscht und neu geplant wird
- **THEN** bindet der neue Pod denselben PersistentVolumeClaim
- **AND** sind zuvor gespeicherte Dateien vorhanden

### Requirement: Schutz persistenter Daten
App-Datenbank, Login und PersistentVolumeClaim SHALL vor impliziter Löschung geschützt sein, z. B. mit `protect` oder `retainOnDelete`. Das Entfernen eines Katalogeintrags oder einer Sollzustandsdatei SHALL keinen Stack-Destroy auslösen. Außerbetriebnahme SHALL ein eigener, ausdrücklich ausgelöster und dokumentierter Vorgang sein.

#### Scenario: Sollzustandsdatei versehentlich entfernt
- **WHEN** `platform/environments/staging/affine.yaml` auf `main` gelöscht wird
- **THEN** verändert der Deploy-Workflow den Stack nicht
- **AND** meldet er den notwendigen separaten Außerbetriebnahme-Vorgang

#### Scenario: Datenbaustein aus dem Eintrag entfernt
- **WHEN** ein Plan die Löschung einer geschützten Datenbank oder eines PVC enthält
- **THEN** scheitert der Plan, bevor eine Zieländerung beginnt

### Requirement: Werte-Vertrag für Charts
Das App-Programm SHALL jedem Chart zusätzlich zu den Werten aus dem Sollzustand `image.repository`, `image.digest`, `secretName`, `imagePullSecretName`, `rolloutMarker` und einen Block `platform` (Host, URL, PVC-Name, Größen, Release-Referenz) übergeben. Diese Schlüssel SHALL im Sollzustand reserviert sein. Charts SHALL weder Secrets noch Ingress-Regeln, PVCs oder Datenbanken anlegen und `rolloutMarker` nicht in Pod-Templates verwenden.

#### Scenario: Reservierter Wert im Sollzustand
- **WHEN** ein Sollzustand `values.secretName` setzt
- **THEN** scheitert die Validierung mit dem betroffenen Feld

#### Scenario: Erneuter Lauf ohne Änderung
- **WHEN** der Deploy-Workflow ohne Sollzustandsänderung mit neuer `operationId` läuft
- **THEN** führt Helm ein Upgrade aus, ohne laufende Pods neu zu starten

### Requirement: Abweichungen vom Sollzustand zurücksetzen
Jeder Rollout SHALL die Kubernetes-Objekte einer App auf den Sollzustand bringen, auch wenn sich die Sollzustandsdatei nicht geändert hat. Manuelle Abweichungen wie eine geänderte Replica-Zahl SHALL dabei zurückgesetzt werden, ohne Datenbank oder Volumes zu verändern.

#### Scenario: AFFiNE auf 0 Replicas
- **WHEN** das AFFiNE-Deployment manuell auf 0 Replicas skaliert ist und der Deploy-Workflow ohne Sollzustandsänderung läuft
- **THEN** läuft AFFiNE danach mit der Replica-Zahl aus dem Sollzustand
- **AND** sind Datenbank und PersistentVolumeClaim unverändert

### Requirement: Validierung vor Rollout
Vor jeder Zieländerung SHALL der Deploy-Workflow Katalog und Sollzustand gegen das Schema, Secret-Referenzen gegen das Foundation-Ausgabeschema und den Pulumi-Plan prüfen. Schlägt eine Prüfung fehl, SHALL keine Zieländerung beginnen. GitHub Actions SHALL in Phase 1 der einzige automatische Weg sein, den Git-Sollzustand auszurollen.

#### Scenario: Ungültiger Sollzustand
- **WHEN** eine Sollzustandsänderung die Schema- oder Planprüfung nicht besteht
- **THEN** beginnt kein Rollout
- **AND** nennt die Workflow-Zusammenfassung die fehlgeschlagenen Prüfungen

### Requirement: Plan-Prüfung für Pull Requests
Der Workflow `.github/workflows/app-plan.yml` SHALL bei Pull Requests mit Änderungen unter `platform/catalog/`, `platform/environments/`, `platform/schemas/app-catalog/` oder `platform/infra/app-runtime/` die Validierung und ein Pulumi-Preview der betroffenen App-Stacks ausführen. Sein Ergebnis-Job `Plan-Ergebnis` SHALL als erforderlicher Status-Check für `main` konfiguriert sein.

#### Scenario: Pull Request ohne Änderungen am App-Pfad
- **WHEN** ein Pull Request nur Dateien außerhalb des App-Pfads ändert
- **THEN** berichtet `app-plan.yml` trotzdem den Check `Plan-Ergebnis` und endet sofort erfolgreich

#### Scenario: Grüner Plan
- **WHEN** ein Pull Request den AFFiNE-Sollzustand gültig ändert
- **THEN** meldet `app-plan.yml` Validierung und Preview erfolgreich
- **AND** zeigt die Zusammenfassung die geplanten Änderungen ohne Secret-Werte

### Requirement: Deploy-Workflow
Der Workflow `.github/workflows/app-deploy.yml` SHALL bei Push auf `main` mit Änderungen unter `platform/environments/` genau die betroffenen App-Stacks ausrollen. Er SHALL zusätzlich per `workflow_dispatch` mit den Eingaben `appId`, `environment`, `operationId`, `actionId` und `force` startbar sein. Fehlt `operationId`, SHALL der Workflow eine erzeugen.

#### Scenario: Push auf main
- **WHEN** ein Merge `platform/environments/staging/affine.yaml` ändert
- **THEN** rollt `app-deploy.yml` nur AFFiNE in `staging` aus

#### Scenario: Start durch den Agenten
- **WHEN** der Agent `app-deploy.yml` per `workflow_dispatch` mit `appId` `affine`, `environment` `staging` und seiner `operationId` startet
- **THEN** rollt der Workflow den aktuellen Sollzustand von AFFiNE unter dieser `operationId` aus

### Requirement: Korrelierbare Workflow-Läufe
Der `run-name` jedes Deploy-Laufs SHALL App-ID, Umgebung, `operationId` und, falls übergeben, `actionId` enthalten. Der Workflow SHALL die URL seines Laufs über den Operations-Client als externe Referenz der Operation protokollieren, sodass der Agent einen von ihm gestarteten Lauf nach einem eigenen Neustart eindeutig wiederfindet.

#### Scenario: Agent startet neu nach Workflow-Start
- **WHEN** der Agent nach dem Dispatch und vor dem Ende des Laufs neu startet
- **THEN** findet er den Lauf über `actionId` im `run-name` oder im Aktionsprotokoll
- **AND** startet er keinen zweiten Lauf für dieselbe Operation

### Requirement: Zugänge ohne Azure-Identität
Plan- und Deploy-Workflow SHALL sich nur mit den Repo-Secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY` und `PULUMI_CONFIG_PASSPHRASE` anmelden und Admin-Kubeconfig, ACR-Admin-User und PostgreSQL-Zugang über die Composite Action `.github/actions/foundation-outputs` beziehen. Sie SHALL keine Azure-Anmeldung ausführen.

#### Scenario: Lauf auf GitHub-hosted Runner
- **WHEN** `app-deploy.yml` auf einem GitHub-hosted Runner läuft
- **THEN** liest er die Foundation-Ausgaben, rollt Kubernetes-Objekte aus und legt Datenbankobjekte an
- **AND** enthält der Lauf keinen Azure-Login-Schritt

#### Scenario: Inkompatibler Ausgabevertrag
- **WHEN** die Composite Action eine andere `contractVersion` als erwartet meldet
- **THEN** bricht der Workflow vor jeder Zieländerung ab

### Requirement: Sollzustand per Pull Request ändern
Der wiederverwendbare Workflow `.github/workflows/app-update-desired-state.yml` (`workflow_call`) SHALL Image-Digest (Eingabe `imageDigest`, Repository standardmäßig `acr:<app-id>`) und Chart-Version in `platform/environments/<env>/<app-id>.yaml` setzen und mit einem Token der GitHub App des Agenten (Rückfall `OPS_GITHUB_TOKEN`) Commit und Pull Request mit Auto-Merge anlegen. `GITHUB_TOKEN` SHALL dafür nicht verwendet werden, weil damit ausgelöste Ereignisse keine weiteren Workflows starten.

#### Scenario: Neues Agentenpaket
- **WHEN** der Agenten-Build den wiederverwendbaren Workflow mit neuem Image-Digest und neuer Chart-Version aufruft
- **THEN** entsteht ein Pull Request, dessen Commit Quell-Commit und Artefaktreferenzen nennt
- **AND** wird er nach grünem `app-plan.yml` gemerged, worauf `app-deploy.yml` startet

#### Scenario: Roter Plan
- **WHEN** die Plan-Prüfung für einen solchen Pull Request fehlschlägt
- **THEN** bleibt der Pull Request offen und es startet kein Deployment

### Requirement: Gemeinsame Sperre für App-Änderungen
Vor der ersten Zieländerung SHALL der Deploy-Workflow über den Operations-Client die Sperre für App und Umgebung unter seiner `operationId` erwerben, sie während des Rollouts erneuern, vor jedem ändernden Schritt den Besitz prüfen und sie am Ende freigeben. Eine Concurrency-Gruppe des Workflows SHALL die Sperre nur ergänzen, nicht ersetzen.

#### Scenario: Besitz verloren
- **WHEN** die Besitzprüfung vor einem ändernden Schritt fehlschlägt
- **THEN** unterlässt der Workflow weitere Zieländerungen
- **AND** endet er als fehlgeschlagen mit Verweis auf die Operation

### Requirement: Konfliktregel für Workflows
Ist die Sperre durch eine andere Operation belegt, SHALL der Deploy-Workflow bis zu 15 Minuten warten und danach ohne Zieländerung abbrechen. Die Workflow-Zusammenfassung SHALL die sperrende Operation nennen.

#### Scenario: Deployment konkurriert mit Agentenmaßnahme
- **WHEN** der Agent AFFiNE in `staging` gerade neu startet und ein Deploy-Lauf für dieselbe App beginnt
- **THEN** wartet der Lauf auf die Sperre und führt keine parallele Änderung aus
- **AND** bricht er nach 15 Minuten ohne Sperre mit dem Namen der sperrenden Operation ab

### Requirement: Keine Umgehung bei Ausfall der Agenten-API
Der Deploy-Workflow SHALL die Sperre über den Operations-Client direkt in PostgreSQL erwerben, ohne die Agenten-API zu benötigen. Ein Ausfall von Agenten-API oder Worker SHALL kein Grund sein, die Sperre zu umgehen.

#### Scenario: Agenten-API ausgefallen
- **WHEN** Agenten-API und Worker nicht laufen, PostgreSQL aber erreichbar ist
- **THEN** erwirbt der Workflow die Sperre über den Operations-Client
- **AND** wird `force` abgelehnt

### Requirement: Break-glass bei nicht erreichbarem PostgreSQL
Ein Deploy-Lauf ohne Sperre SHALL nur per `workflow_dispatch` mit `force` (Aufruf mit `--force`) und nur bei nicht erreichbarem PostgreSQL möglich sein. Der Lauf SHALL das in der Workflow-Zusammenfassung vermerken. Der Vorgang SHALL nach Wiederherstellung nach dem dokumentierten Verfahren ins Aktionsprotokoll nachgetragen werden.

#### Scenario: PostgreSQL nicht erreichbar
- **WHEN** PostgreSQL nicht erreichbar ist und ein Mensch `app-deploy.yml` mit `force` startet
- **THEN** rollt der Workflow ohne Sperre aus
- **AND** vermerkt die Zusammenfassung Break-glass, Grund und `operationId` für den Nachtrag

#### Scenario: PostgreSQL nicht erreichbar ohne force
- **WHEN** PostgreSQL nicht erreichbar ist und der Lauf ohne `force` startet
- **THEN** bricht er vor jeder Zieländerung ab

### Requirement: Abgeschlossene und geprüfte Deployments
Ein Deployment SHALL erst als erfolgreich gelten, wenn der Rollout abgeschlossen ist und der Smoke-Test der App als Kubernetes-Job im App-Namespace mit `TARGET_URL` `https://<app-id>.<baseDomain>` erfolgreich war. Das Deployment-Ergebnis SHALL Operations-ID, Workflow-Lauf, App, Umgebung, Release-Referenz und Verifikationsergebnis enthalten und in Zusammenfassung und Aktionsprotokoll stehen.

#### Scenario: Workflow nur gestartet
- **WHEN** ein Lauf angenommen wurde, Rollout oder Smoke-Test aber noch laufen
- **THEN** wird das Deployment nicht als erfolgreich gemeldet

#### Scenario: Smoke-Test scheitert
- **WHEN** der Rollout endet, der Smoke-Test aber fehlschlägt
- **THEN** endet der Lauf als fehlgeschlagen mit dem Verifikationsergebnis
- **AND** ist ein HTTP-Erfolg allein kein Ersatz für den fachlichen Nachweis

### Requirement: Getrennte Releases und Datenkompatibilität
Foundation-, App- und Agenten-Releases SHALL getrennt ausführbar und protokollierbar sein. Ein Rollback SHALL als Pull Request auf einen früheren Sollzustand erfolgen und erst nach dokumentierter Prüfung der Datenkompatibilität. Ein Image-Rollback SHALL nicht als Datenwiederherstellung gelten.

#### Scenario: Agentenprozess beim eigenen Release beendet
- **WHEN** ein Agenten-Release den bisherigen Worker ersetzt
- **THEN** führt GitHub Actions den Release unabhängig vom beendeten Prozess fort
- **AND** bleibt derselbe Lauf nach dem Neustart über `operationId` auffindbar

#### Scenario: Rückkehr nach Datenmigration
- **WHEN** ein AFFiNE-Release Daten migriert hat und die vorherige Version gewünscht wird
- **THEN** wird der Rollback-PR erst nach dokumentierter Kompatibilitätsprüfung gemerged

### Requirement: Betriebsverfahren des App-Pfads
Unter `platform/operations/apps/` SHALL dokumentiert sein, wie Apps ausgerollt, zurückgerollt, gestoppt und wieder gestartet werden, wie Break-glass ausgelöst und nachgetragen wird und welche Repository-Einstellungen (erforderlicher Status-Check, Auto-Merge) der Sollzustandsfluss voraussetzt.

#### Scenario: Wiederanlauf nach Cluster-Verlust
- **WHEN** der Foundation-Stack einen neuen Cluster bereitgestellt hat
- **THEN** lassen sich AFFiNE und Agent nach dem Verfahren mit `app-deploy.yml` wieder ausrollen

## Abgrenzung

B implementiert App-Programm und `app-*.yml`-Workflows. A liefert Foundation-Ausgaben und Composite Action, C den Operations-Client mit Sperre und Aktionsprotokoll, D den Dispatch des Deploy-Workflows, dessen Beobachtung und den Aufruf des Sollzustands-Workflows aus dem Agenten-Build. Mit dem Start eines Laufs geht die Verantwortung für das tatsächliche Deployment auf den Deploy-Workflow über.
