# Spec Delta

## Purpose

Eine schreibgeschützte Statusansicht zeigt den aktuellen Zustand der Apps, der Operations-Datenbank und des Agent-Workers, ohne Geheimnisse preiszugeben und ohne von der Verfügbarkeit des Agenten abzuhängen. Dashboard, Anwendungsanalyse und Aktivitäten sind getrennte Admin-Aufgaben; die folgenden Requirements definieren ihre Daten und Bedienung.

## ADDED Requirements

### Requirement: Ausschließlich lesende Statusansicht
Die Statusansicht SHALL nur lesende Endpunkte bereitstellen (`GET /`, `GET /applications`, `GET /applications/<app-id>` und `GET /activity` für die Seiten, `GET /status` für die JSON-Projektion, `GET /status-assets/*` für statische Dateien, `GET /healthz` im eigenständigen Dienst). Sie SHALL keine Ereignisse, Eingaben oder Aktionen annehmen, keine Betriebsmaßnahme auslösen und weder Datenbank, Kubernetes noch GitHub verändern. Die Seite SHALL keine bedienbare schreibende Funktion enthalten. Datenbankabfragen SHALL in einer lesenden Transaktion mit Statement-Timeout laufen.

#### Scenario: Schreibender Request
- **WHEN** ein `POST`, `PUT`, `PATCH` oder `DELETE` an `/`, `/status` oder `/healthz` des eigenständigen Dienstes gesendet wird
- **THEN** wird er mit HTTP 405 abgelehnt
- **AND** ändert sich kein Zustand

#### Scenario: Datenbankabfrage
- **WHEN** die Statusansicht die Operations-Datenbank abfragt
- **THEN** läuft die Abfrage in einer als lesend gesetzten Transaktion
- **AND** bricht sie nach spätestens 2,5 Sekunden ab

### Requirement: Keine Geheimnisse und minimale Projektion
Die öffentliche Projektion SHALL ausschließlich Umgebung, Prüfzeitpunkt, Gesamt- und Komponentenzustände, App-IDs, Namen, Sollversionen, Hosts, HTTP-Status, Latenz, kurze feste Fehlerbeschreibungen, Heartbeat-Alter, Auftragszahlen, erlaubte Monitoring-Metadaten, strukturierte Quellen und Tätigkeiten, Ergebnis- und Verifikationsstatus, dokumentierte Release-Metadaten und erlaubte GitHub-Lauf-Links enthalten. Sie SHALL keine Auftrags- oder Vorfalltexte, Auftrags- oder Vorfall-IDs, Logs, Modellantworten, Zugangsdaten, Connection-Strings, Secret-Werte oder rohen Ausnahmetexte enthalten. Fehler SHALL nur als betroffene Komponente und fester Kurztext sichtbar werden.

#### Scenario: Datenbankfehler mit Zugangsdaten in der Ausnahme
- **WHEN** die Datenbankverbindung mit einer Ausnahme scheitert, die Host oder Passwort enthält
- **THEN** zeigt die Projektion die Datenbank als `unavailable`
- **AND** enthält keine Antwort Host, Passwort oder Ausnahmetext

#### Scenario: Auftragsdaten
- **WHEN** Aufträge mit Vorfalltexten und IDs in der Datenbank liegen
- **THEN** liefert die Projektion je Auftrag nur App-ID, Zustand und Aktualisierungszeit

### Requirement: Feste Prüfziele
Prüfziele SHALL ausschließlich aus Konfiguration (`OPS_BASE_DOMAIN`, `OPS_PUBLIC_SCHEME`) und dem App-Katalog (App-ID, `availability.path`) abgeleitet werden. Die Statusansicht SHALL kein Ziel, keinen Host und keinen Pfad aus einem Request übernehmen. Ungültige Prüfpfade (nicht mit `/` beginnend, `//`, Query oder Fragment) und ungültige Basisdomains SHALL kein Prüfziel ergeben.

#### Scenario: Request mit Zielangabe
- **WHEN** ein Request einen Host oder Pfad als Parameter mitsendet
- **THEN** werden Prüfziele und Ergebnis dadurch nicht verändert

#### Scenario: Ungültiger Prüfpfad im Katalog
- **WHEN** ein Katalogeintrag einen Prüfpfad `//evil` oder `/x?y` enthält
- **THEN** wird für diese App nicht geprüft
- **AND** gilt der Katalog als unvollständig

### Requirement: Unabhängige Bereitstellung mit demselben Image
Die Statusansicht SHALL als eigenständige App `platform-status` mit eigenem Helm-Chart (`platform/infra/status-ui/`), eigenem Katalogeintrag (`platform/catalog/platform-status/app.yaml`) und eigenem Sollzustand je Umgebung bereitgestellt werden. Sie SHALL dasselbe Container-Image wie der Operations-Agent verwenden, per Digest im Sollzustand referenziert. Der eigenständige Dienst SHALL keinen Worker, keine Migration, keinen Ereigniseingang, kein Modell und keinen GitHub-Zugang starten und das Kubernetes-ServiceAccount-Token nicht mounten. Ihr Rollout SHALL über den regulären App-Pfad (`app-deploy.yml` mit Operations-Sperre, Plan und Smoke-Test) laufen und weder vom Agenten-Rollout noch von dessen Zugängen (etwa `githubAppPrivateKey`) abhängen. Der Katalogeintrag SHALL keine erlaubten Aktionen enthalten.

#### Scenario: Agent nicht ausgerollt
- **WHEN** der Operations-Agent nicht erreichbar ist und kein Worker-Heartbeat existiert
- **THEN** ist `platform-status` trotzdem erreichbar und ausrollbar
- **AND** zeigt den Agenten und den Worker als gestört an

#### Scenario: Chart prüfen
- **WHEN** das Chart unter `platform/infra/status-ui/` gerendert wird
- **THEN** startet der Pod nur den Statusdienst aus dem referenzierten Image-Digest
- **AND** ist `automountServiceAccountToken` ausgeschaltet
- **AND** läuft der Container nicht als Root, mit schreibgeschütztem Wurzeldateisystem und ohne zusätzliche Linux-Capabilities

#### Scenario: Neuer Image-Digest
- **WHEN** ein neues Image gebaut wurde
- **THEN** ändert sich das laufende Release erst, wenn der Digest im Sollzustand ausdrücklich aktualisiert wurde

### Requirement: Dieselbe Projektion in der Agenten-API
Die Agenten-API SHALL dieselbe Statusansicht weiterhin einbinden, ohne ihre Ereigniseingänge, Authentifizierung oder `/healthz`-Semantik zu verändern. Der eigenständige Dienst SHALL den Nachweis-Endpunkt `/runs/{auftragsId}` nicht anbieten.

#### Scenario: Eigenständiger Dienst
- **WHEN** `/runs/<id>` oder ein `/events/*`-Endpunkt am eigenständigen Dienst aufgerufen wird
- **THEN** antwortet er mit HTTP 404

### Requirement: Datenquellen und Zustandssemantik
Die Statusansicht SHALL für jede App mit gültigem Katalogeintrag und Sollzustand der Umgebung eine HTTP-Prüfung ausführen: TLS prüfen, Redirects nicht verfolgen, Antwortkörper nicht laden, 4 Sekunden Timeout. Nur HTTP 200 SHALL `healthy` ergeben, jede andere Antwort und jeder Verbindungs-, TLS- oder Timeout-Fehler `unavailable`, fehlende Konfiguration `unknown`. Datenbankzustand, Worker-Heartbeat, Zahl aktiver Aufträge (`received`, `queued`, `investigating`, `acting`, `verifying`), Zahl der in den letzten 24 Stunden `resolved` sowie `failed` oder `needs_attention` gewordenen Aufträge und die jüngsten Aufträge SHALL aus der Operations-Datenbank gelesen und auf die konfigurierte Umgebung (`staging` oder `production`) beschränkt werden. Der Worker SHALL `unavailable` sein, wenn kein Heartbeat existiert oder er älter als die Grenze von `/healthz` ist (Standard 600 Sekunden). Der Gesamtzustand SHALL `degraded` sein, sobald eine App, die Datenbank oder der Worker `unavailable` ist, sonst `unknown`, wenn eine Komponente `unknown` ist oder der Katalog nicht vollständig lesbar ist, sonst `healthy`. Ein Datenbankfehler SHALL als `unavailable` mit unbekannten Zahlen (`null`) erscheinen und nie als null Aufträge.

#### Scenario: Redirect statt Gesundheitsantwort
- **WHEN** der Prüfpfad einer App mit HTTP 301 oder 302 antwortet
- **THEN** gilt die App als `unavailable`
- **AND** wird dem Redirect nicht gefolgt

#### Scenario: TLS-Fehler
- **WHEN** das Zertifikat des Prüfziels ungültig ist
- **THEN** gilt die App nicht als `healthy`

#### Scenario: Datenbank nicht erreichbar
- **WHEN** die Operations-Datenbank nicht antwortet
- **THEN** ist die Datenbank `unavailable` und der Worker `unknown`
- **AND** sind Auftragszahlen `null` statt 0
- **AND** ist der Gesamtzustand nicht `healthy`

#### Scenario: Kein Heartbeat
- **WHEN** die Datenbank erreichbar ist, aber kein Heartbeat existiert
- **THEN** ist der Worker `unavailable` und das Heartbeat-Alter `null`
- **AND** ist der Gesamtzustand `degraded`

#### Scenario: Katalog nicht lesbar
- **WHEN** Katalog oder Sollzustände der Umgebung fehlen oder ein Eintrag fehlerhaft ist
- **THEN** ist der Gesamtzustand nicht `healthy`

#### Scenario: Gesunder Zustand ist keine Funktionsgarantie
- **WHEN** alle Prüfungen `healthy` ergeben
- **THEN** besagt das nur, dass die geprüften Komponenten antworten
- **AND** zeigt die Ansicht weder erfundene Uptime-Werte noch funktionale Testergebnisse

### Requirement: Sollversion statt Istversion
Die angezeigte Version einer App SHALL die Sollversion aus dem Sollzustand im Image sein und als solche gekennzeichnet werden. Sie SHALL nicht als laufende Version dargestellt werden.

#### Scenario: Version fehlt
- **WHEN** der Sollzustand weder `upstreamRelease` noch `chart.version` enthält
- **THEN** ist die Version unbekannt und wird nicht erfunden

### Requirement: Aktualisierung und Veralterung
Der Server SHALL den Snapshot höchstens alle 10 Sekunden neu berechnen und mehrere gleichzeitige Abrufe auf eine Berechnung bündeln. `/status` SHALL `Cache-Control: no-store`, `checkedAt` und `refreshAfterSeconds` liefern. Die Seite SHALL die Daten automatisch im durch `refreshAfterSeconds` angegebenen Abstand (Standard 30 Sekunden) und beim erneuten Sichtbarwerden der Seite aktualisieren und eine manuelle Aktualisierung anbieten. Schlägt ein Abruf fehl oder ist der Stand älter als 90 Sekunden, SHALL die Seite den letzten Stand als veraltet kennzeichnen und den Gesamtzustand als unbekannt anzeigen, statt ihn als gesund stehen zu lassen. Ohne JavaScript SHALL ein Hinweis auf `/status` verweisen.

#### Scenario: Status-API fällt aus
- **WHEN** die Seite geladen ist und `/status` danach nicht mehr antwortet
- **THEN** bleibt der letzte Stand sichtbar und ist als veraltet markiert
- **AND** zeigt der Gesamtzustand „unbekannt“, nicht „gesund“

#### Scenario: Viele gleichzeitige Besucher
- **WHEN** innerhalb von 10 Sekunden viele Abrufe eingehen
- **THEN** führt der Dienst die Prüfungen höchstens einmal aus

#### Scenario: Antwort nicht zwischenspeicherbar
- **WHEN** `/status` abgerufen wird
- **THEN** erlaubt der Header keine Zwischenspeicherung

### Requirement: Eigener Gesundheitsendpunkt des eigenständigen Dienstes
Der eigenständige Dienst SHALL `GET /healthz` bereitstellen, der nur den Statusprozess prüft und 200 liefert, unabhängig von Datenbank, Worker und Apps. Komponentenzustände SHALL ausschließlich in `/status` stehen.

#### Scenario: Datenbank fällt aus
- **WHEN** die Operations-Datenbank nicht erreichbar ist
- **THEN** antwortet `/healthz` des eigenständigen Dienstes weiterhin mit 200
- **AND** zeigt `/status` die Datenbank als `unavailable`

### Requirement: Getrennte Admin-Aufgaben
Die UI SHALL ein priorisiertes Dashboard unter `/`, einen suchbaren Bestand unter `/applications`, Detailseiten unter `/applications/<app-id>` und einen Verlauf unter `/activity` bereitstellen. Alle Seiten SHALL echte Links, eindeutige Titel, sichtbare aktive Navigation und einen mobilen Zugang haben. Das Dashboard SHALL zu gefilterten Analyseansichten führen.

#### Scenario: Tagescheck mit Störung
- **WHEN** der Admin eine gestörte App auf dem Dashboard auswählt
- **THEN** öffnet sich deren eigene Detailseite mit Zustand, Monitoring und Aktivitäten
- **AND** bleibt die Übersicht als eigener Anwendungsfall erreichbar

#### Scenario: Direkte Detail-URL
- **WHEN** eine gültige App-Detailadresse neu geladen wird
- **THEN** wird dieselbe App angezeigt
- **AND** liefert eine unbekannte App HTTP 404

### Requirement: Auffindbare Anwendungen
Die Anwendungsliste SHALL nach Name oder Host suchbar und nach Erreichbarkeit bzw. fehlendem Prüfziel filterbar sein. Suchtext und Filter SHALL in der URL stehen und Aktualisierung sowie Browser-Zurück überstehen. Die angezeigte Versionskonfiguration SHALL von einem dokumentierten Rollout getrennt sein.

#### Scenario: Suche und Zurück
- **WHEN** der Admin eine gefilterte App öffnet und über Browser-Zurück zurückkehrt
- **THEN** sind Suche und Filter unverändert

### Requirement: Nachvollziehbare Überwachung
Die Detailseite SHALL Live-HTTP-Prüfung, konfigurierte externe Verfügbarkeit und konfigurierten Smoke-Test unterscheiden. Sie SHALL Prüfpfad, TLS-Verhalten, Takt, Alarmziel und vorhandene Signal- bzw. Verifikationszeitpunkte nennen. Aus IaC abgeleitete Monitoring-Konfiguration SHALL nicht als verifizierte aktive Azure-Konfiguration erscheinen.

#### Scenario: Noch kein externes Signal
- **WHEN** die externe Überwachung konfiguriert ist, aber kein empfangenes Signal existiert
- **THEN** zeigt die Seite Konfiguration und fehlenden Signalnachweis separat
- **AND** behauptet nicht, Azure-Monitor sei aktiv oder ausgefallen

### Requirement: Strukturierter Aktivitätenverlauf
Der Verlauf SHALL Erkennungen, strukturierte Agentenbefunde, Agentenaktionen, Systemaktionen und Abschlüsse anhand fester Beschreibungen unterscheiden. Er SHALL nach App, Kategorie, Ergebnis und 24 Stunden bzw. sieben Tagen filterbar sein. Die URL SHALL Filter enthalten. Der Ausschnitt SHALL auf 100 jüngste Einträge aus sieben Tagen begrenzt und diese Grenze sichtbar sein. Rohe Payloads, Gründe, Findingtexte, Targets, interne IDs und freie Meldungen SHALL nicht ausgegeben werden.

#### Scenario: System-Deployment und Agentenaktion
- **WHEN** beide in der Datenbank dokumentiert sind
- **THEN** sind sie als System bzw. Agent erkennbar
- **AND** lässt sich die betroffene App direkt öffnen

#### Scenario: Verlauf nicht verfügbar
- **WHEN** die Verlaufsabfrage fehlschlägt
- **THEN** zeigt die UI fehlende Daten statt „keine Aktivitäten“ oder null Probleme

### Requirement: Behebungen mit Prüfnachweis
„Verifiziert abgeschlossen“ SHALL einen Auftrag im Zustand `resolved` und eine erfolgreiche jüngste Verifikation desselben Auftrags erfordern. Ein erfolgreicher Aktions- oder Deployment-Status allein SHALL nicht ausreichen. Monitoring-Störungen, Support-Fälle und andere Aufträge SHALL anhand ihrer strukturierten Quelle getrennt benannt werden; ein erfolgreicher Wartungsauftrag SHALL nicht als behobene Monitoring-Störung erscheinen. Ein aktueller App-Ausfall SHALL unabhängig von früheren Behebungen angezeigt werden.

#### Scenario: Abschluss ohne erfolgreiche jüngste Verifikation
- **WHEN** ein Auftrag resolved ist, aber der jüngste Prüfnachweis fehlt oder fehlschlug
- **THEN** erscheint er als abgeschlossen, nicht als verifiziert behoben

#### Scenario: Spätere erneute Störung
- **WHEN** eine früher verifiziert behobene App heute nicht erreichbar ist
- **THEN** bleibt ihr aktueller Zustand gestört
- **AND** bleibt die frühere Behebung ein historischer Eintrag

### Requirement: Dokumentierter Release-Stand
Die App-Detailseite SHALL den letzten gespeicherten Deployment-Lauf mit Zeitpunkt und Ergebnis zeigen, sofern vorhanden. „Verifiziert“ SHALL nur bei erfolgreichem Deployment-Ergebnis, erfolgreicher Verifikation und passendem Soll-/Ist-Release erscheinen. GitHub-Links SHALL ausschließlich auf das bekannte Plattform-Repository und dessen Actions-Läufe zeigen. Diese Angaben SHALL als letzter dokumentierter Rollout, nicht als Live-Kubernetes-Inventar bezeichnet werden.

#### Scenario: Unverifizierter Rollout
- **WHEN** das gespeicherte Deployment-Ergebnis keinen passenden Prüfnachweis enthält
- **THEN** wird kein verifizierter Release behauptet

## Abgrenzung

C besitzt `operations-agent/src/api/` einschließlich `status.py` und `static/`, B Katalogeintrag, Sollzustand und App-Rollout, A den daraus erzeugten Verfügbarkeitstest. Die Statusansicht ist kein Verwaltungsportal und kein Ersatz für ein unabhängiges externes Monitoring.
