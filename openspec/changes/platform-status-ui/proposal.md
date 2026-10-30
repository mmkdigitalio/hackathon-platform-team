# Proposal

## Why

Die Staging-Plattform besteht aus mehreren Teilen (Apps, Operations-Datenbank, Agent-Worker), und ihr Gesamtzustand war bisher nur über einzelne Endpunkte und Alarme ablesbar. Eine lesende Statusübersicht „Plattformstatus“ wurde ohne Spec gebaut und nach Staging ausgerollt (Commits `d95d545` bis `fbe2a01`, 30.10.2026). Dieser Change holt die Specs nach: Er beschreibt, was existiert, und legt die stabilen Eigenschaften verbindlich fest, damit die geplante inhaltliche Überarbeitung der Oberfläche sie nicht versehentlich verletzt.

## What Changes

Dieser Change dokumentiert Vorhandenes. Er ändert keinen Code und kein Verhalten.

- Eine schreibgeschützte Statusseite (`GET /`) mit JSON-Projektion (`GET /status`) im bestehenden Python-Paket `operations-agent/src/api/` (`status.py`, `static/`). Sie liest den App-Katalog, prüft die Verfügbarkeitspfade der Apps per HTTP und liest aus der Operations-Datenbank Datenbankzustand, Worker-Heartbeat, Auftragszahlen und die letzten Aufträge.
- Die Statusseite läuft als eigenständige App `platform-status` mit eigenem Helm-Chart (`platform/infra/status-ui/`), eigenem Katalogeintrag und Sollzustand, aber mit demselben Container-Image wie der Operations-Agent. Ein fehlender Agentenzugang oder ein ausgefallener Worker verhindert ihre Bereitstellung deshalb nicht.
- Die Agenten-API (`create_app`) bindet dieselbe Statusseite weiterhin ein.
- Die Capability `platform-status` hält die stabilen Anforderungen fest: lesend, keine Geheimnisse, unabhängige Bereitstellung, Datenquellen, Zustandssemantik, Aktualisierung.
- **Die konkret angezeigten Inhalte und das Layout sind vorläufig und werden inhaltlich überarbeitet.** Sie stehen nur beschreibend im [Design](design.md) und im Requirement „Vorläufige Inhaltsdarstellung“, nicht als feste Anforderung.

### Abweichung vom Produktdokument

[PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md) §12 nimmt „ein eigenes Verwaltungsportal“ aus Phase 1 aus, und [PARALLEL-IMPLEMENTATION.md](../../PARALLEL-IMPLEMENTATION.md) wiederholt: „Phase 1 umfasst kein Portal“. Die Statusseite ist formal eine Abweichung davon. Sie ist ein Portal im Sinne einer Weboberfläche, aber kein Verwaltungsportal: Sie nimmt weder Ereignisse noch Eingaben an, löst keine Maßnahmen aus und ändert nichts. Die Abweichung ist nur als reine Leseansicht gedeckt. Jede schreibende Funktion (Aktionen, Freigaben, Auftragsdetails mit Texten, Anmeldung) bleibt Phase 3 und bräuchte einen eigenen Change. Der Change wurde nachträglich geschrieben; die Entscheidung für die Ansicht fiel vor der Spec und ist hier erstmals festgehalten.

## Capabilities

### New Capabilities

- `platform-status`: Schreibgeschützte, unabhängig bereitgestellte Statusansicht mit Zustandssemantik für Apps, Operations-Datenbank und Worker.

### Modified Capabilities

Keine. Es existieren keine archivierten Specs unter `openspec/specs/`. Berührungspunkte (kein Spec-Konflikt, aber Wechselwirkung):

- `app-catalog` ([B](../app-delivery-and-onboarding/specs/app-catalog/spec.md)): `platform-status` ist ein regulärer Katalogeintrag mit leeren `allowedActions` und `smokeTest.env.CHECK_PATH: /status`.
- `shared-platform-runtime` ([A](../platform-foundation/specs/shared-platform-runtime/spec.md)): Aus dem Katalogeintrag entsteht wie bei jeder App ein Verfügbarkeitstest samt Alarm an `ops-intake`.
- `durable-operations` ([C](../operations-intake-and-state/specs/durable-operations/spec.md)): Die Statusseite nutzt dieselbe Heartbeat-Grenze (`healthz_max_heartbeat_age_seconds`) wie `/healthz`.

## Impact

Bestehende Dateien (keine neuen Lieferungen durch diesen Change):

- `operations-agent/src/api/status.py`, `operations-agent/src/api/static/`, `operations-agent/scripts/preview-status.py`, `operations-agent/tests/state/test_status.py`
- `platform/infra/status-ui/` (Chart, `files/serve.py`), `.github/workflows/status-ui-package.yml`
- `platform/catalog/platform-status/app.yaml`, `platform/environments/staging/platform-status.yaml`
- `platform/operations/state/status-ui.md` (Runbook, enthält den Staging-Nachweis)

Dateizuständigkeit siehe Ergänzung in [PARALLEL-IMPLEMENTATION.md](../../PARALLEL-IMPLEMENTATION.md). Der Change ist keinem der vier Stränge zugeordnet, weil er nachträglich entstand; die Verzeichnisse behalten ihre bisherigen Besitzer.

### Out of scope

- Die inhaltliche und gestalterische Überarbeitung der Oberfläche. Sie bekommt einen eigenen Folge-Change oder ein Delta dieses Changes, sobald Inhalte feststehen.
- Authentifizierung oder Zugriffsbeschränkung der Statusseite (heute öffentlich ohne Anmeldung, siehe [Design](design.md)).
- Historische Verfügbarkeit (Uptime), Verlaufsdiagramme, Benachrichtigungen, funktionale Prüfungen und jede schreibende Funktion.
- Ein unabhängiges externes Monitoring: Bei Ausfall des gemeinsamen Ingress ist auch die Statusseite nicht erreichbar.
- Auftragsdetails, Vorfalltexte und Laufnachweise; diese bleiben `GET /runs/{auftragsId}` der Agenten-API (C) vorbehalten.
