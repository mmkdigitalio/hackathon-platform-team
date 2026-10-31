# Tasks

## 1. Standard-Chart und Chart-Quelle

- [ ] 1.1 Chart `platform/charts/web-app` mit Werte-Schema; Abschluss durch `helm lint` und Template-Tests mit und ohne Volume.
- [ ] 1.2 Katalogschema `chart.platform`, Validierung der Chart-Version, App-Programm löst den lokalen Pfad auf; Abschluss durch Schema- und Programmtests.
- [ ] 1.3 Standard-Runbook `platform/operations/apps/standard-runbook.md`.

## 2. Gerüst und Workflows

- [ ] 2.1 `new-app` in der Katalog-CLI mit Tests (gültig, ungültige App-ID, vorhandene Dateien, Datenbausteine).
- [ ] 2.2 `app-onboard.yml` (Formular, optionaler Build in die ACR, Gerüst, Validierung, PR); Abschluss durch `actionlint` und einen Probelauf.
- [ ] 2.3 `app-release.yml`; Abschluss durch `actionlint` und einen Probelauf.

## 3. Anleitung und Nachweis

- [ ] 3.1 `platform/operations/apps/ONBOARDING.md` für Einsteiger.
- [ ] 3.2 Eine Beispiel-App (z. B. ein öffentliches Demo-Image) über das Formular aufnehmen und in Staging prüfen.
