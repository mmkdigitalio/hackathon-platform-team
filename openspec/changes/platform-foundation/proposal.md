# Proposal

## Why

Die Phase-1-Plattform benötigt eine reproduzierbare Azure-Grundlage, bevor Anwendungen und Betriebsagent integriert werden können. Ein eigener Infrastrukturstrang ermöglicht diese Arbeit parallel zur lokalen Entwicklung der anderen drei Stränge. Für den Hackathon stehen nur Contributor-Rechte auf der Subscription zur Verfügung; die Foundation muss deshalb ohne Rollenzuweisungen auskommen.

## What Changes

- Strang A übernimmt P1-01 für Monorepo-Gerüst und Konventionen sowie P1-03 und P1-04 für Bootstrap und Grundplattform.
- Verbindliche Konventionen für App-IDs, abgeleitete Ressourcennamen und Release-Referenzen.
- State-Backend, AKS, ACR, Ingress mit HTTPS über sslip.io, Log Analytics, Application Insights, Diagnoseartefakt-Speicher und ein gemeinsamer PostgreSQL-Server werden bereitgestellt.
- Zugänge laufen über Schlüssel statt Rollen: Admin-Kubeconfig, ACR-Admin-User, Datenbankpasswort, Storage-Keys und ein PAT. Der Foundation-Stack wird lokal mit Contributor ausgerollt; GitHub Actions brauchen keine Azure-Identität.
- Ein versionierter Ausgabevertrag verbindet die Infrastruktur mit App-Stacks, Workflows und Agentenkonfiguration.
- Alle Azure-Ressourcen liegen im Foundation-Stack, auch die aus dem Katalog erzeugten Verfügbarkeitstests und Alarmregeln. Die Action Group `ops-intake` leitet App-Alarme an den Ereigniseingang; der Ausfall des Agenten wird per E-Mail gemeldet.
- Secrets und Netzzugang sind für Staging bewusst einfach gehalten: Pulumi-Ausgaben und Repo-Secrets statt Key Vault, öffentliche Endpunkte mit Passwort bzw. API-Key statt privater Netze.
- Budget-Alarm und dokumentiertes Stoppen für Staging. Bootstrap, Wiederanlauf und Foundation-Release funktionieren ohne laufenden Agenten.

## Capabilities

### New Capabilities

- `platform-bootstrap`: Monorepo-Gerüst, Konventionen, reproduzierbarer Bootstrap, Zugänge ohne Rollenzuweisungen, State und unabhängiger Foundation-Release.
- `shared-platform-runtime`: Gemeinsame Laufzeit, Daten- und Monitoringdienste einschließlich verbindlicher Ausgaben für Verbraucher.

### Modified Capabilities

Keine. Es existieren noch keine implementierten Plattform-Capabilities.

## Impact

Zielbereiche im Monorepo `mmkdigitalio/hackathon-platform-team` sind `platform/infra/foundation/`, `platform/infra/components/`, `platform/scripts/bootstrap/`, `platform/schemas/conventions/`, `platform/schemas/foundation-outputs/`, `platform/operations/foundation/`, `.github/actions/foundation-outputs/` und `.github/workflows/foundation-*.yml`. A integriert gemeinsame Root-Dateien.

Maßgeblich sind [PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md), insbesondere Abschnitte 3 bis 5, 9 und 12 bis 14, und die Hackathon-Entscheidungen in der [gemeinsamen Arbeitsaufteilung](../../PARALLEL-IMPLEMENTATION.md). Abweichend vom Produktdokument laufen Bootstrap und Wiederherstellung lokal statt aus GitHub Actions, und der Agent erhält keinen Azure-Lesezugriff.

### Out of scope und Lieferungen anderer Stränge

| Strang | Dessen Lieferung | Nutzung durch A |
| --- | --- | --- |
| [B: App-Bereitstellung](../app-delivery-and-onboarding/proposal.md) | Katalogschema, App-Modul, App-Stacks, Release-Pipelines, AFFiNE-Integration und Agenten-Katalogeintrag | A liest die Katalogeinträge für Verfügbarkeitstests, deployt aber keine Apps und besitzt keine Kubernetes-Objekte von Apps. |
| [C: Ereignisse und Zustand](../operations-intake-and-state/proposal.md) | Webhook-Empfänger, `/healthz`, Datenbankmigrationen, Auftragsverwaltung und Sperrclient | A liefert Datenbank, Zugang und Netzpfad. A implementiert weder Tabellen noch Auftrags- oder Sperrlogik und nimmt selbst keine Sperre. |
| [D: Agent und Werkzeuge](../operations-agent-and-tools/proposal.md) | Deep-Agent-Laufzeit, Integrationen, Agenten-Image und Helm-Paket mit API- und Worker-Prozess | A stellt Zugänge und Infrastruktur bereit, besitzt aber weder Prompts noch Tools oder das Anwendungspaket. |

Die AFFiNE-Paketierung aus `mmkdigitalio/hackathon-platform-affine`, Produktionsbetrieb, Multi-Region, feingranulare Rechte, Key Vault und ein Verwaltungsportal gehören nicht zu diesem Change.
