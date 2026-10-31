# Spec Delta

## Purpose

Neue Apps lassen sich ohne Helm- und Plattformwissen über ein Formular aufnehmen und danach mit einem Klick neu veröffentlichen.

## ADDED Requirements

### Requirement: Standard-Chart für Web-Apps
Die Plattform SHALL ein Chart `web-app` unter `platform/charts/web-app/` bereitstellen, das den Werte-Vertrag des App-Programms erfüllt, ein Deployment `<app-id>` mit Proben auf `probes.path`, einen Service `<app-id>` auf Port 80 zum `container.port` und optional ein Volume am `persistence.mountPath` anlegt. Mit Volume SHALL die Strategie `Recreate` gelten. Das Chart SHALL keine Secrets, Ingress-Regeln, PVCs oder Datenbanken anlegen.

#### Scenario: App mit Datenbank und Volume
- **WHEN** eine App `chart.platform: web-app` mit den Bausteinen `postgres-database` und `persistent-volume` verwendet
- **THEN** bekommt der Container `DATABASE_URL` aus dem Plattform-Secret und das PVC am konfigurierten Pfad
- **AND** läuft das Deployment mit Strategie `Recreate`

### Requirement: Chart-Quelle aus dem Repository
Der Katalog SHALL `chart.platform: <name>` als vierte Chart-Quelle erlauben; das App-Programm SHALL dann das Chart aus `platform/charts/<name>` verwenden, und die Validierung SHALL einen Sollzustand ablehnen, dessen `chart.version` nicht der Version in `Chart.yaml` entspricht.

#### Scenario: Falsche Chart-Version
- **WHEN** der Sollzustand `chart.version: 0.2.0` nennt und `platform/charts/web-app/Chart.yaml` die Version `0.1.0` hat
- **THEN** schlägt `app-plan.yml` mit einer verständlichen Meldung fehl

### Requirement: Gerüst für neue Apps
Die Katalog-CLI SHALL mit `new-app` aus App-ID, Beschreibung, Image (mit Digest) oder ACR-Repository, Port, Prüfpfad, Datenbausteinen und optionalen Umgebungsvariablen einen gültigen Katalogeintrag und Sollzustand für `staging` erzeugen: Standard-Chart, Standard-Runbook mit Commit-SHA, `smoke-http` auf den Prüfpfad, `allowedActions: [restart, redeploy]` und Standard-`dataProcedures`. Bestehende Dateien SHALL nicht überschrieben werden.

#### Scenario: Ungültige App-ID
- **WHEN** jemand `new-app` mit der App-ID `Meine_App` aufruft
- **THEN** bricht die CLI mit der Konventionsregel und einem Beispiel ab und schreibt nichts

### Requirement: Aufnahme per Formular
Der Workflow `app-onboard.yml` SHALL per `workflow_dispatch` mit Formularfeldern eine App aufnehmen: optional das Image aus einem GitHub-Repository mit Dockerfile bauen und in die ACR legen, `new-app` ausführen, validieren und einen Pull Request mit den beiden Dateien und einer Checkliste öffnen. Er SHALL nichts direkt auf `main` schreiben und nichts ausrollen.

#### Scenario: Eigene App aus einem Repository
- **WHEN** jemand das Formular mit `appId: zeiterfassung`, Repository `mmkdigitalio/zeiterfassung`, Port 8080 und Prüfpfad `/health` absendet
- **THEN** liegt das Image als `acr:zeiterfassung@sha256:…` in der ACR
- **AND** ist ein PR mit Katalogeintrag und Sollzustand offen, dessen `app-plan` grün ist

### Requirement: Release per Formular
Der Workflow `app-release.yml` SHALL für eine Katalog-App mit Quell-Repository eine neue Version bauen, in die ACR legen und den Sollzustand über `app-update-desired-state.yml` als PR vorschlagen.

#### Scenario: Neue Version
- **WHEN** jemand `app-release.yml` für `zeiterfassung` mit dem Ref `v1.2.0` startet
- **THEN** entsteht ein PR, der nur `image` in `platform/environments/staging/zeiterfassung.yaml` ändert

### Requirement: Anleitung ohne Vorkenntnisse
`platform/operations/apps/ONBOARDING.md` SHALL in einfacher Sprache beschreiben, wie eine App vom Dockerfile bis zur laufenden, überwachten und über den Chat betreuten App kommt, mit Voraussetzungen, Formularfeldern, Prüfschritten, typischen Fehlern und dem Weg zu einem fachlichen Smoke-Test.

#### Scenario: Erste App
- **WHEN** eine Person ohne Plattformkenntnisse der Anleitung folgt
- **THEN** läuft ihre App unter `https://<app-id>.<baseDomain>`, erscheint in der Plattform-UI und ist über den Chat meldbar
