# Tasks

## 1. Standard-Chart und Chart-Quelle

- [x] 1.1 Chart `platform/charts/web-app` mit Werte-Schema; Abschluss durch `helm lint` und Template-Tests mit und ohne Volume. Nachweis: `helm lint`, Template-Tests mit und ohne Volume.
- [x] 1.2 Katalogschema `chart.platform`, Validierung der Chart-Version, App-Programm löst den lokalen Pfad auf; Abschluss durch Schema- und Programmtests. Nachweis: Katalog- (86) und Runtime-Tests (17) grün am 31.10.
- [x] 1.3 Standard-Runbook `platform/operations/apps/standard-runbook.md`.

## 2. Gerüst und Workflows

- [x] 2.1 `new-app` in der Katalog-CLI mit Tests (gültig, ungültige App-ID, vorhandene Dateien, Datenbausteine). Nachweis: `test/scaffold.test.ts` (36 Tests).
- [ ] 2.2 `app-onboard.yml` (Formular, optionaler Build in die ACR, Gerüst, Validierung, PR); Abschluss durch `actionlint` und einen Probelauf. Stand 31.10.: `actionlint` grün, auf `main`; Probelauf steht aus.
- [ ] 2.3 `app-release.yml`; Abschluss durch `actionlint` und einen Probelauf. Stand 31.10.: `actionlint` grün, auf `main`; Probelauf steht aus.

## 3. Anleitung und Nachweis

- [x] 3.1 `platform/operations/apps/ONBOARDING.md` für Einsteiger.
- [ ] 3.2 Eine Beispiel-App (z. B. ein öffentliches Demo-Image) über das Formular aufnehmen und in Staging prüfen. Stand 31.10.: offen.
