# Proposal

## Why

Apps brauchen einen gemeinsamen, überprüfbaren Weg vom versionierten Sollzustand zur laufenden und fachlich geprüften Anwendung. Diesen Weg nutzen in Phase 1 genau zwei Apps: AFFiNE als einzige Fach-App und der Betriebsagent selbst. Weil GitHub Actions keine Azure-Identität haben, darf der App-Pfad nur Kubernetes- und PostgreSQL-Zugänge aus den Foundation-Ausgaben benötigen.

## What Changes

- Strang B übernimmt P1-05 (ohne Demo-App) und P1-11 sowie die App-Integration aus P1-08 und die Fehler- und Verifikationsseite von P1-10.
- Katalogschema v0.1 mit zwei Dateien je App: `platform/catalog/<app-id>/app.yaml` umgebungsunabhängig, `platform/environments/<env>/<app-id>.yaml` als Sollzustand. Namespace und Host werden nach Konvention abgeleitet.
- Unterstützte Datenbausteine v0.1: `postgres-database`, `redis`, `persistent-volume`. Secret-Referenzen `foundation:<ausgabeName>` und `app:<name>`.
- Ein Pulumi-App-Programm unter `platform/infra/app-runtime/` mit einem Stack je App und Umgebung, ausschließlich mit Kubernetes- und PostgreSQL-Objekten. Verfügbarkeitstests und Alarme erzeugt der Foundation-Stack aus dem Prüfpfad in `app.yaml`.
- Workflows `.github/workflows/app-*.yml` für Plan-Prüfung, Deployment und Sollzustandsänderung per Pull Request mit `OPS_GITHUB_TOKEN` und Auto-Merge. Der Deploy-Workflow nutzt C's Operations-Client mit Konfliktregel und Break-glass.
- Smoke-Test-Vertrag für fachliche Prüfungen und ein einheitliches Verifikationsergebnis.
- AFFiNE-Integration über den App-Pfad. Die AFFiNE-Paketierung (Chart, Werte, Smoke-Test-Image, Runbook) liefert ein Kollege aus `mmkdigitalio/hackathon-platform-affine`; dieser Change legt nur fest, was diese Lieferung enthalten muss.
- Zwei datensichere Fehlerszenarien mit AFFiNE: reparierbar (0 Replicas) und nicht reparierbar (ungültiger Konfigurationswert im Sollzustand).
- Das von D gebaute Agentenpaket wird als App `operations-agent` über denselben Pfad ausgerollt.

## Capabilities

### New Capabilities

- `app-catalog`: Versionierter, validierter App-Vertrag mit Sollzustand, Datenbausteinen, Secret-Referenzen, Prüfpfad und Smoke-Test-Vertrag.
- `app-delivery`: App-Stacks, Plan-, Deploy- und Sollzustands-Workflows mit gemeinsamer Sperre und fachlich geprüftem Ergebnis.
- `app-onboarding`: Aufnahme von AFFiNE und Betriebsagent, Fehlerszenarien und Nachweis der Abnahmeszenarien 2 und 9.

### Modified Capabilities

Keine. Es existieren noch keine implementierten App-Capabilities.

## Impact

Zielbereiche im Monorepo `mmkdigitalio/hackathon-platform-team` sind `platform/infra/app-runtime/`, `platform/schemas/app-catalog/`, `platform/catalog/`, `platform/environments/`, `platform/operations/apps/` und `.github/workflows/app-*.yml`. `platform/catalog/affine/` befüllt der AFFiNE-Kollege nach B's Schema. Ein eigenes Demo-App-Repository gibt es nicht. B verändert keine Foundation-Module und keine Azure-Ressourcen.

Maßgeblich sind die Hackathon-Entscheidungen und Verträge in der [gemeinsamen Arbeitsaufteilung](../../PARALLEL-IMPLEMENTATION.md), danach [PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md), Abschnitte 5 und 8 bis 14. Abweichend vom Produktdokument entfällt die Demo-App (E2), und App-Stacks enthalten keine Azure-Ressourcen (E5).

### Out of scope und Lieferungen anderer Stränge

| Strang | Dessen Lieferung | Nutzung durch B |
| --- | --- | --- |
| [A: Grundplattform](../platform-foundation/proposal.md) | Konventionen, Foundation-Ausgaben, Composite Action `foundation-outputs`, gemeinsamer PostgreSQL-Server, Ingress mit sslip.io, Verfügbarkeitstests und Alarme aus `app.yaml` | B konsumiert Ausgaben, liefert den Prüfpfad im Katalog und legt keine Azure-Ressourcen an. |
| [C: Ereignisse und Zustand](../operations-intake-and-state/proposal.md) | Migrationen, standalone Operations-Client (Sperre, Aktionsprotokoll), Validierung von App/Umgebung | B verwendet den Client im Deploy-Workflow. B baut keine eigene Sperre. |
| [D: Agent und Werkzeuge](../operations-agent-and-tools/proposal.md) | Agenten-Image und -Chart mit API, Worker, Migrations-Hook, ServiceAccount; Agenten-Build-Workflow | B deployt das Paket und stellt den wiederverwendbaren Sollzustands-Workflow bereit. B ändert weder Agentenlogik noch Chart. |
| AFFiNE-Kollege, `mmkdigitalio/hackathon-platform-affine` | Chart und Werte, Smoke-Test-Image, Runbook, Katalogeintrag nach Schema | B integriert die Lieferung über den App-Pfad und spezifiziert nur deren Schnittstellen. |

Beliebige Software automatisch aufzunehmen, AFFiNE zu forken, eine Demo-App, Azure-Ressourcen in App-Stacks, autonome Wiederherstellung von Produktionsdaten und unbeaufsichtigte irreversible Migrationen gehören nicht zum Change.
