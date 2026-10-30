# Tasks

A startet sofort. B, C und D brauchen früh die Konventionen und das Ausgabe-Schema v0; die Cloud-Ressourcen folgen. Die AFFiNE-Paketierung ist externe Lieferung; siehe [Abgrenzung](proposal.md#out-of-scope-und-lieferungen-anderer-stränge).

## 1. Monorepo und Konventionen, P1-01

- [x] 1.1 Monorepo-Gerüst mit `platform/`, `operations-agent/`, `.github/` und `CODEOWNERS` gemäß Dateizuständigkeit anlegen; Abschluss durch Zuordnung jedes Verzeichnisses zu einem Strang prüfen.
- [x] 1.2 Konventionen für App-ID, Umgebungen, abgeleitete Namen, Tags und Release-Referenz unter `platform/schemas/conventions/` mit Prüfskript veröffentlichen; Abschluss durch abgelehnte App-ID `Demo_App` und korrekt abgeleitete Namen für `affine` prüfen.
- [ ] 1.3 Ausgabe-Schema v0 unter `platform/schemas/foundation-outputs/` mit Platzhalterwerten veröffentlichen; Abschluss durch Bestätigung von B, C und D, dass sie dagegen entwickeln können.

## 2. Bootstrap und Zugänge, P1-03

- [ ] 2.1 Vorab-Check für Contributor-Rechte, Repo-Admin und PAT-Berechtigungen liefern; Abschluss durch Abbruch mit klarer Meldung bei fehlendem Recht prüfen.
- [ ] 2.2 Idempotentes Bootstrap-Skript für State-Resource-Group, Storage-Account mit Versionierung und Soft Delete sowie State-Container liefern; Abschluss durch zweimaligen Lauf ohne Änderung und Wiederholung nach Abbruch prüfen.
- [ ] 2.3 Repo-Secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE`, `OPS_GITHUB_TOKEN` setzen und Foundation-Konfigurationssecrets (Modell-Keys, `githubWebhookSecret`, PAT) hinterlegen; Abschluss durch Secret-Liste ohne Klartext im Repo prüfen.
- [ ] 2.4 Composite Action `.github/actions/foundation-outputs` mit Versionsprüfung und Maskierung liefern; Abschluss durch Lesen der Platzhalterausgaben und Abbruch bei falscher Vertragsversion prüfen.

## 3. Grundplattform, P1-04

- [ ] 3.1 Resource Group, Log Analytics, Application Insights, ACR mit Admin-User und AKS mit lokalen Accounts ausrollen; Abschluss durch lokalen Lauf ohne Fehler wegen fehlender Rollenzuweisung prüfen.
- [ ] 3.2 Container Insights aktivieren, soweit ohne Rollenzuweisung möglich; Abschluss durch Log-Abfrage einer Testzeile oder dokumentierten Verzicht prüfen.
- [ ] 3.3 Ingress-Controller und Zertifikatsverwaltung ausrollen, `baseDomain` aus der Service-IP ableiten; Abschluss durch HTTPS-Aufruf einer Test-Ingress-Regel unter `<test>.<baseDomain>` prüfen.
- [ ] 3.4 PostgreSQL Flexible Server mit Datenbank `operations`, TLS-Pflicht, offener Firewall und 7 Tagen Backup ausrollen; Abschluss durch Verbindung von einem GitHub-hosted Runner prüfen.
- [ ] 3.5 Diagnose-Storage mit Container `diagnostics` und 30-Tage-Lifecycle ausrollen; Abschluss durch Schreiben und Lesen mit dem Storage-Key prüfen.
- [ ] 3.6 Generierte Keys (`alertWebhookKey`, `maintenanceApiKey`) und den vollständigen Ausgabevertrag mit `contractVersion` ausgeben; Abschluss durch Schema-Validierung der echten Ausgaben prüfen.

## 4. Monitoring und Alarmziele

- [ ] 4.1 Action Groups `ops-intake` und `ops-humans` anlegen; Abschluss durch Testbenachrichtigung an den Team-Verteiler prüfen.
- [ ] 4.2 Verfügbarkeitstests und Alarmregeln aus `platform/catalog/*/app.yaml` erzeugen, mit Custom Properties und automatischer Auflösung; Abschluss durch einen Test-Katalogeintrag mit Fired- und Resolved-Ereignis prüfen.
- [ ] 4.3 Verfügbarkeitstest auf `https://<operationsApiHost>/healthz` mit Alarm an `ops-humans` anlegen; Abschluss durch E-Mail bei gestopptem Worker prüfen, sobald C und B den Agenten ausgerollt haben.
- [ ] 4.4 Budget-Alarm für die Plattform-Resource-Group anlegen; Abschluss durch sichtbares Budget mit Empfänger prüfen.

## 5. Nachweis und Betrieb, Anteil P1-12

- [ ] 5.1 Zugangscheck-Workflow `foundation-access-check.yml` liefern; Abschluss durch grünen Lauf mit einzeln gemeldeten Kategorien und rotem Lauf bei absichtlich falschem Key prüfen.
- [ ] 5.2 Preview-Prüfung für gegenseitige Änderungen zwischen Foundation- und App-Stack durchführen; Abschluss durch leere Previews nach je einem Release prüfen.
- [ ] 5.3 Betriebsdokumentation unter `platform/operations/foundation/` für Bootstrap, Upgrade, Stoppen/Starten, Wiederherstellung (Cluster, State, Datenbank) und akzeptierte Staging-Risiken schreiben; Abschluss durch Wiederherstellung eines gelöschten Clusters durch eine zweite Person anhand der Anleitung prüfen.
- [ ] 5.4 Abnahmeszenario 1 federführend nachweisen; Abschluss durch Bereitstellung aus Bootstrap und Git-Zustand ohne manuelle Ressourcenreparatur mit URLs, Stack-Namen und Versionen in der Ausgabe prüfen.
