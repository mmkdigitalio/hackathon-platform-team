# Spec Delta

## Purpose

Der gemeinsame App-Pfad wird an AFFiNE als einziger Fach-App und am Betriebsagenten praktisch nachgewiesen, einschließlich zweier kontrollierter, datensicherer Fehlerszenarien.

## ADDED Requirements

### Requirement: Lieferumfang des AFFiNE-Pakets
Die externe Lieferung aus `mmkdigitalio/hackathon-platform-affine` SHALL einen schemakonformen Katalogeintrag `platform/catalog/affine/app.yaml`, ein Chart mit fester Version, ein Smoke-Test-Image nach Smoke-Test-Vertrag und ein Runbook mit Commit-SHA umfassen. Chart-Inhalt, Smoke-Test-Logik und Runbook-Inhalt SHALL nicht Teil dieser Spec sein.

#### Scenario: Unvollständige Lieferung
- **WHEN** der AFFiNE-Katalogeintrag kein Smoke-Test-Image mit Digest oder kein Runbook referenziert
- **THEN** scheitert die Validierung mit dem fehlenden Feld
- **AND** wird AFFiNE nicht ausgerollt

### Requirement: AFFiNE über den App-Pfad
AFFiNE SHALL als unverändertes Upstream-Release über dasselbe App-Programm sowie denselben Plan- und Deploy-Workflow wie jede andere App bereitgestellt werden, mit den Datenbausteinen `postgres-database`, `redis` und `persistent-volume`. Ein AFFiNE-Fork oder AFFiNE-spezifischer Code im App-Programm SHALL nicht erforderlich sein.

#### Scenario: Erstes AFFiNE-Deployment
- **WHEN** Katalogeintrag und `platform/environments/staging/affine.yaml` auf `main` gemerged sind
- **THEN** rollt `app-deploy.yml` den Stack `organization/app-affine/staging` aus
- **AND** ist AFFiNE unter `affine.<baseDomain>` erreichbar und der Smoke-Test erfolgreich

### Requirement: AFFiNE-Versionswechsel per Pull Request
Ein AFFiNE-Versionswechsel SHALL als manueller Pull Request auf `platform/environments/<env>/affine.yaml` erfolgen und Plan-Prüfung, Merge und Deploy-Workflow durchlaufen. Datenmigrationen der neuen Version SHALL vorher im Datenverfahren des Katalogeintrags beschrieben sein.

#### Scenario: Neue AFFiNE-Version
- **WHEN** ein Teammitglied einen Pull Request mit neuem Image-Digest und neuer Chart-Version öffnet
- **THEN** läuft `app-plan.yml`, und nach dem Merge rollt `app-deploy.yml` die Version aus

### Requirement: AFFiNE-Daten überstehen Neustarts
Für Abnahmeszenario 9 SHALL der Smoke-Test des AFFiNE-Pakets Anmeldung, Dokument und Anhang prüfen. Nach einem kontrollierten Neustart aller AFFiNE- und Redis-Pods SHALL ein erneuter Lauf dieselben Inhalte abrufen können. Beide Ergebnisse SHALL die Release-Referenz enthalten.

#### Scenario: AFFiNE-Neustart
- **WHEN** ein Smoke-Test-Lauf Dokument und Anhang gespeichert hat und danach alle AFFiNE- und Redis-Pods neu starten
- **THEN** findet ein zweiter Smoke-Test-Lauf beide Inhalte wieder
- **AND** nennen beide Ergebnisse dasselbe Release und die Umgebung `staging`

### Requirement: Reparierbares Fehlerszenario
Ein dokumentiertes Skript unter `platform/operations/apps/affine/` SHALL das AFFiNE-Deployment in `staging` auf 0 Replicas skalieren. Der Verfügbarkeitstest der Foundation SHALL daraufhin den Alarm `alert-affine-staging-availability` an `ops-intake` auslösen. Die bekannte Reparatur SHALL ein Lauf von `app-deploy.yml` per `workflow_dispatch` sein, der den Sollzustand wiederherstellt.

#### Scenario: Fehler auslösen und beheben
- **WHEN** das Skript AFFiNE auf 0 Replicas skaliert
- **THEN** schlägt der Smoke-Test fehl und es entsteht ein Fired-Ereignis für `affine` in `staging`
- **AND** laufen nach `app-deploy.yml` mit `operationId` wieder die Replicas aus dem Sollzustand, der Smoke-Test ist erfolgreich und der Alarm wird aufgelöst

#### Scenario: Rücksetzen ohne Agent
- **WHEN** ein Mensch `app-deploy.yml` für AFFiNE manuell startet
- **THEN** ist der Normalzustand wiederhergestellt und Datenbank und Volume sind unverändert

### Requirement: Nicht reparierbares Fehlerszenario
Ein dokumentierter Commit SHALL in `platform/environments/staging/affine.yaml` einen schemagültigen, aber ungültigen AFFiNE-Konfigurationswert setzen, sodass AFFiNE nicht mehr erreichbar ist. Neustart und erneutes Deployment SHALL den Fehler reproduzieren. Erwartetes Ergebnis des Agenten ist `needs_attention` mit Verweis auf diesen Commit. Rücksetzen SHALL per Revert-Pull-Request erfolgen.

#### Scenario: Erlaubte Maßnahmen helfen nicht
- **WHEN** der Agent nach dem Alarm `restart` und `redeploy` ausgeführt hat
- **THEN** bleibt der Smoke-Test negativ
- **AND** ist der auslösende Commit aus der Git-Historie der Sollzustandsdatei ermittelbar

#### Scenario: Rücksetzen per Revert
- **WHEN** der Revert des Commits gemerged ist
- **THEN** rollt `app-deploy.yml` den vorherigen Wert aus und der Smoke-Test ist erfolgreich

### Requirement: Fehlerszenarien datensicher und auf Staging begrenzt
Auslösen und Rücksetzen beider Fehlerszenarien SHALL unter `platform/operations/apps/affine/` dokumentiert und nur für `staging` ausführbar sein. Sie SHALL weder Datenbank noch PersistentVolumeClaim noch Secrets von AFFiNE verändern.

#### Scenario: Falsche Umgebung
- **WHEN** das Fehlerskript mit einer anderen Umgebung als `staging` aufgerufen wird
- **THEN** bricht es ohne Änderung ab

#### Scenario: Daten nach beiden Szenarien
- **WHEN** beide Fehlerszenarien ausgelöst und zurückgesetzt wurden
- **THEN** findet der Smoke-Test zuvor gespeicherte Dokumente und Anhänge wieder

### Requirement: Betriebsagent über den App-Pfad
Der Betriebsagent SHALL als App `operations-agent` mit Katalogeintrag `platform/catalog/operations-agent/app.yaml` und Sollzustand je Umgebung über dasselbe App-Programm und denselben Deploy-Workflow ausgerollt werden. Image und Chart SHALL unverändert aus dem Agenten-Build referenziert werden. API und Worker SHALL getrennte Deployments sein.

#### Scenario: Agenten-Release ausrollen
- **WHEN** ein neuer Agenten-Sollzustand auf `main` gemerged ist
- **THEN** rollt `app-deploy.yml` API und Worker als getrennte Deployments in `operations-agent-staging` aus
- **AND** wird weder Agenten-Image noch -Chart im App-Pfad nachgebaut

### Requirement: Agenten-Host
Der abgeleitete Host `operations-agent.<baseDomain>` SHALL der Foundation-Ausgabe `operationsApiHost` entsprechen. Bei Abweichung SHALL der Deploy-Workflow vor jeder Zieländerung abbrechen.

#### Scenario: Alarmziel erreicht den Agenten
- **WHEN** der Agent ausgerollt ist
- **THEN** erreicht ein Request von `ops-intake` an `https://<operationsApiHost>/events/azure-monitor` die Agenten-API über die Ingress-Regel des App-Stacks

### Requirement: Agenten-Secret aus Foundation-Ausgaben
Der Katalogeintrag des Agenten SHALL per `foundation:`-Referenzen `postgresHost`, `postgresAdminUser`, `postgresAdminPassword`, `postgresOperationsDatabase`, `alertWebhookKey`, `maintenanceApiKey`, `githubWebhookSecret`, `modelApiKeys`, `opsGithubToken`, `diagnosticsStorageAccount`, `diagnosticsContainer` und `diagnosticsStorageKey` in das Kubernetes-Secret seines Namespace übernehmen.

#### Scenario: Agent startet mit Secret
- **WHEN** API und Worker starten
- **THEN** lesen sie Datenbank-, Webhook-, Modell-, GitHub- und Storage-Zugang aus dem Kubernetes-Secret
- **AND** steht keiner dieser Werte in Git oder in Workflow-Logs

### Requirement: Migrationen als Helm-Hook
Migrationen der Agentendatenbank SHALL als Helm-Hook-Job (`pre-install,pre-upgrade`) aus dem Agenten-Chart laufen. Der App-Stack SHALL das Chart so ausrollen, dass Helm-Hooks ausgeführt werden. Schlägt der Hook fehl, SHALL der Deploy-Lauf als fehlgeschlagen enden, ohne neue API- oder Worker-Pods zu starten.

#### Scenario: Migration schlägt fehl
- **WHEN** der Migrations-Job eines Agenten-Releases fehlschlägt
- **THEN** laufen API und Worker in der bisherigen Version weiter
- **AND** endet der Deploy-Lauf mit dem Job-Ergebnis als fehlgeschlagen

#### Scenario: Aufträge nach Release weiterverwenden
- **WHEN** ein Agenten-Release erfolgreich ausgerollt wurde
- **THEN** verarbeitet der neue Worker zuvor gespeicherte Aufträge aus der Datenbank `operations` weiter

### Requirement: AFFiNE und Agent gleichzeitig
Für Abnahmeszenario 2 SHALL AFFiNE und Betriebsagent gleichzeitig in `staging` über dasselbe App-Programm und denselben Deploy-Workflow laufen, in getrennten Stacks und Namespaces. Ein Release der einen App SHALL die andere nicht verändern.

#### Scenario: Paralleler Betrieb
- **WHEN** AFFiNE und Agent nacheinander ausgerollt wurden
- **THEN** sind beide über ihre Hosts erreichbar und ihre Smoke-Tests erfolgreich
- **AND** zeigt ein Preview des jeweils anderen App-Stacks keine Änderungen

### Requirement: Betriebswissen je App
Jeder Katalogeintrag SHALL auf ein versioniertes Runbook verweisen: AFFiNE auf das Runbook der externen Lieferung, der Agent auf die Betriebsregeln unter `platform/operations/agent/`. Wiederherstellungsverfahren ohne praktischen Test SHALL nicht als erlaubte Aktion gelten.

#### Scenario: Agent lädt App-Kontext
- **WHEN** der Agent einen Vorfall für AFFiNE untersucht
- **THEN** lädt er Runbook, erlaubte Aktionen und Smoke-Test-Referenz passend zum ausgerollten Release
- **AND** führt er keine Datenwiederherstellung als autonome Maßnahme aus

## Abgrenzung

B integriert AFFiNE und Agent, liefert Fehlerszenarien und den Smoke-Test-Vertrag. Der AFFiNE-Kollege liefert Chart, Werte, Smoke-Test-Image und Runbook aus `hackathon-platform-affine`. A liefert Laufzeit, Verfügbarkeitstests und Alarmtransport, C Auftragsverarbeitung, Migrationen und Abschlussregeln, D Agentenpaket, Diagnose und Reparatur.
