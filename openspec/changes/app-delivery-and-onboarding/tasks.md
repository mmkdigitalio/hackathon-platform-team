# Tasks

B startet lokal mit Schema, App-Programm und Workflows gegen lokalen Cluster und lokales PostgreSQL. Echte App-Deployments benötigen A's Foundation, C's Migrationen und standalone Operations-Client, D's Agentenpaket und die AFFiNE-Lieferung aus `hackathon-platform-affine`. Diese Abhängigkeiten sind Lieferpunkte, keine Voraussetzung für den lokalen Start; siehe [Abgrenzung](proposal.md#out-of-scope-und-lieferungen-anderer-stränge).

## 1. App-Katalog v0.1, Anteil P1-05

- [x] 1.1 Schema für `app.yaml` und Sollzustand unter `platform/schemas/app-catalog/` mit gültigen Beispielen (AFFiNE-artig, `operations-agent`) und ungültigen Beispielen liefern; Abschluss durch Ablehnung unbekannter Versionen, Felder, Bausteine, Namespace-/Host-Felder, Tags ohne Digest und App-IDs entgegen den Konventionen prüfen.
- [x] 1.2 Secret-Referenzen `foundation:` und `app:` validieren; Abschluss durch Ablehnung unbekannter Foundation-Ausgaben laut `platform/schemas/foundation-outputs/` und nicht deklarierter App-Secrets prüfen.
- [ ] 1.3 Smoke-Test-Vertrag und JSON-Schema des Verifikationsergebnisses veröffentlichen; Abschluss durch gemeinsame Vertragsprüfung mit C und D anhand eines fehlgeschlagenen Ergebnisses prüfen.
- [x] 1.4 Lieferanforderungen an den AFFiNE-Kollegen übergeben (Katalogeintrag nach Schema, Chart mit fester Version und Strategie `Recreate`, Smoke-Test-Image, Runbook mit Commit-SHA, Datenverfahren); Abschluss durch bestätigte Übergabe und validierten Entwurf von `platform/catalog/affine/app.yaml` prüfen.

## 2. App-Programm, Anteil P1-05

- [x] 2.1 Pulumi-Programm unter `platform/infra/app-runtime/` mit `StackReference`, Namespace, Image-Pull-Secret, Secret `<app-id>-platform`, Ingress und Namespace-Annotationen liefern; Abschluss durch Preview ohne Azure-Provider gegen Platzhalter-Ausgaben prüfen.
- [x] 2.2 Datenbausteine `postgres-database`, `redis` und `persistent-volume` mit Schutz vor Löschung umsetzen; Abschluss durch lokale Bereitstellung, stabile Secrets bei erneutem Lauf und scheiternden Plan bei entferntem Baustein prüfen.
- [x] 2.3 Helm-Ausrollung mit Hooks und Drift-Korrektur umsetzen; Abschluss durch ausgeführten Pre-Install-Hook und zurückgesetzte Replica-Zahl nach manuellem Skalieren ohne Sollzustandsänderung prüfen.
- [x] 2.4 Generisches Smoke-Test-Image `smoke-http` mit Build-Workflow liefern; Abschluss durch Vertragsausgabe bei erreichbarem und nicht erreichbarem Ziel prüfen.

## 3. Workflows, Anteil P1-05

- [ ] 3.1 `app-plan.yml` mit Validierung und Preview der betroffenen Stacks liefern; Abschluss durch roten Check bei ungültigem Sollzustand und grünen Check ohne Secret-Werte in der Ausgabe prüfen.
- [ ] 3.2 `app-deploy.yml` mit Push- und `workflow_dispatch`-Auslöser, `run-name` mit `operationId` und `actionId`, Zugängen über Repo-Secrets und `foundation-outputs` liefern; Abschluss durch Lauf ohne Azure-Login und Ausrollen nur der geänderten Stacks prüfen.
- [x] 3.3 C's Operations-Client mit Erwerb, Erneuerung, Besitzprüfung, Freigabe und Aktionsnachweis integrieren; Abschluss durch Sperrerwerb bei gestoppter Agenten-API und Abbruch nach 15 Minuten bei belegter Sperre prüfen.
- [x] 3.4 Break-glass mit `force` umsetzen; Abschluss durch Lauf ohne Sperre bei nicht erreichbarem PostgreSQL mit Vermerk in der Zusammenfassung und abgelehntes `force` bei erreichbarem PostgreSQL prüfen.
- [ ] 3.5 Smoke-Job, Rollout-Prüfung und Deployment-Ergebnis verbinden; Abschluss durch echten Erfolg sowie fehlschlagenden Smoke-Test ohne Erfolgsmeldung prüfen.
- [ ] 3.6 `app-update-desired-state.yml` mit Token der GitHub App (Rückfall `OPS_GITHUB_TOKEN`) und Auto-Merge liefern (geändert 2026-10-30 (Cornelius); war: mit `OPS_GITHUB_TOKEN`); Abschluss durch Aufruf mit neuem Digest, gemergten PR nach grünem Plan und automatisch gestarteten Deploy-Lauf prüfen.
- [ ] 3.7 Betriebsverfahren unter `platform/operations/apps/` dokumentieren (Release, Rollback mit Kompatibilitätsprüfung, Stopp und Wiederanlauf, Break-glass-Nachtrag, Repository-Einstellungen); Abschluss durch Durchspielen anhand der Anleitung prüfen.

## 4. Agentenpaket integrieren, Anteil P1-08

- [x] 4.1 `platform/catalog/operations-agent/app.yaml` mit `foundation:`-Secrets, `smoke-http` und Prüfpfad `/healthz` sowie Sollzustand für `staging` liefern; Abschluss durch erfolgreiche Validierung prüfen.
- [x] 4.2 API und Worker über den App-Pfad ausrollen; Abschluss durch getrennte Deployments, erfolgreichen Migrations-Hook, Host gleich `operationsApiHost` und Weiterverarbeitung gespeicherter Aufträge nach einem zweiten Release prüfen.

## 5. AFFiNE aufnehmen, P1-11

- [ ] 5.1 AFFiNE-Lieferung validieren und `platform/environments/staging/affine.yaml` mit festem Upstream-Release anlegen; Abschluss durch grünen Plan prüfen.
- [ ] 5.2 AFFiNE über `app-deploy.yml` ausrollen; Abschluss durch Erreichbarkeit unter `affine.<baseDomain>` und erfolgreichen Smoke-Test prüfen.
- [x] 5.3 Persistenz nachweisen; Abschluss durch zweiten erfolgreichen Smoke-Test nach Neustart aller AFFiNE- und Redis-Pods prüfen.

## 6. Fehlerszenarien, Anteil P1-10

- [ ] 6.1 Reparierbaren Fall unter `platform/operations/apps/affine/` mit Skript, erwarteter Wirkung und Rücksetzen liefern; Abschluss durch Alarm `alert-affine-staging-availability` nach Skalieren auf 0 und Wiederherstellung per `app-deploy.yml` prüfen.
- [x] 6.2 Nicht reparierbaren Fall mit dokumentiertem Commit und Revert liefern; Abschluss durch weiterhin negativen Smoke-Test nach Neustart und erneutem Deployment sowie erfolgreichen Revert prüfen.
- [x] 6.3 Datensicherheit beider Fälle nachweisen; Abschluss durch Abbruch bei anderer Umgebung als `staging` und wiedergefundene Inhalte nach beiden Szenarien prüfen.

## 7. Gemeinsame Integration, Anteil P1-12

- [ ] 7.1 Abnahmeszenarien 2 und 9 federführend nachweisen; Abschluss durch releasebezogene Ergebnisse für parallelen Betrieb von AFFiNE und Agent sowie AFFiNE-Persistenz prüfen.
- [ ] 7.2 Fehlerszenarien, Deploy-Workflow und Korrelation für die Szenarien 4, 5, 7 und 8 bereitstellen; Abschluss durch die gemeinsamen Durchläufe mit tatsächlichen Ziel-, Workflow- und Testnachweisen prüfen.
