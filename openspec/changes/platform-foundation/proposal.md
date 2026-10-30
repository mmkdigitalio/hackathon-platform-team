# Proposal

## Why

Die Phase-1-Plattform benötigt eine reproduzierbare Azure-Grundlage, bevor Anwendungen und Betriebsagent integriert werden können. Ein eigener Infrastrukturstrang ermöglicht diese Arbeit parallel zur lokalen Entwicklung der anderen drei Stränge.

## What Changes

- Strang A übernimmt P1-01 für Repository-Grundgerüste und Konventionen sowie P1-03 und P1-04 für Bootstrap und Grundplattform.
- State-Backend, Identitäten, GitHub-OIDC, AKS, ACR, Netzwerk, HTTPS, Monitoring, Diagnoseartefakt-Speicher und externes Agenten-PostgreSQL werden bereitgestellt.
- Verbindliche Konventionen für App-IDs, abgeleitete Ressourcennamen und Release-Referenzen; Wildcard-DNS und Zertifikate für `<app-id>.<baseDomain>`; Budget-Alarm und dokumentiertes Stoppen für Staging.
- Versionierte Foundation-Ausgaben verbinden die Infrastruktur mit App-Stacks und Agentenkonfiguration.
- Eine Action Group leitet App-Alarme an den von C gelieferten Eingang weiter; die Alarmregeln selbst liegen in B's App-Stacks. Der Ausfall des Agenten wird separat per E-Mail gemeldet.
- Secrets und Netzzugang sind für Staging bewusst einfach gehalten: Pulumi-Ausgaben und GitHub-Secrets statt Key Vault, öffentliche Endpunkte mit Passwort bzw. API-Key statt privater Netze.
- Bootstrap, Wiederanlauf und Foundation-Deployment funktionieren ohne laufenden Agenten.

## Capabilities

### New Capabilities

- `platform-bootstrap`: Reproduzierbarer Bootstrap, Identitäten, State und unabhängiger Foundation-Release.
- `shared-platform-runtime`: Gemeinsame Laufzeit, Daten- und Monitoringdienste einschließlich verbindlicher Ausgaben für Verbraucher.

### Modified Capabilities

Keine. Es existieren noch keine implementierten Plattform-Capabilities.

## Impact

Zielbereiche sind `mmk-platform/infra/foundation/`, gemeinsame Module in `infra/components/`, Foundation-Workflows und `operations/foundation/`. A koordiniert initiale Repository-Gerüste und gemeinsame Root-Konfiguration. Die geplanten Repositories werden in diesem Proposal nur beschrieben, noch nicht angelegt.

Maßgeblich ist [PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md), insbesondere Abschnitte 3 bis 5, 9 und 12 bis 14. Die ältere README entscheidet keinen abweichenden Stack. Die [gemeinsame Arbeitsaufteilung](../../PARALLEL-IMPLEMENTATION.md) benennt Dateibesitz und Übergaben.

### Out of scope und Lieferungen anderer Stränge

| Strang | Dessen Lieferung | Nutzung durch A |
| --- | --- | --- |
| [B: App-Bereitstellung](../app-delivery-and-onboarding/proposal.md) | Katalogschema, App-Modul, App-Stacks, Release-Pipelines, Demo-App, AFFiNE und Agenten-Katalogeintrag | A exportiert Infrastrukturreferenzen, deployt aber keine Apps und besitzt keine App-Ressourcen. |
| [C: Ereignisse und Zustand](../operations-intake-and-state/proposal.md) | Webhook-Empfänger, Datenbankmigrationen, Auftragsverwaltung und gemeinsam nutzbarer Sperrclient | A liefert Datenbank, Zugang und Netzpfad. A implementiert weder Tabellen noch Auftrags- oder Sperrlogik. |
| [D: Agent und Werkzeuge](../operations-agent-and-tools/proposal.md) | Deep-Agent-Laufzeit, Integrationen, Agenten-Image und Helm-Paket mit API- und Worker-Prozess | A stellt Identitäten und Infrastruktur bereit, besitzt aber weder Prompts noch Tools oder das Anwendungspaket. |

Produktionsbetrieb, Multi-Region, feingranulare Mandantentrennung und ein Verwaltungsportal gehören nicht zu diesem Change.
