# Spec Delta

## Purpose

Monitoring, Mitarbeitermeldungen und Wartungsaufträge gelangen über authentifizierte Eingänge in eine gemeinsame persistente Auftragsverarbeitung.

## ADDED Requirements

### Requirement: Dauerhaft annehmen vor Bestätigung
Der Ereigniseingang SHALL gültige Ereignisse und ihre Auftragsreferenz vor einer erfolgreichen Annahmebestätigung in PostgreSQL persistieren. Die eigentliche Agentenuntersuchung SHALL außerhalb des HTTP-Requests laufen. Ohne erfolgreiche Speicherung SHALL keine Annahme bestätigt werden.

#### Scenario: API nach Annahme neu starten
- **WHEN** ein gültiges Ereignis bestätigt wurde und die API vor der Verarbeitung neu startet
- **THEN** ist das Ereignis anschließend als gespeicherter Auftrag auffindbar
- **AND** muss die Quelle es nicht erneut senden, damit die Verarbeitung beginnt

#### Scenario: Datenbank nicht verfügbar
- **WHEN** ein Ereignis nicht dauerhaft gespeichert werden kann
- **THEN** erhält die Quelle eine fehlgeschlagene Annahme
- **AND** wird keine erfolgreiche Auftragsreferenz vorgetäuscht

### Requirement: Einheitliches Ereignisformat
Normalisierte Ereignisse SHALL `schemaVersion`, `source`, `deliveryId`, `externalRef`, `appId`, `environment`, `eventType`, `severity`, `state`, `occurredAt`, `receivedAt` und eine Referenz auf die Quelldaten enthalten. Das Schema und gültige wie ungültige Beispiele SHALL unter `platform/schemas/events/` liegen.

#### Scenario: Beispielereignisse prüfen
- **WHEN** die Beispiele unter `platform/schemas/events/` gegen das Schema geprüft werden
- **THEN** bestehen die gültigen Beispiele
- **AND** schlagen die ungültigen mit benanntem Feld fehl

### Requirement: App-Zuordnung gegen den Katalog prüfen
Der Ereigniseingang SHALL `appId` und `environment` gegen `platform/catalog/` und `platform/environments/` prüfen. Fehlende oder unbekannte Zuordnungen SHALL ohne Zieländerung gespeichert und sichtbar als Zuordnungsfehler markiert werden.

#### Scenario: Unbekannte App
- **WHEN** ein authentifiziertes Ereignis keine gültige Katalogzuordnung besitzt
- **THEN** wird kein ändernder Auftrag für eine geratene App ausgeführt
- **AND** bleibt der Zuordnungsfehler mit den Quelldaten nachvollziehbar

### Requirement: Azure-Monitor-Eingang mit Key
Der Ereigniseingang SHALL Alarme unter `POST /events/azure-monitor?key=<alertWebhookKey>` im Common Alert Schema annehmen. Requests ohne `key` oder mit falschem Wert SHALL abgelehnt werden und keinen Auftrag erzeugen. Der Key SHALL nicht protokolliert werden.

#### Scenario: Gültiger Alarm
- **WHEN** `ops-intake` einen Fired-Alarm mit gültigem Key sendet
- **THEN** entsteht ein gespeichertes Ereignis mit `source` `azure-monitor`, Alarm-ID als `externalRef`, Schweregrad und Zeitpunkt

#### Scenario: Key fehlt oder ist falsch
- **WHEN** ein Request ohne `key` oder mit falschem `key` eintrifft
- **THEN** antwortet der Eingang mit 401
- **AND** entsteht weder Ereignis noch Auftrag
- **AND** steht der übergebene Wert in keinem Log

### Requirement: Fired und Resolved unterscheiden
Der Ereigniseingang SHALL `monitorCondition` als `state` `fired` oder `resolved` übernehmen. Ein Resolved-Alarm SHALL nur den Alarmzustand des Vorfalls aktualisieren und keinen Vorfall ohne erfolgreiche fachliche Verifikation abschließen.

#### Scenario: Alarm automatisch zurückgesetzt
- **WHEN** ein authentifiziertes Resolved-Ereignis für einen offenen Vorfall eintrifft
- **THEN** wird der Alarmzustand des Vorfalls aktualisiert
- **AND** bleibt der Vorfall ohne passenden positiven Verifikationsnachweis ungelöst

### Requirement: App-Zuordnung von Alarmen
Der Ereigniseingang SHALL `appId` und `environment` zuerst aus den Custom Properties `app-id` und `environment` des Alarms lesen. Fehlen sie, SHALL er sie aus dem Regelnamen `alert-<app-id>-<env>-<check>` ableiten. Gelingt beides nicht, gilt der Alarm als Zuordnungsfehler.

#### Scenario: Custom Properties vorhanden
- **WHEN** ein Alarm die Custom Properties `app-id` `affine` und `environment` `staging` trägt
- **THEN** wird er `affine` in `staging` zugeordnet, unabhängig vom Regelnamen

#### Scenario: Nur Regelname vorhanden
- **WHEN** ein Alarm ohne Custom Properties von der Regel `alert-affine-staging-availability` stammt
- **THEN** wird er `affine` in `staging` zugeordnet

#### Scenario: Weder Properties noch passender Regelname
- **WHEN** ein Alarm keine Custom Properties trägt und sein Regelname nicht dem Muster entspricht
- **THEN** wird er als Zuordnungsfehler gespeichert
- **AND** entsteht kein ändernder Auftrag

### Requirement: GitHub-Webhook authentifizieren
Der Ereigniseingang SHALL Issue- und Kommentarereignisse aus dem Repo-Webhook von `mmkdigitalio/hackathon-platform-team` annehmen. Er SHALL die Signatur `X-Hub-Signature-256` über den unveränderten Request-Body mit `githubWebhookSecret` prüfen und nur Repositories der Allowlist annehmen. Ein Repo-Admin richtet den Webhook manuell nach Dokumentation ein.

(geändert 31.10.2026: Stand 31.10.2026 ist der Webhook nicht eingerichtet und bewusst zurückgestellt; Mitarbeitermeldungen sollen über einen UI-Chat kommen, geplant als eigener Change. Die Anforderungen an den Eingang gelten unverändert.)

#### Scenario: Ungültige Signatur
- **WHEN** ein Ereignis mit fehlender oder falscher Signatur eintrifft
- **THEN** wird es abgelehnt und erzeugt keinen Auftrag

#### Scenario: Nicht zugelassenes Repository
- **WHEN** ein Ereignis aus einem Repository außerhalb der Allowlist eintrifft
- **THEN** wird es trotz gültiger Signatur nicht zur Agentenausführung angenommen

### Requirement: Support-Issue-Form
Unter `.github/ISSUE_TEMPLATE/` SHALL eine Issue-Form mit Dropdowns für App-ID und Umgebung sowie Feldern für Beschreibung liegen. Der Ereigniseingang SHALL App und Umgebung aus den gerenderten Abschnitten `### App` und `### Umgebung` des Issue-Texts lesen.

#### Scenario: Support-Issue annehmen
- **WHEN** ein signiertes `issues`-Ereignis zu einem über die Form angelegten Issue mit App `affine` und Umgebung `staging` eintrifft
- **THEN** entsteht ein Auftrag mit Referenz auf Repository und Issue-Nummer für `affine` in `staging`
- **AND** kann der Agent im selben Issue antworten

#### Scenario: Issue ohne Form-Abschnitte
- **WHEN** ein Issue die Abschnitte `### App` oder `### Umgebung` nicht enthält
- **THEN** wird es als Zuordnungsfehler gespeichert und erzeugt keinen ändernden Auftrag

### Requirement: Schleifenschutz über Markierung
Der Ereigniseingang SHALL Kommentare, deren Text die Markierung `<!-- mmk-ops-agent -->` enthält, ignorieren. Der Autor des Kommentars SHALL dafür nicht ausgewertet werden (geändert 31.10.2026; war: „weil der Agent per PAT unter einem Teammitglied schreibt“; jetzt schreibt der Agent als GitHub App, ein PAT eines Teammitglieds ist nur Rückfall, E7).

#### Scenario: Agent kommentiert
- **WHEN** der Agent einen Diagnosekommentar mit `<!-- mmk-ops-agent -->` erstellt und dessen Webhook eintrifft
- **THEN** wird daraus kein neuer Auftrag und keine neue Untersuchung

#### Scenario: Mensch kommentiert mit demselben Account
- **WHEN** das Teammitglied, dessen PAT der Agent im Rückfall nutzt, selbst einen Kommentar ohne Markierung schreibt (geändert 31.10.2026; war: „dessen PAT der Agent nutzt“)
- **THEN** wird der Kommentar wie jeder andere Kommentar verarbeitet

### Requirement: Zustellungen deduplizieren
Der Ereigniseingang SHALL doppelte Zustellungen an der Kombination aus `source` und `deliveryId` erkennen. Eine wiederholte Zustellung SHALL auf den bereits gespeicherten Vorgang verweisen und nichts Neues auslösen.

#### Scenario: Dieselbe Zustellung mehrfach erhalten
- **WHEN** dieselbe Zustellung mit gleicher `source` und `deliveryId` erneut eintrifft
- **THEN** verweist die Antwort auf denselben gespeicherten Vorgang
- **AND** entstehen weder ein zweiter Auftrag noch eine zusätzliche Maßnahme

### Requirement: Vorfallzuordnung innerhalb einer Quelle
Fired- und Resolved-Ereignisse derselben Alarmreferenz SHALL demselben Vorfall zugeordnet werden, ebenso Kommentare desselben Issues. Ereignisse verschiedener Quellen SHALL in Phase 1 nicht automatisch zu einem Vorfall zusammengeführt werden.

#### Scenario: Resolved zum offenen Alarm
- **WHEN** nach einem Fired-Ereignis ein Resolved-Ereignis mit derselben Alarm-ID eintrifft
- **THEN** wird es demselben Vorfall zugeordnet

#### Scenario: Kommentar zum Support-Issue
- **WHEN** zu einem angenommenen Support-Issue ein Kommentar ohne Agentenmarkierung eintrifft
- **THEN** wird er dem Vorfall dieses Issues zugeordnet und erzeugt keinen neuen Vorfall

#### Scenario: Alarm und Issue zur selben App
- **WHEN** ein Alarm und ein Support-Issue dieselbe App und Umgebung betreffen
- **THEN** entstehen zwei getrennte Vorfälle mit je eigenem Herkunftsnachweis

### Requirement: Authentifizierter Wartungseingang
Der Ereigniseingang SHALL Wartungsaufträge unter `POST /events/maintenance` nur mit Header `X-Api-Key` gleich `maintenanceApiKey` annehmen. Fehlt der Header oder ist er falsch, SHALL der Request mit 401 abgelehnt werden. Der Key SHALL nicht protokolliert werden.

#### Scenario: Wartungsauftrag ohne Key
- **WHEN** ein Wartungsauftrag ohne oder mit falschem `X-Api-Key` eintrifft
- **THEN** antwortet der Eingang mit 401 und erzeugt keinen Auftrag

### Requirement: Geplanter Gesundheitscheck ohne unnötigen Modellaufruf
Der Workflow `.github/workflows/health-check.yml` SHALL zeitgesteuert deterministische Gesundheitsprüfungen ausführen und bei Diagnosebedarf einen Wartungsauftrag erzeugen. Er SHALL `maintenanceApiKey` über die Composite Action `.github/actions/foundation-outputs` lesen. Eine bestandene Prüfung SHALL keinen Modellaufruf auslösen.

#### Scenario: Gesunder planmäßiger Check
- **WHEN** der geplante Check seine deterministischen Prüfungen besteht
- **THEN** dokumentiert die Workflow-Zusammenfassung App, Umgebung und Zeitpunkt
- **AND** wird kein Auftrag erzeugt und kein Modell aufgerufen

#### Scenario: Fehlgeschlagener planmäßiger Check
- **WHEN** eine Prüfung fehlschlägt
- **THEN** sendet der Workflow einen Wartungsauftrag mit `X-Api-Key` an den Ereigniseingang
- **AND** steht der Key maskiert und nicht im Klartext im Workflow-Log

## Abgrenzung

C besitzt Empfänger, Normalisierung, Ereignisschema, Issue-Form und Gesundheitscheck-Workflow. A liefert Action Group `ops-intake`, Alarmregeln mit Custom Properties und die Keys und Secrets als Foundation-Ausgaben. Ein Repo-Admin richtet den GitHub-Webhook ein (Stand 31.10.2026 zurückgestellt). B liefert Katalog und fachliche Prüfungen, D die Issue-Antwort mit Markierung und die Diagnose nach der Annahme.
