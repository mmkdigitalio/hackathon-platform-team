# Proposal

## Why

Eingehende Meldungen und laufende Betriebsmaßnahmen müssen Prozessausfälle, doppelte Zustellungen und konkurrierende Änderungen überstehen. Strang C liefert die deterministische Auftrags- und Zustandsverwaltung als gemeinsame Grundlage für Agent und Deployments.

## What Changes

- Strang C übernimmt P1-06 und den Eingangsanteil von P1-09 sowie Persistenz-, Wiederanlauf- und Kollisionsschutzanteile aus P1-08, P1-10 und P1-12.
- FastAPI nimmt Azure-Monitor-Alarme (`POST /events/azure-monitor?key=<alertWebhookKey>`), GitHub-Issue-Ereignisse (signierter Repo-Webhook) und Wartungsaufträge (Header `X-Api-Key`) an, normalisiert sie und persistiert sie vor der Bestätigung.
- Deduplizierung über `source` und `deliveryId`. Fired/Resolved derselben Alarmreferenz und Kommentare desselben Issues gehören zum selben Vorfall. Quellenübergreifende Vorfallkorrelation ist nicht Teil von Phase 1.
- PostgreSQL speichert Aufträge, Vorfälle, Leases, Ausführungen, Aktionen, Worker-Heartbeat und die von D benötigten Checkpoints. C liefert eine Migrations-CLI, die D's Chart als Helm-Hook-Job ausführt.
- Ein Worker übernimmt Aufträge, schreibt mindestens jede Minute einen Heartbeat und ruft D's Agentenausführung über einen definierten Vertrag auf. `GET /healthz` wertet Datenbank und Heartbeat aus (E10).
- Eine gemeinsame Operations-Sperre mit Bibliothek und eigenständiger CLI koordiniert App-Änderungen durch Agent und Pipeline, mit fester Konfliktregel und Break-glass `--force` nach E9. Sie hängt nicht von der laufenden Agenten-API ab.
- Laufnachweise sind je Auftrag als JSON ohne Secrets abrufbar.
- Gezielte Tests belegen Duplikaterkennung, Wiederanlauf, Zustandsregeln und Kollisionsschutz.

## Capabilities

### New Capabilities

- `event-intake`: Authentifizierte, normalisierte und gegen Mehrfachzustellung abgesicherte Ereignisannahme.
- `durable-operations`: Persistente Auftragsausführung, Zustandsübergänge, Aktionsnachweise, Heartbeat, Migrationen und gemeinsame Operations-Sperre.

### Modified Capabilities

Keine. Es existieren noch keine implementierten Betriebs-Capabilities.

## Impact

Monorepo `mmkdigitalio/hackathon-platform-team` (E1). C besitzt:

- `operations-agent/src/contracts/`, `src/api/`, `src/persistence/`, `src/worker/` und `operations-agent/tests/` im eigenen Unterverzeichnis,
- `platform/schemas/events/` (Ereignisvertrag und Beispiele),
- `platform/operations/state/` (Betriebsdokumentation zu Eingang, Aufträgen, Sperre und Wiederanlauf),
- `.github/ISSUE_TEMPLATE/` (Support-Issue-Form),
- `.github/workflows/health-check.yml` (geplanter Gesundheitscheck).

C liefert Prozess-Einstiegspunkte, Migrations-CLI, Schema-Versionsprüfung, Heartbeat und `/healthz` an D für die gemeinsame Paketierung und meldet benötigte Abhängigkeiten an D.

Grundlage sind [PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md), Abschnitte 6, 7 und 12 bis 14, und die [gemeinsame Arbeitsaufteilung](../../PARALLEL-IMPLEMENTATION.md). Bei Abweichungen gelten deren Hackathon-Entscheidungen E1 bis E10.

### Out of scope und Lieferungen anderer Stränge

| Strang | Dessen Lieferung | Nutzung durch C |
| --- | --- | --- |
| [A: Grundplattform](../platform-foundation/proposal.md) | PostgreSQL-Server mit Datenbank `operations`, Foundation-Ausgaben (`postgresAdminPassword`, `alertWebhookKey`, `maintenanceApiKey`, `githubWebhookSecret`), Composite Action `foundation-outputs`, Action Group `ops-intake`, Verfügbarkeitstest auf `/healthz` | C implementiert Datenmodell, Eingänge und `/healthz`, erstellt aber weder Datenbankserver noch Cluster oder Monitor-Ressourcen. |
| [B: App-Bereitstellung](../app-delivery-and-onboarding/proposal.md) | Katalog, App-Zuordnung, App-Deploy-Workflows, AFFiNE-Integration und fachliche Tests | C validiert App-/Umgebungsreferenzen gegen den Katalog und liefert den Sperrclient. C schreibt keine App-Deployment-Pipeline. |
| [D: Agent und Werkzeuge](../operations-agent-and-tools/proposal.md) | Agentenausführung, SDK-Anbindung, Kubernetes- und GitHub-Werkzeuge, externe Statusabgleiche, Verifikationsergebnisse, Image und Chart | C erzwingt Zustands- und Persistenzregeln. C implementiert weder Diagnose noch Werkzeugadapter. |

Nicht Teil des Changes: quellenübergreifende Vorfallkorrelation, eine zusätzliche Messaging-Plattform, ein Supportportal, neue Meldekanäle, eine eigene Agentenhierarchie und das Einrichten des GitHub-Webhooks. Das erledigt ein Repo-Admin manuell; C dokumentiert URL, Ereignistypen und Secret in `platform/operations/state/`.
