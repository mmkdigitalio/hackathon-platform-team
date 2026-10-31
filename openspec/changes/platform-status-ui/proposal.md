# Proposal

## Why

Die erste Statusseite beantwortet die Erreichbarkeitsfrage, unterstützt aber weder die regelmäßige Bestandsprüfung noch die Analyse von Überwachung, Systemaktivitäten und behobenen Problemen. Administratoren brauchen einen kurzen Tagescheck und eigene Analysewege. Die User Stories US-1 bis US-6 und Journeys J1 bis J5 stehen in [user-journeys.md](user-journeys.md).

## What Changes

- Dashboard, Anwendungsbestand, Anwendungsdetails und Aktivitäten erhalten getrennte URLs und Aufgaben.
- Monitoring-Konfiguration, Live-Prüfungen und empfangene Signale werden als unterschiedliche Nachweise dargestellt.
- Der Verlauf kombiniert strukturierte Erkennungen, Agentenaktionen, Systemaktionen und Abschlüsse aus der Operations-Datenbank.
- Behebungen gelten nur mit erfolgreicher jüngster Verifikation als verifiziert.
- Such- und Filterzustände sind teilbare URL-Parameter und überstehen Aktualisierungen.
- Das MMK-Design, die unabhängige Bereitstellung und der lesende Charakter bleiben erhalten.

## Stand

Stand 31. Oktober 2026: Die vier Seiten (`/`, `/applications`, `/applications/<app-id>`, `/activity`) sind umgesetzt, in Staging ausgerollt und mit der Spec abgeglichen (Nachweise in [tasks.md](tasks.md)). Der frühere Vorbehalt „Inhalt vorläufig“ gilt nur noch für drei Punkte: Die Statusansicht ist in Staging ohne Anmeldung erreichbar, die Monitoring-Parameter der Foundation-IaC sind fest im Code hinterlegt (siehe [design.md](design.md), Abschnitt Datenquellen), und ein geplanter Chat für Mitarbeitende (eigener, noch nicht geschriebener Change) berührt die Aussage „ausschließlich lesend“. (geändert 31.10.2026 (Cornelius); war: „Inhalt vorläufig und wird überarbeitet“ im Nachtrag von PARALLEL-IMPLEMENTATION.md.)

## Capabilities

### New Capabilities

- `platform-status`: Lesender Tagescheck und Admin-Analyse mit nachprüfbarer Zustandssemantik.

### Modified Capabilities

Keine schreibende Capability. Die bestehenden Agenten-Endpunkte und ihre Authentifizierung bleiben unverändert. Die Statusansicht ist die bereits eingeführte lesende Ausnahme zum Ausschluss eines Verwaltungsportals in Phase 1.

## Impact

Betroffen sind `operations-agent/src/api/status.py` und `status_activity.py`, die statischen Assets unter `src/api/static/`, die Projektionstests `operations-agent/tests/state/test_status.py` und `test_status_admin.py`, das eigenständige Status-Chart `platform/infra/status-ui/`, Katalogeintrag und Sollzustand von `platform-status` sowie das Status-Runbook `platform/operations/state/status-ui.md`. Keine Datenbankmigration, kein neuer Modell- oder Kubernetes-Zugang und keine Betriebsaktionen. Die öffentliche Staging-Projektion bleibt auf feste Metadaten beschränkt; eine Anmeldung und die Veröffentlichung freier Vorfalltexte sind nicht Teil dieses Changes.
