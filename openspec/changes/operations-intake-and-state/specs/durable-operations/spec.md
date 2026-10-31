# Spec Delta

## Purpose

Betriebsaufträge, Maßnahmen und ihre Ergebnisse bleiben über Prozessausfälle hinweg nachvollziehbar und verhindern konkurrierende oder unkontrolliert wiederholte App-Änderungen.

## ADDED Requirements

### Requirement: Persistente und begrenzt geleaste Aufträge
Der Worker SHALL gespeicherte Aufträge atomar mit zeitlich begrenztem Besitz und Besitzversion übernehmen. Gleichzeitige Worker SHALL denselben Auftrag nicht unabhängig ausführen. Nach Ablauf des Besitzes SHALL eine kontrollierte Wiederaufnahme möglich sein; veraltete Besitzer SHALL keine neuen Änderungen veranlassen.

#### Scenario: Zwei Worker übernehmen gleichzeitig (lokaler Test)
- **WHEN** in einem lokalen Test zwei Worker denselben wartenden Auftrag übernehmen wollen
- **THEN** erhält nur einer gültigen Besitz
- **AND** führt der andere keine Untersuchung oder Maßnahme für diesen Auftrag aus

#### Scenario: Worker fällt aus
- **WHEN** ein Worker seinen Besitz nicht mehr erneuert
- **THEN** kann ein Worker den Auftrag nach Ablauf übernehmen
- **AND** prüft er gespeicherte Aktionen und externe Ausführungen vor weiteren Änderungen

#### Scenario: Veralteter Besitzer schreibt
- **WHEN** ein Worker mit abgelaufener Besitzversion einen Zustand oder eine Aktion speichern will
- **THEN** wird die Änderung abgewiesen

### Requirement: Ein Worker-Replica in Staging
In Staging SHALL genau ein Worker-Replica laufen. Konkurrenz mehrerer Worker SHALL durch lokale Tests gegen PostgreSQL nachgewiesen werden, nicht durch einen Mehr-Replica-Betrieb in Staging.

#### Scenario: Staging-Release des Agenten
- **WHEN** das Agentenpaket in Staging ausgerollt ist
- **THEN** läuft genau ein Worker-Pod
- **AND** liegen die Nachweise für konkurrierende Worker als lokale Testergebnisse vor

### Requirement: Deterministische Zustandsmaschine
Die Auftragsverwaltung SHALL `received`, `queued`, `investigating`, `acting`, `verifying`, `resolved`, `failed` und `needs_attention` unterstützen. Sie SHALL zulässige Übergänge und deren Voraussetzungen unabhängig vom Modell prüfen und abgelehnte Übergänge ohne Zustandsänderung protokollieren.

#### Scenario: Modell behauptet Erfolg ohne Nachweis
- **WHEN** der Agent `resolved` ohne passenden positiven Verifikationsnachweis anfordert
- **THEN** wird der Übergang abgelehnt und protokolliert
- **AND** bleibt der Auftrag sichtbar ungelöst

### Requirement: Verifikation an die tatsächliche Maßnahme binden
Ein `resolved`-Übergang SHALL einen positiven fachlichen Test erfordern, der nach der letzten Maßnahme des Auftrags begonnen hat, sowie den danach erfassten Kubernetes-Zustand des Ziels mit dem erwarteten Release. Beides SHALL zu App, Umgebung und Zielstand passen. Azure-Messwerte SHALL keine Voraussetzung sein.

#### Scenario: Veralteter positiver Test
- **WHEN** nur ein positiver Test von vor der Reparatur vorliegt
- **THEN** wird der Vorfall nicht als behoben gespeichert
- **AND** wird eine neue passende Verifikation verlangt

#### Scenario: Test bestanden, Kubernetes-Zustand fehlt
- **WHEN** ein positiver Test nach der Maßnahme vorliegt, aber kein danach erfasster Kubernetes-Zustand des Ziels
- **THEN** wird `resolved` abgelehnt

#### Scenario: Falscher Zielstand
- **WHEN** der erfasste Kubernetes-Zustand einen anderen Release als den erwarteten zeigt
- **THEN** wird `resolved` abgelehnt

### Requirement: Aktionsprotokoll vor externem Aufruf
Jede externe Betriebsmaßnahme SHALL vor dem Aufruf eine stabile `operationId`, eine eigene `actionId` und einen gespeicherten Aktionsdatensatz mit Ziel, Art und Status erhalten. Externe Referenzen und unklare Ergebnisse SHALL unter derselben Identität ergänzt werden und über Neustarts erhalten bleiben.

#### Scenario: Prozessabbruch nach Workflow-Start
- **WHEN** der Prozess nach externem Start und vor dessen lokaler Bestätigung endet
- **THEN** bleibt eine offene oder unklare Aktion mit stabiler Identität erhalten
- **AND** fordert die Wiederaufnahme einen Abgleich statt eines erneuten Starts an

### Requirement: Wiederaufnahme externer Ausführungen
Ein wiederaufgenommener Auftrag SHALL für gestartete oder unklare externe Aktionen den Abgleich des Agenten verwenden. Ein bekannter Workflow SHALL anhand seiner bestehenden Ausführungsreferenz weiterverfolgt werden. Nicht aufklärbare Ergebnisse SHALL weitere potenziell doppelte Änderungen verhindern und den Auftrag in `needs_attention` beenden.

#### Scenario: Bereits laufenden Workflow fortsetzen
- **WHEN** der Worker mit gespeichertem GitHub-Lauf neu startet
- **THEN** verfolgt er denselben Lauf weiter
- **AND** erzeugt er keinen zweiten Workflow für dieselbe Aktion

### Requirement: Gemeinsame Operations-Sperre
Agentenmaßnahmen und App-Deployments SHALL dieselbe exklusive Sperre je App und Umgebung in PostgreSQL verwenden. Erwerb, Erneuerung, Besitzprüfung und Freigabe SHALL den Besitzer prüfen. Ungeklärte externe Änderungen SHALL vor Vergabe an eine neue ändernde Operation abgeglichen werden. Der Foundation-Stack SHALL keine Sperre nehmen.

#### Scenario: Unabhängige Operationen
- **WHEN** zwei Operationen unterschiedliche Apps oder Umgebungen betreffen
- **THEN** blockieren sie sich nicht

#### Scenario: Freigabe durch fremden Besitzer
- **WHEN** eine Operation eine Sperre freigeben will, die ihr nicht gehört
- **THEN** wird die Freigabe abgewiesen und die Sperre bleibt bestehen

#### Scenario: Sperre abgelaufen, externe Operation unklar
- **WHEN** ein Sperrbesitz abläuft und eine zugehörige externe Änderung nicht abschließend geklärt ist
- **THEN** wird keine neue Änderung allein wegen des abgelaufenen Zeitlimits zugelassen
- **AND** ist zuerst ein Abgleich oder ein sichtbarer Klärungsfall erforderlich

#### Scenario: Foundation-Lauf während gehaltener Sperre
- **WHEN** der Foundation-Stack ausgerollt wird, während eine App-Sperre gehalten wird
- **THEN** läuft er ohne Sperrabfrage durch

### Requirement: Konfliktregel der Sperre
Ein Deploy-Workflow SHALL bis zu 15 Minuten auf eine belegte Sperre warten und danach mit Fehler abbrechen. Der Agent SHALL nicht warten, sondern den Auftrag mit `needs_attention` und dem Grund „gesperrt durch Operation <operationId>“ beenden.

#### Scenario: Pipeline hält die Sperre, Agent will ändern
- **WHEN** ein Deploy-Workflow die Sperre für `affine` in `staging` hält und der Agent eine Maßnahme dort beginnen will
- **THEN** beendet die Auftragsverwaltung den Auftrag mit `needs_attention` und nennt die `operationId` des Workflows
- **AND** führt der Agent keine Änderung aus

#### Scenario: Agent hält die Sperre, Workflow startet
- **WHEN** ein Deploy-Workflow startet, während der Agent die Sperre hält
- **THEN** wartet der Workflow höchstens 15 Minuten
- **AND** bricht er danach ohne Zieländerung ab, falls die Sperre weiter belegt ist

### Requirement: Break-glass bei unerreichbarer Datenbank
Ist PostgreSQL nicht erreichbar, SHALL ohne `--force` keine Änderung beginnen. Mit `--force` SHALL die Sperr-CLI einen Deploy-Workflow ohne Sperre fortsetzen lassen, wenn PostgreSQL nicht erreichbar ist, und das in der Workflow-Zusammenfassung vermerken. Bei erreichbarem PostgreSQL SHALL `--force` wirkungslos sein. Der Agent SHALL `--force` nicht verwenden.

#### Scenario: Recovery-Deployment ohne Datenbank
- **WHEN** PostgreSQL nicht erreichbar ist und ein Deploy-Workflow die CLI mit `--force` aufruft
- **THEN** läuft das Deployment ohne Sperre
- **AND** vermerkt die Workflow-Zusammenfassung Break-glass, App, Umgebung, Git-SHA und Zeitpunkt

#### Scenario: Ohne force bei unerreichbarer Datenbank
- **WHEN** PostgreSQL nicht erreichbar ist und die CLI ohne `--force` aufgerufen wird
- **THEN** bricht der Workflow vor jeder Zieländerung ab

#### Scenario: force bei erreichbarer Datenbank
- **WHEN** PostgreSQL erreichbar ist und die Sperre belegt ist
- **THEN** gilt trotz `--force` die normale Konfliktregel

### Requirement: Nachtrag nach Break-glass
Nach Wiederherstellung von PostgreSQL SHALL ein Break-glass-Deployment als Operation mit Vermerk `break-glass` ins Aktionsprotokoll nachgetragen werden. Die Sperr-CLI SHALL dafür einen Befehl bieten, der die Angaben aus dem Vermerk der Workflow-Zusammenfassung übernimmt.

#### Scenario: Datenbank wieder erreichbar
- **WHEN** PostgreSQL wieder erreichbar ist und der Nachtrag nach Dokumentation ausgeführt wird
- **THEN** enthält das Aktionsprotokoll das Break-glass-Deployment mit App, Umgebung, Git-SHA, Workflow-Lauf und Zeitpunkt

### Requirement: Sperrclient ohne laufende Agenten-API
Sperre und Aktionsnachweise SHALL über eine eigenständige Bibliothek und CLI nutzbar sein, die direkt auf PostgreSQL zugreifen. GitHub-Runner SHALL sich mit `postgresAdminPassword` aus den Foundation-Ausgaben anmelden, ohne Azure-Identität. Ein Ausfall von API oder Worker SHALL den Zugriff nicht verhindern.

#### Scenario: Erstes Agenten-Deployment
- **WHEN** noch keine Agenten-API läuft und das erste Agentenpaket ausgerollt wird
- **THEN** erwirbt der Deploy-Workflow über die CLI die Sperre in PostgreSQL
- **AND** verwendet er keine temporäre separate Sperrlösung und keine Azure-Anmeldung

### Requirement: Versionierte Migrationen
Die Auftragsverwaltung SHALL eine Migrations-CLI liefern, die das Schema der Datenbank `operations` versioniert anlegt und aktualisiert und bei erneutem Lauf nichts verändert. Das Agenten-Chart SHALL sie als Helm-Hook-Job mit `pre-install,pre-upgrade` ausführen. API und Worker SHALL keine Migrationen ausführen.

#### Scenario: Migration erneut ausführen
- **WHEN** die Migrations-CLI auf einem aktuellen Schema erneut läuft
- **THEN** endet sie erfolgreich ohne Änderung und ohne Datenverlust

#### Scenario: Agenten-Release mit Schemaänderung
- **WHEN** ein Agenten-Release eine neue Schema-Version benötigt
- **THEN** läuft der Migrations-Job vor dem Ersetzen von API und Worker
- **AND** bleiben bestehende Aufträge, Aktionen und Checkpoints lesbar

### Requirement: Erstinitialisierung vor dem ersten Deployment
Vor dem ersten App-Deployment SHALL der manuell startbare Workflow `.github/workflows/ops-migrate.yml` die Migrations-CLI einmal gegen die Foundation-Datenbank ausführen, weil die Sperre ihr Schema schon vor dem ersten Agenten-Release braucht. Gleichzeitige Migrationsläufe SHALL über eine PostgreSQL-Advisory-Lock serialisiert werden. Fehlt das Schema, SHALL der Sperrclient mit einem Hinweis auf `ops-migrate.yml` abbrechen.

#### Scenario: Erstes Deployment ohne Schema
- **WHEN** ein Deploy-Workflow die Sperre erwerben will, bevor `ops-migrate.yml` gelaufen ist
- **THEN** bricht der Sperrclient ab und nennt `ops-migrate.yml` als nächsten Schritt
- **AND** beginnt keine Zieländerung

#### Scenario: Hook und Workflow gleichzeitig
- **WHEN** `ops-migrate.yml` und der Helm-Hook-Job gleichzeitig migrieren
- **THEN** wartet einer auf die Advisory-Lock und findet danach ein aktuelles Schema vor

### Requirement: Schema-Versionsprüfung beim Start
API und Worker SHALL beim Start nur die Schema-Version der Datenbank prüfen. Passt sie nicht zur erwarteten Version, SHALL der Prozess mit einer Meldung beenden, die erwartete und vorhandene Version nennt.

#### Scenario: Migration fehlt
- **WHEN** API oder Worker gegen ein älteres Schema starten
- **THEN** beendet sich der Prozess mit erwarteter und vorhandener Schema-Version
- **AND** verändert er das Schema nicht

### Requirement: Persistenz außerhalb der Container
Aufträge, Aktionen, Heartbeat und SDK-Checkpoints SHALL in PostgreSQL außerhalb der Container liegen. Die SDK-Anbindung des Agenten SHALL ihre Tabellen über dieselbe Migrations-CLI anlegen und keinen eigenen Migrationsablauf einführen.

#### Scenario: API und Worker neu ausrollen
- **WHEN** ein Agenten-Release beide Prozesse ersetzt
- **THEN** sind bestehende Aufträge, Aktionen und Checkpoints danach lesbar

### Requirement: Worker-Heartbeat
Der Worker SHALL mindestens jede Minute einen Heartbeat mit Worker-ID und Datenbankzeit in PostgreSQL schreiben, auch während er einen Auftrag ausführt.

#### Scenario: Worker arbeitet einen langen Auftrag ab
- **WHEN** ein Auftrag länger als zehn Minuten läuft
- **THEN** ist der letzte Heartbeat jederzeit höchstens etwa eine Minute alt

### Requirement: Gesundheitsendpunkt
Die API SHALL `GET /healthz` ohne Authentifizierung bereitstellen. Der Endpunkt SHALL 503 antworten, wenn die Datenbank nicht erreichbar ist oder der letzte Worker-Heartbeat älter als 10 Minuten ist oder keiner existiert, sonst 200. Die Antwort SHALL keine Secrets enthalten.

#### Scenario: Worker gestoppt
- **WHEN** der Worker seit mehr als 10 Minuten keinen Heartbeat geschrieben hat
- **THEN** antwortet `/healthz` mit 503

#### Scenario: Datenbank nicht erreichbar
- **WHEN** die API PostgreSQL nicht erreicht
- **THEN** antwortet `/healthz` mit 503

#### Scenario: Normalbetrieb
- **WHEN** die Datenbank erreichbar ist und der letzte Heartbeat weniger als 10 Minuten alt ist
- **THEN** antwortet `/healthz` mit 200

### Requirement: Ein aufrufbarer Agent statt zweiter Auftragsverwaltung
Der Worker SHALL die Agentenausführung über einen versionierten Auftrag-/Ergebnisvertrag unter `operations-agent/src/contracts/` aufrufen. Die Auftragsverwaltung SHALL Besitz, Zustandsübergänge und Persistenz kontrollieren; der Agent SHALL Diagnose und Maßnahmen über die bereitgestellten Dienste ausführen.

#### Scenario: Agentenausführung lokal ersetzen
- **WHEN** die Auftragsverwaltung mit einer vertragskonformen Testausführung geprüft wird
- **THEN** lassen sich Leases und Zustandsregeln ohne echten Modellaufruf testen
- **AND** besitzt die Testausführung keine eigene Queue oder Zustandsdatenbank

### Requirement: Durchgängige Laufnachweise
Für jeden Auftrag SHALL Ereignis, Vorfall, App, Umgebung, Code- und Konfigurationsstand, Modellkonfiguration, Werkzeugaufrufe, externe Referenzen, verfügbare Verbrauchsdaten und Verifikation verknüpft gespeichert sein. Bestätigte Ergebnisse SHALL von Vermutungen unterscheidbar sein. Modellgedanken SHALL kein erforderlicher Nachweis sein.

#### Scenario: Abgeschlossenen Lauf prüfen
- **WHEN** ein Verantwortlicher eine Auftrags-ID untersucht
- **THEN** findet er die genannten Nachweise und das begründete Ergebnis
- **AND** unterscheiden die Daten bestätigte Ergebnisse von Vermutungen

### Requirement: Laufnachweise abrufen
Alle mit einem Auftrag verknüpften Nachweise SHALL als JSON ohne Secrets abrufbar sein, über `GET /runs/{auftragsId}` mit Header `X-Api-Key` gleich `maintenanceApiKey` oder über die CLI `ops runs show <id>`. Ohne gültigen Key SHALL der Endpunkt mit 401 antworten.

#### Scenario: Nachweise abrufen
- **WHEN** ein Verantwortlicher die Nachweise zu einer bestehenden Auftrags-ID mit gültigem Key abruft
- **THEN** erhält er ein JSON mit allen verknüpften Nachweisen
- **AND** enthält es weder Keys noch Passwörter noch Tokens

#### Scenario: Abruf ohne Key
- **WHEN** `GET /runs/{auftragsId}` ohne oder mit falschem `X-Api-Key` aufgerufen wird
- **THEN** antwortet der Endpunkt mit 401 und liefert keine Daten

## Abgrenzung

C besitzt Zustands- und Sperrregeln, Datenmodell, Migrations-CLI, Heartbeat, `/healthz`, Laufnachweisabruf und Worker-Auftragsübernahme. A liefert Datenbankserver, Admin-Passwort als Foundation-Ausgabe und den Verfügbarkeitstest auf `/healthz`. B verwendet die Sperr-CLI in den Deploy-Workflows. D liefert Agentenausführung, externe Abgleiche, Verifikationsbefunde sowie das Chart mit Migrations-Hook und einem Worker-Replica. SDK-Checkpoints ersetzen weder Aktionsprotokoll noch Operations-Sperre.

(geändert 31.10.2026) Die lesende Statusprojektion `GET /status` samt Status-Seiten wird zusätzlich von der Agenten-API ausgeliefert und läuft als eigenständige App `platform-status`. Ihre Anforderungen stehen im Change `platform-status-ui`. `/healthz` bleibt der maßgebliche Gesundheitsendpunkt dieses Changes.
