# Tasks

C kann lokal mit PostgreSQL und einer vertragskonformen Testausführung beginnen. A liefert später Datenbankserver, Foundation-Ausgaben und Action Group, B Katalog und Deploy-Workflows, D die echte Agentenausführung, externe Adapter und das Chart. C implementiert diese Lieferungen nicht selbst; siehe [Abgrenzung](proposal.md#out-of-scope-und-lieferungen-anderer-stränge). Code liegt unter `operations-agent/src/contracts|api|persistence|worker/`, Tests unter C's Unterverzeichnis in `operations-agent/tests/`.

Stand 31.10.2026: Tests laufen in `agent-build.yml` (Lauf 41124648862, main, 2026-10-30: 304 passed, 1 skipped). Der Agent (API und Worker, Chart 0.1.23, Lauf 41125866426) läuft in Staging, `/healthz` antwortet 200, `/status` zeigt Datenbank und Worker-Heartbeat gesund. Migrationen liefen über `ops-migrate.yml` und den Helm-Hook-Job im Chart. Der GitHub-Webhook ist nicht eingerichtet, er ist bewusst zurückgestellt (Mitarbeitermeldungen sollen über einen UI-Chat kommen, geplant als eigener Change). Es wurde noch kein echter Auftrag verarbeitet, und kein Azure-Monitor-Alarm hat den Agenten erreicht. Die Haken von Abschnitt 1 bis 5 stützen sich auf Tests mit Beispielzustellungen und Test-Doubles, nicht auf echte Ereignisse.

## 1. Verträge und lokale Persistenz, Anteil P1-06

- [x] 1.1 Ereignisschema v0.1 mit gültigen und ungültigen Beispielen (Azure-Monitor Fired/Resolved, Issue, Kommentar mit Markierung, Wartung) unter `platform/schemas/events/` veröffentlichen; Abschluss durch Schema-Prüfung der Beispiele prüfen.
- [x] 1.2 Aufruf-/Ergebnis-, Aktions- und Sperrverträge mit D und B unter `operations-agent/src/contracts/` festlegen; Abschluss durch Beispiele für normalen Lauf, Wiederaufnahme und Workflow-Dispatch ohne doppelte Ziel-Sperre prüfen.
- [x] 1.3 Lokales PostgreSQL und Migrations-CLI `ops migrate` mit initialem Schema liefern; Abschluss durch reproduzierbare Initialisierung und erneuten Lauf ohne Änderung und ohne Datenverlust prüfen.

## 2. Authentifizierte Ereignisannahme, P1-06

- [x] 2.1 FastAPI-Annahme mit transaktionaler Speicherung implementieren; Abschluss durch API-Neustart nach Bestätigung sowie Datenbankfehler ohne falsche Annahmebestätigung prüfen.
- [x] 2.2 `POST /events/azure-monitor?key=` mit Key-Prüfung, Common Alert Schema und Fired/Resolved liefern; Abschluss durch gültigen Key, fehlenden und falschen Key (401, kein Key im Log) sowie Resolved ohne Vorfallabschluss prüfen.
- [x] 2.3 App-Zuordnung von Alarmen über Custom Properties, ersatzweise Regelname `alert-<app-id>-<env>-<check>`, und Katalogprüfung implementieren; Abschluss durch beide Wege und einen nicht zuordenbaren Alarm prüfen.
- [x] 2.4 `POST /events/github` mit Signaturprüfung über `githubWebhookSecret`, Repository-Allowlist und Ereignisfilter liefern; Abschluss durch gültiges Issue, ungültige Signatur und fremdes Repository prüfen.
- [x] 2.5 Issue-Form mit Dropdowns für App und Umgebung unter `.github/ISSUE_TEMPLATE/` und Auslesen von `### App` und `### Umgebung` liefern; Abschluss durch über die Form angelegtes Issue und Issue ohne diese Abschnitte prüfen.
- [x] 2.6 Schleifenschutz über `<!-- mmk-ops-agent -->` implementieren; Abschluss durch markierten Kommentar ohne Auftrag und unmarkierten Kommentar desselben Accounts mit Verarbeitung prüfen.
- [x] 2.7 Deduplizierung über `source` und `deliveryId` sowie Vorfallzuordnung innerhalb einer Quelle implementieren; Abschluss durch wiederholte Zustellung, Fired/Resolved derselben Alarm-ID, Kommentar zum selben Issue und getrennte Vorfälle für Alarm und Issue derselben App prüfen.
- [x] 2.8 `POST /events/maintenance` mit `X-Api-Key` und `.github/workflows/health-check.yml` liefern, der den Key über `.github/actions/foundation-outputs` liest; Abschluss durch gesunden Check ohne Auftrag und Modellaufruf, fehlgeschlagenen Check mit Wartungsauftrag und Request ohne Key (401) prüfen.
- [x] 2.9 Eingangsverträge, Webhook-Einrichtung durch einen Repo-Admin und Fehlerdiagnose in `platform/operations/state/` dokumentieren; Abschluss durch reproduzierbare Beispielzustellungen anhand der Anleitung prüfen.

## 3. Worker und Zustandsregeln, Anteil P1-06

- [x] 3.1 Atomare Auftragsübernahme mit Erneuerung und Besitzversion implementieren; Abschluss durch lokalen Test mit zwei konkurrierenden Workern, Ablauf und abgewiesene Änderungen eines veralteten Besitzers prüfen.
- [x] 3.2 Übergangstabelle und Abschlussprüfung implementieren; Abschluss durch erlaubte Abläufe sowie abgewiesenes `resolved` bei Test vor der Maßnahme, fehlendem Kubernetes-Zustand und falschem Release prüfen.
- [x] 3.3 D's aufrufbare Ausführung an den Worker anbinden; Abschluss durch eine vertragskonforme Testausführung ohne zusätzliche Queue und danach durch D's echte SDK-Ausführung prüfen.
- [x] 3.4 Worker-Heartbeat (mindestens jede Minute) und `GET /healthz` implementieren; Abschluss durch 200 im Normalbetrieb, 503 bei gestopptem Worker nach 10 Minuten und 503 bei unerreichbarer Datenbank prüfen.
- [x] 3.5 API-/Worker-Einstiegspunkte mit Schema-Versionsprüfung beim Start an D liefern; Abschluss durch getrennten Prozessstart und Abbruch mit erwarteter und vorhandener Version bei fehlender Migration prüfen.
- [x] 3.6 Start-, Stopp-, Lease- und Wiederanlaufverhalten in `platform/operations/state/` dokumentieren; Abschluss durch kontrolliertes Beenden und Wiederübernehmen eines gespeicherten Auftrags prüfen.

## 4. Aktionen und gemeinsame Operations-Sperre

- [x] 4.1 Aktionsprotokoll mit `operationId`, `actionId`, externen Referenzen und unklarem Status implementieren; Abschluss durch Prozessabbruch zwischen externer Annahme und lokaler Bestätigung ohne verlorene Aktion prüfen.
- [x] 4.2 Exklusive App-/Umgebungs-Sperre mit Besitzerprüfung und Erneuerung implementieren; Abschluss durch Konkurrenz, unabhängige Apps und abgewiesene Freigabe durch fremden Besitzer prüfen.
- [x] 4.3 Konfliktregel implementieren; Abschluss durch Workflow, der nach 15 Minuten ohne Zieländerung abbricht, und Agentenauftrag, der sofort mit `needs_attention` und „gesperrt durch Operation <operationId>“ endet, prüfen.
- [x] 4.4 Break-glass `--force` und `ops lock backfill` implementieren; Abschluss durch Abbruch ohne `--force` bei unerreichbarer Datenbank, Deployment mit Vermerk in der Workflow-Zusammenfassung, wirkungsloses `--force` bei erreichbarer Datenbank und Nachtrag ins Aktionsprotokoll prüfen.
- [x] 4.5 Bibliothek und CLI `ops` an B und D liefern; Abschluss durch Sperrerwerb von einem GitHub-hosted Runner mit `postgresAdminPassword` aus den Foundation-Ausgaben bei gestoppter API prüfen.
  Nachweis (31.10.): `app-deploy.yml` (`ubuntu-latest`, `PGPASSWORD` aus `postgresAdminPassword`) lief am 2026-10-30 in Lauf 41070724132 und erwarb 14:19Z „Sperre affine/staging erworben: Operation gha-41070724132-1“. Zu diesem Zeitpunkt lief noch keine Agenten-API: Der erste Agenten-Deploy begann erst 18:25Z (Lauf 41106782912), der erste erfolgreiche um 19:07Z.
- [x] 4.6 Sperrverlust und ungeklärte externe Änderungen behandeln; Abschluss durch abgelaufenen Besitz ohne blinde Neuvergabe und ohne neue unberechtigte Zieländerung prüfen.
- [x] 4.7 Wiederaufnahme mit D's externem Abgleich verbinden; Abschluss durch Verfolgung desselben bekannten Workflows und sichtbaren Klärungsfall bei mehrdeutigem Ausgang prüfen.
- [x] 4.8 Sperr-CLI, Konfliktregel, Break-glass mit Nachtrag und Dispatch-/Workflow-Zuständigkeit in `platform/operations/state/` dokumentieren; Abschluss durch ausgeführtes Beispiel ohne verschachtelte konkurrierende Ziel-Sperre prüfen.

## 5. Checkpoints, Migrationen und Laufnachweise, Anteil P1-08

- [x] 5.1 D's geprüfte SDK-Persistenz in die Migrationsfolge von `ops migrate` aufnehmen; Abschluss durch persistierten und nach Prozessneustart lesbaren Checkpoint mit gepinnten Versionen prüfen.
- [x] 5.2 Migrationen gegen A's Datenbank `operations` ausführen; Abschluss durch Lauf von einem GitHub-hosted Runner und ersten Sperrclient-Aufruf ohne laufende Agenten-API prüfen.
  Nachweis (31.10.): `ops-migrate.yml`, Lauf 41070533875 (`workflow_dispatch`, `ubuntu-latest`, success, 2026-10-30 14:17Z), danach Sperrerwerb in Lauf 41070724132 (siehe 4.5) ohne laufende Agenten-API.
- [ ] 5.2a Workflow `.github/workflows/ops-migrate.yml` für die Erstinitialisierung mit Advisory-Lock liefern und den Abbruch des Sperrclients bei fehlendem Schema einbauen; Abschluss durch Erstlauf gegen die Foundation-Datenbank und gleichzeitigen zweiten Lauf ohne Fehler prüfen.
  Stand 31.10.: Erstlauf belegt (Lauf 41070533875, success). Abbruch bei fehlendem Schema belegt: Lauf 41070187111 (14:16Z) brach mit „Datenbankschema fehlt. Zuerst den Workflow .github/workflows/ops-migrate.yml ausführen“ ab, `ops-migrate.yml` lief erst danach. Offen: ein gleichzeitiger zweiter Lauf gegen die Foundation-Datenbank. Es gibt nur diesen einen `ops-migrate`-Lauf; die Serialisierung ist nur lokal getestet (`test_concurrent_migrations_serialize`, in `agent-build.yml` Lauf 41124648862).
- [ ] 5.3 Migrations-CLI als Helm-Hook-Job (`pre-install,pre-upgrade`) mit D in das Chart einbinden und ein Worker-Replica für Staging festlegen; Abschluss durch neues Agenten-Release ohne verlorene Aufträge, Aktionen oder Checkpoints prüfen.
  Stand 31.10.: Hook-Job (`operations-agent/deploy/templates/migrate-job.yaml`) und `worker.replicas: 1` (Schema erlaubt höchstens 1) liegen im Chart. Mehrere Agenten-Releases liefen erfolgreich (zuletzt Lauf 41125866426, Chart 0.1.23); die Aktionsnachweise früherer Deployments sind in `/status` weiterhin lesbar. Offen: Es gibt weder Aufträge noch Checkpoints, deren Erhalt sich prüfen ließe, und die Logs der Helm-Hook-Läufe sind nicht erhalten (Löschrichtlinie `hook-succeeded`).
- [x] 5.4 Ereignis-, Code-, Konfigurations-, Modell-, Werkzeug-, Workflow- und Testreferenzen je Auftrag verknüpfen; Abschluss durch vollständigen Laufnachweis prüfen.
- [x] 5.5 `GET /runs/{auftragsId}` mit `X-Api-Key` oder `ops runs show <id>` liefern; Abschluss durch JSON mit allen verknüpften Nachweisen, Prüfung auf fehlende Secret-Werte und 401 ohne Key prüfen.

## 6. Gemeinsame Integration, P1-09 und Anteil P1-12

- [ ] 6.1 Abnahmeszenario 3 mit echtem Support-Issue über die Issue-Form und D's markierter Antwort sowie echten Alarm aus `ops-intake` nachweisen; Abschluss durch richtige Issue-/App-Zuordnung, keine Schleife und abrufbare Laufnachweise prüfen.
  Stand 31.10.: Der GitHub-Webhook ist nicht eingerichtet, bewusst zurückgestellt zugunsten eines UI-Chats für Mitarbeitermeldungen (geplanter, eigener Change). Die Anforderung bleibt bestehen, bis der Meldekanal entschieden ist. Ein echter Alarm aus `ops-intake` hat den Agenten ebenfalls noch nicht erreicht (`/status`: `lastMonitorSignalAt` null, Überwachung „not-verified“).
- [ ] 6.2 Abnahmeszenarien 6 und 7 federführend durchführen; Abschluss durch eine Aktion bei doppelter Zustellung und denselben externen Workflow nach Worker-Neustart prüfen.
  Stand 31.10.: Offen. Es wurde noch kein echter Auftrag verarbeitet (`/status`: keine Aufträge); doppelte Zustellung und Worker-Neustart sind nur in Tests belegt.
- [ ] 6.3 Abnahmeszenarien 8 und 10 federführend durchführen; Abschluss durch genau einen aktiven ändernden Besitzer bei Pipeline-/Agentenkonkurrenz, angewandte Konfliktregel und vollständige, über `GET /runs/{auftragsId}` oder `ops runs show <id>` abgerufene Laufnachweise prüfen.
  Stand 31.10.: Offen. Kein echter Auftrag und keine Agentenmaßnahme in Staging; Pipeline-Sperren laufen in Deployments (z. B. Lauf 41125866426), eine Konkurrenz mit dem Agenten wurde nicht erzeugt.
- [ ] 6.4 Ausfallmeldung nach E10 mit A nachweisen; Abschluss durch gestoppten Worker, 503 auf `/healthz` und E-Mail über `ops-humans` prüfen.
  Stand 31.10.: Nicht nachgewiesen. `/healthz` antwortet im Normalbetrieb mit 200; die externe Überwachung ist in `/status` als „not-verified“ gekennzeichnet, ein Ausfall wurde noch nicht herbeigeführt.
