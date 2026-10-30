# Spec Delta

## Purpose

Ein versionierter App-Katalog beschreibt jede App umgebungsunabhängig und ihren Sollzustand je Umgebung, sodass App-Stack, Deploy-Workflow, Foundation-Stack und Agent dieselben Referenzen verwenden.

## ADDED Requirements

### Requirement: Umgebungsunabhängiger Katalogeintrag
Jede App SHALL genau einen Eintrag `platform/catalog/<app-id>/app.yaml` besitzen. Er SHALL `schemaVersion`, `appId`, Herkunft und Repository, Chart-Quelle, Ingress-Ziel (Service und Port), Datenbausteine, Secret-Referenzen, Verfügbarkeits-Prüfpfad, Smoke-Test-Image, Runbook, erlaubte Aktionen und Datenverfahren für Migration, Sicherung und Wiederherstellung enthalten. Nicht benötigte Datenverfahren SHALL als nicht anwendbar gekennzeichnet sein.

#### Scenario: AFFiNE-Eintrag validieren
- **WHEN** `platform/catalog/affine/app.yaml` validiert wird
- **THEN** stehen App-Stack, Foundation-Stack und Agent dieselben App-, Herkunfts- und Betriebsreferenzen zur Verfügung
- **AND** enthält der Eintrag keine umgebungsspezifischen Werte

### Requirement: Sollzustand je Umgebung
Der Sollzustand einer App SHALL in `platform/environments/<env>/<app-id>.yaml` liegen und Image-Digest, Chart-Version, Konfigurationswerte für das Chart und Größen (Replicas, Ressourcen, Volumengrößen) enthalten. Git-SHA des Commits, Image-Digest und Chart-Version SHALL zusammen die Release-Referenz bilden. Eine Sollzustandsdatei ohne zugehörigen Katalogeintrag SHALL abgelehnt werden.

#### Scenario: Neue AFFiNE-Version
- **WHEN** ein Pull Request Image-Digest und Chart-Version in `platform/environments/staging/affine.yaml` ändert
- **THEN** bleibt `platform/catalog/affine/app.yaml` unverändert
- **AND** ist das Release nach dem Merge über Commit-SHA, Digest und Chart-Version eindeutig referenzierbar

#### Scenario: Sollzustand ohne Katalogeintrag
- **WHEN** `platform/environments/staging/foo.yaml` existiert, `platform/catalog/foo/app.yaml` aber fehlt
- **THEN** scheitert die Validierung mit dem Dateipfad

### Requirement: Namespace und Host nach Konvention
Namespace `<app-id>-<env>` und Host `<app-id>.<baseDomain>` SHALL aus App-ID, Umgebung und der Foundation-Ausgabe `baseDomain` abgeleitet werden. Katalogeintrag und Sollzustand SHALL keine Felder für Namespace, Host oder Domain enthalten.

#### Scenario: Namen für AFFiNE
- **WHEN** AFFiNE in `staging` ausgerollt wird
- **THEN** heißen Namespace und Host `affine-staging` und `affine.<baseDomain>`
- **AND** stehen beide Werte in keiner Katalog- oder Sollzustandsdatei

#### Scenario: Eingetragener Host
- **WHEN** eine Sollzustandsdatei ein Feld `host` enthält
- **THEN** lehnt die Validierung es als unbekanntes Feld ab

### Requirement: Versioniertes Katalogschema
Das Schema für Katalogeintrag und Sollzustand SHALL als Version v0.1 unter `platform/schemas/app-catalog/` liegen, mit gültigen und ungültigen Beispielen. Die Validierung SHALL unbekannte Schema-Versionen, unbekannte Felder, App-IDs entgegen `platform/schemas/conventions/` und Image-Referenzen ohne Digest vor jeder Änderung ablehnen.

#### Scenario: Bewegliche Image-Referenz
- **WHEN** ein Sollzustand nur ein Image-Tag ohne Digest angibt
- **THEN** wird er vor dem Deployment als unvollständig zurückgewiesen

#### Scenario: Unbekannte Schema-Version
- **WHEN** ein Katalogeintrag `schemaVersion` `0.2` angibt
- **THEN** scheitert die Validierung mit der unterstützten Version im Fehlertext

### Requirement: Unterstützte Datenbausteine v0.1
Der Katalog SHALL genau die Datenbausteine `postgres-database`, `redis` und `persistent-volume` zulassen, die der App-Stack umsetzt. Ein unbekannter Baustein SHALL mit dem betroffenen Feld abgelehnt werden. Ein neuer Baustein SHALL erst zusammen mit seiner Umsetzung im App-Stack und einer neuen Schema-Version zugelassen werden.

#### Scenario: Nicht unterstützter Baustein
- **WHEN** ein Eintrag den Baustein `mongodb` anfordert
- **THEN** scheitert die Validierung mit dem betroffenen Feld
- **AND** wird kein Deployment begonnen

#### Scenario: Bausteine von AFFiNE
- **WHEN** der AFFiNE-Eintrag `postgres-database`, `redis` und `persistent-volume` anfordert
- **THEN** besteht er die Validierung

### Requirement: Secret-Referenzen statt Werten
Katalogeinträge SHALL Secrets nur als `foundation:<ausgabeName>` oder `app:<name>` referenzieren. Eine `foundation:`-Referenz SHALL eine Ausgabe des Foundation-Ausgabeschemas benennen. Eine `app:`-Referenz SHALL ein Secret benennen, das ein deklarierter Datenbaustein liefert oder der App-Stack laut Eintrag generiert. Andere Formen und Klartextwerte SHALL abgelehnt werden.

#### Scenario: Unbekannte Foundation-Ausgabe
- **WHEN** ein Eintrag `foundation:postgresPassword` referenziert
- **THEN** scheitert die Validierung, weil das Ausgabeschema nur `postgresAdminPassword` kennt

#### Scenario: Nicht deklariertes App-Secret
- **WHEN** ein Eintrag `app:redis-password` referenziert, aber keinen Baustein `redis` deklariert
- **THEN** scheitert die Validierung mit der betroffenen Referenz

### Requirement: Keine Secret-Werte in Ausgaben
Validierung, Plan-Prüfung und Deploy-Workflow SHALL aufgelöste Secret-Werte weder in Logs noch in Zusammenfassungen, Pull Requests oder Artefakten ausgeben.

#### Scenario: Plan mit Datenbankzugang
- **WHEN** die Plan-Prüfung für AFFiNE ein Preview mit Datenbank-Login anzeigt
- **THEN** erscheinen Referenzen und maskierte Werte, aber kein Passwort im Klartext

### Requirement: Verfügbarkeits-Prüfpfad für den Foundation-Stack
Jeder Katalogeintrag SHALL einen Verfügbarkeits-Prüfpfad enthalten, der ohne Anmeldung mit HTTP 200 antwortet, solange die App nutzbar ist. Der Foundation-Stack erzeugt daraus Verfügbarkeitstest und Alarmregel `alert-<app-id>-<env>-availability`. Katalog und App-Stack SHALL keine Azure-Ressourcen beschreiben oder anlegen.

#### Scenario: Monitoring für eine neue App
- **WHEN** ein Katalogeintrag mit Prüfpfad auf `main` liegt und der Foundation-Stack danach ausgerollt wird
- **THEN** prüft ein Verfügbarkeitstest `https://<app-id>.<baseDomain><Prüfpfad>`
- **AND** enthält der Plan des App-Stacks keine Azure-Ressource

### Requirement: Smoke-Test-Vertrag
Ein Smoke-Test SHALL ein per Digest referenziertes Container-Image sein, das als Kubernetes-Job mit der Umgebungsvariable `TARGET_URL` läuft. Er SHALL mit Exit-Code 0 bei Erfolg und ungleich 0 bei Fehlschlag enden und genau ein JSON-Ergebnis als letzte Zeile seiner Ausgabe auf stdout schreiben; Diagnosezeilen davor sind erlaubt. Nicht geheime Testparameter SHALL über `smokeTest.env`, benötigte Test-Secrets über Secret-Referenzen in `smokeTest.secrets` bereitgestellt werden.

#### Scenario: Erreichbar, aber fachlich defekt
- **WHEN** eine App HTTP 200 liefert, ihre zentrale Nutzeraktion aber fehlschlägt
- **THEN** endet der Smoke-Test mit Exit-Code ungleich 0 und `success` `false`
- **AND** gilt die Erreichbarkeit allein nicht als Verifikationsnachweis

#### Scenario: Widersprüchliches Ergebnis
- **WHEN** ein Smoke-Test mit Exit-Code 0 endet, aber kein gültiges JSON oder `success` `false` schreibt
- **THEN** wertet der Ausführende den Test als Fehlschlag

### Requirement: Verifikationsergebnis
Ein Verifikationsergebnis SHALL `appId`, `environment`, `release` (Git-SHA, Image-Digest, Chart-Version), `checkRef`, `startedAt`, `finishedAt`, `success` und `evidence` enthalten. Das Smoke-Test-Image SHALL mindestens `success`, `startedAt`, `finishedAt` und `evidence` schreiben. `appId`, `environment`, den tatsächlich laufenden `release` und `checkRef` als Digest des Smoke-Test-Images SHALL der Ausführende ergänzen.

#### Scenario: Ergebnis nach Rollout
- **WHEN** der Deploy-Workflow den Smoke-Test nach einem Rollout ausführt
- **THEN** enthält das Ergebnis den im Cluster tatsächlich laufenden Image-Digest
- **AND** wird eine Abweichung vom Sollzustand im Ergebnis ausgewiesen

### Requirement: Soll- und Iststand unterscheidbar
Deploy-Workflow und Agent SHALL den Sollzustand aus Git und den tatsächlich laufenden Stand aus dem Cluster getrennt ermitteln können. Der App-Stack SHALL die Release-Referenz des letzten erfolgreichen Rollouts als Annotationen `ops.mmk/release-git-sha`, `ops.mmk/release-image-digest` und `ops.mmk/release-chart-version` am Namespace hinterlegen; den laufenden Image-Digest liefern die Pods.

#### Scenario: Fehlgeschlagenes Deployment untersuchen
- **WHEN** ein gewünschtes Release nicht erfolgreich ausgerollt wurde
- **THEN** kann der Agent Sollstand und zuletzt ausgerollten Stand getrennt ermitteln
- **AND** enthalten seine Befunde beide Versionsreferenzen

### Requirement: Erlaubte Aktionen und Runbook
Der Katalogeintrag SHALL die für den Agenten erlaubten Aktionen aus der im Schema festgelegten Liste benennen, in v0.1 `restart` und `redeploy`. Er SHALL ein Runbook mit Repository, Pfad und Commit-SHA referenzieren. Aktionen außerhalb der Liste und Runbook-Referenzen ohne Commit-SHA SHALL abgelehnt werden.

#### Scenario: Agent lädt Betriebswissen
- **WHEN** der Agent einen Vorfall für AFFiNE untersucht
- **THEN** lädt er das Runbook in der im Katalogeintrag referenzierten Version
- **AND** schlägt er nur Aktionen aus `restart` und `redeploy` als autonome Maßnahme vor

## Abgrenzung

B besitzt Schema, Validierung und den Katalogeintrag des Agenten. `platform/catalog/affine/` befüllt der AFFiNE-Kollege nach diesem Schema. A erzeugt aus `app.yaml` Verfügbarkeitstests und Alarme, C validiert App-/Umgebungszuordnungen, D lädt Release, erlaubte Aktionen und Betriebswissen und führt Smoke-Tests aus.
