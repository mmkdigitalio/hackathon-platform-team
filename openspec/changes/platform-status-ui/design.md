# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md), die Anforderungen in [platform-status](specs/platform-status/spec.md). Die Statusseite entstand ohne Spec; dieses Dokument beschreibt den vorgefundenen Stand (30.10.2026) und trennt stabile Entscheidungen von vorläufigem Inhalt. Maßgeblich bleibt der Code; das Runbook liegt in `platform/operations/state/status-ui.md`.

## Goals / Non-Goals

Goals:

- Stabile Eigenschaften festschreiben, die auch nach der inhaltlichen Überarbeitung gelten: lesend, keine Geheimnisse, unabhängig bereitgestellt, nachvollziehbare Zustandssemantik.
- Ehrlich dokumentieren, wo der Ist-Stand von diesen Eigenschaften abweicht (siehe Risiken).

Non-Goals:

- Kein Verwaltungsportal, keine Aktionen, keine Auftragsdetails, keine Anmeldung.
- Keine Spezifikation von Layout, Texten, Kennzahlen und Gestaltung.

## Decisions

### 1. Eine Projektion, zwei Hüllen (stabil)

`status.py` stellt `install_status(app, settings, pool)` bereit und registriert `GET /` (HTML), `GET /status` (JSON) und `/status-assets/*` (statische Dateien). Zwei Prozesse nutzen es:

| Hülle | Einstieg | Zweck |
| --- | --- | --- |
| Agenten-API | `create_app` in `src/api/app.py` | Dieselbe Seite neben den Ereigniseingängen; deren Endpunkte und Schutz bleiben unverändert. |
| Eigenständige App `platform-status` | `platform/infra/status-ui/files/serve.py` | Nur Statusseite und `/healthz` (reiner Prozesscheck), kein Ereigniseingang, kein Worker, kein Modell, kein GitHub-Zugang, kein Kubernetes-Client. |

Das Image ist dasselbe (`acr:operations-agent@sha256:…`). Der Chart ersetzt nur den Startbefehl und mountet `serve.py` per ConfigMap. Das Image enthält Katalog und Sollzustände (`OPS_PLATFORM_ROOT`); die Seite sieht daher den Katalog des gebauten Images, nicht den aktuellen Git-Stand.

Grund für die Trennung: Der vollständige Agenten-Rollout hängt an der Foundation-Ausgabe `githubAppPrivateKey` und lief zum Nachweiszeitpunkt nicht. Die Statusseite soll gerade dann erreichbar sein und den Agenten als gestört zeigen.

### 2. Datenquellen und feste Prüfziele (stabil)

- **Katalog:** Für jede App-ID mit gültigem Katalogeintrag und Sollzustand der Umgebung entsteht ein Dienst mit Name, Sollversion (`upstreamRelease`, sonst `chart.version`), Ziel `<scheme>://<app-id>.<OPS_BASE_DOMAIN>` und Prüfpfad `availability.path`. Prüfziele stammen ausschließlich aus Konfiguration und Katalog, nie aus Request-Daten. Die angezeigte Version ist die Sollversion, nicht die laufende.
- **HTTP-Prüfung:** `GET`, TLS geprüft, Redirects nicht verfolgt, Antwortkörper nicht gelesen, 4 Sekunden Timeout, alle Prüfungen parallel. Nur HTTP 200 gilt als erreichbar.
- **Operations-Datenbank:** lesende Transaktion (`SET TRANSACTION READ ONLY`), Statement-Timeout 2,5 Sekunden, Verbindungs-Timeout 3 Sekunden, auf die gewählte Umgebung beschränkt. Gelesen werden der jüngste Eintrag in `worker_heartbeats`, Zählungen aus `jobs` (aktiv; fehlgeschlagen oder `needs_attention` in 24 Stunden; `resolved` in 24 Stunden) und die sechs zuletzt aktualisierten Aufträge (nur App-ID, Zustand, Zeitpunkt).
- **Umgebung:** `OPS_STATUS_ENVIRONMENT` (`staging` oder `production`, Standard `staging`); das Chart übernimmt sie aus `platform.environment`.

### 3. Zustandssemantik (stabil)

Komponentenzustand: `healthy`, `unavailable`, `unknown`. Gesamtzustand: `degraded`, sobald irgendeine App, die Datenbank oder der Worker `unavailable` ist; sonst `unknown`, wenn etwas `unknown` ist oder der Katalog nicht vollständig lesbar ist; sonst `healthy`. Ein Datenbankfehler ergibt `unavailable` mit leeren Zahlen (`null`), niemals 0 Aufträge. Fehlt ein Heartbeat oder ist er älter als die Grenze (Standard 600 Sekunden, wie `/healthz`), ist der Worker `unavailable`. Ohne konfigurierte Datenbank sind Datenbank und Worker `unknown`; ohne gültige Basisdomain hat eine App kein Prüfziel und ist `unknown`. Fehlerdetails sind feste kurze Texte; rohe Fehlermeldungen verlassen den Prozess nicht, weil sie DSNs oder SQL enthalten können.

### 4. Aktualisierung (stabil, Werte konfigurierbar gedacht)

Der Server berechnet den Snapshot höchstens alle 10 Sekunden (Cache und Sperre im Prozess). Das JSON nennt `refreshAfterSeconds` (30) und `checkedAt`. Der Browser holt `/status` alle 30 Sekunden und beim Sichtbarwerden des Tabs, mit 12 Sekunden Timeout und `no-store`. Schlägt ein Abruf fehl oder ist der Stand älter als 90 Sekunden, markiert die Seite ihn als veraltet und zeigt den Gesamtzustand als unbekannt; der letzte Stand bleibt sichtbar. Ohne JavaScript verweist ein Hinweis auf `/status`.

### 5. Katalogeintrag und Ausrollen (stabil)

`platform/catalog/platform-status/app.yaml` hat keine Datenbausteine, `allowedActions: []`, Datenverfahren `not-applicable`, Prüfpfad `/` und einen Smoke-Test (`smoke-http`, `CHECK_PATH: /status`). Der Sollzustand `platform/environments/staging/platform-status.yaml` pinnt Image-Digest und Chart-Version. `app-deploy.yml` rollt über den normalen Pulumi-App-Pfad aus (Sperre, Plan, Smoke-Test). `status-ui-package.yml` lintet und veröffentlicht das Chart nach ACR (Version `0.1.<Laufnummer>`). Nach neuen Image-Builds muss der Digest im Sollzustand ausdrücklich aktualisiert werden.

### 6. Vorläufiger Inhalt (nicht verbindlich)

Stand 30.10.2026, vorläufig, wird inhaltlich überarbeitet. Die Seite zeigt auf Deutsch, mit lokal gehosteten Schriften und Design-Tokens aus der Unternehmenswebsite, ohne Drittanfragen:

- Kopf mit Umgebungsname, Gesamtzusammenfassung und Zeit der letzten Prüfung, Schaltfläche „Aktualisieren“.
- Vier Kennzahlen: erreichbare Dienste von gesamt, aktive Aufträge, abgeschlossene und aufmerksamkeitspflichtige Aufträge der letzten 24 Stunden.
- Liste „Anwendungen“ mit Name, Host, Sollversion, Zustand, Latenz oder Fehlertext und Link auf die App.
- Abschnitt „Betrieb“ mit Status-API, Operations-Datenbank und Agent-Worker (mit Heartbeat-Alter) sowie Tabelle „Letzte Aufträge“ (App, Zustand, Zeit).
- Fußzeile mit Aktualisierungshinweis und Link auf `/docs`.

Nichts davon ist Anforderung. Die Überarbeitung darf es ändern, solange die Requirements der Spec gelten.

## Risks / Trade-offs

Beim Nachziehen der Spec gefundene Abweichungen zwischen Anspruch und Ist-Stand; sie sind in [tasks.md](tasks.md) als offene Punkte geführt:

- **Schreibfähiger Datenbankzugang:** Der Statusdienst erhält über das Secret `POSTGRES_USER`/`POSTGRES_PASSWORD` den Administrator-Zugang (`foundation:postgresAdminPassword`). „Nur lesend“ stützt sich auf die lesende Transaktion und den Code, nicht auf eine Datenbankrolle. Eine eigene Leserolle fehlt.
- **Öffentlich ohne Anmeldung:** `/`, `/status` und `/status-assets` sind unauthentifiziert erreichbar (Seite trägt `noindex`). Die Projektion enthält App-IDs, Hosts, Sollversionen und Auftragszustände. Das ist für Staging so gebaut, aber nicht entschieden.
- **Mehr Endpunkte als beschrieben:** Beide Hüllen sind FastAPI-Apps mit Standard-Dokumentation. Die Fußzeile verlinkt `/docs`; `/docs` und `/openapi.json` antworten auf der eigenständigen App mit 200. Auf der Agenten-API macht `/docs` deren Endpunktbeschreibungen öffentlich lesbar. Das Runbook nennt `/docs` für den eigenständigen Dienst nicht.
- **Die Seite zeigt sich nicht selbst:** Der live ausgelieferte Snapshot listet nur AFFiNE und den Operations-Agent, nicht `platform-status`, weil das Image älter als der Katalogeintrag ist. Nach dem nächsten Image-Build prüft die Seite ihre eigene Adresse über den Ingress; das ist ein schwacher Selbsttest.
- **Gemeinsamer Ingress:** Fällt er aus, fehlt auch die Statusseite (kein unabhängiges Monitoring).
- **Alarmkette:** Der Katalogeintrag erzeugt wie jede App einen Verfügbarkeitstest samt Alarm an `ops-intake` (A). Nach A's Spec würde ein Alarm für `platform-status` einen Agentenauftrag ohne erlaubte Aktionen auslösen. Ob gewollt, ist nicht entschieden.
- **Server- und Client-Werte:** `refreshAfterSeconds` im JSON wird vom Browser nicht gelesen; die 30 Sekunden stehen fest in `status.js`.
