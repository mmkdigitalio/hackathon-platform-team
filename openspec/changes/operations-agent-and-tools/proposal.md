# Proposal

## Why

Die Plattform benötigt einen selbst gehosteten Betriebsagenten, der echte Systemzustände untersucht und eine bekannte AFFiNE-Störung nachweisbar behebt. Strang D prüft das Deep-Agents-Framework früh und entwickelt Agent und Werkzeuge unabhängig von Cloud-Bootstrap und Ereignisannahme.

## What Changes

- Strang D übernimmt P1-02 und P1-07, die Agentenpaketierung aus P1-08 und die Diagnose-/Reparaturseite von P1-10.
- Ein lokaler Prototyp mit gepinnten Paketversionen verwendet ein echtes Tool-Calling-Modell und ein typisiertes Diagnosewerkzeug. Anbieter (Azure OpenAI oder Anthropic) und Modell sind konfigurierbar; Keys kommen aus der Foundation-Ausgabe `modelApiKeys`.
- Die Agentenausführung konsumiert C's Auftrags-, Zustands-, Sperr- und Checkpoint-Verträge sowie B's Katalog und das AFFiNE-Runbook.
- Typisierte Kubernetes-, HTTP-, GitHub-, Kontext- und Verifikationswerkzeuge führen begrenzte Maßnahmen aus und gleichen unklare externe Ergebnisse ab.
- Zugänge ohne Azure-Identität (E6, E7): Kubernetes über den ServiceAccount `operations-agent` mit ClusterRoleBinding aus dem Agenten-Chart, GitHub über die GitHub App `hackathonplatformagent` (Installations-Token), der PAT aus `opsGithubToken` nur als Rückfall. Agentenkommentare tragen die Markierung `<!-- mmk-ops-agent -->`. Keine Workload Identity.
- **Abweichung von P1-07:** Der Agent liest keine Azure-Ressourcen, Messwerte oder Log-Analytics-Daten, weil nur Contributor ohne Rollenzuweisungen vorhanden ist (E3, E6). „Monitor abfragen“ wird ersetzt durch Auswertung der Alarm-Payload, Kubernetes-Zustand, Events und Logs sowie eigene HTTP-Prüfungen.
- Bekannte Reparatur: Ist das AFFiNE-Deployment auf 0 Replicas skaliert, stellt der Agent den Sollzustand per Dispatch von `app-deploy.yml` (Eingaben `operationId` und `actionId`) wieder her und verfolgt den Lauf. Ein Smoke-Test als Kubernetes-Job und der aktuelle Kubernetes-Zustand belegen den Erfolg. Der Neustart bleibt allgemeines Werkzeug.
- Nicht reparierbarer Fall: Ein ungültiger AFFiNE-Konfigurationswert im Sollzustand führt zu `needs_attention` mit Verweis auf den letzten Sollzustands-Commit.
- Der Agent kann Repository-Änderungen vorschlagen: klonen, ändern, auf `agent/<operationId>` pushen, Pull Request öffnen. Er merged nie, pusht nie auf Standard-Branches und ändert keine Workflows (Änderung vom 30.10.2026). Workflows startet er nur aus einer vorgegebenen Liste.
- Codebasierte Grenzen mit konfigurierbaren Startwerten: 10 Minuten Laufzeit, 30 Tool-Aufrufe, 2 Reparaturversuche und etwa 1 USD Modellkosten pro Lauf.
- Ein Image und ein Helm-Chart starten C's API und Worker als getrennte Deployments; ein Helm-Hook-Job führt C's Migrations-CLI aus.
- Der Build-Workflow pusht das Image mit dem ACR-Admin-User und ruft B's wiederverwendbaren Workflow `app-update-desired-state.yml` auf.

## Capabilities

### New Capabilities

- `agent-execution`: Konfigurierbare Deep-Agent-Ausführung mit Kontext, Grenzen, Wiederaufnahme, verifizierten Ergebnissen und Agentenpaket.
- `operational-tools`: Typisierte Integrationen, nachvollziehbare Maßnahmen und Abgleich externer Ausführungen.

### Modified Capabilities

Keine. Es existieren noch keine implementierten Agenten-Capabilities.

## Impact

Monorepo `mmkdigitalio/hackathon-platform-team` (E1). Zielbereiche von D:

| Pfad | Inhalt |
| --- | --- |
| `operations-agent/src/agent/` | Agentenausführung, Modellkonfiguration, Grenzen |
| `operations-agent/src/tools/` | Kubernetes-, HTTP-, GitHub-, Kontext- und Verifikationsadapter |
| `operations-agent/deploy/` | Helm-Chart für API, Worker, Migrations-Hook und RBAC |
| `operations-agent/Dockerfile`, Paketmanifest und Lockdatei unter `operations-agent/` | Gemeinsames Image |
| `.github/workflows/agent-*.yml` | Prüfung, Image-Build und Übergabe an den Sollzustand |
| `platform/operations/agent/` | Agenten-Betriebsregeln |
| D's Unterverzeichnis in `operations-agent/tests/` | Agenten- und Adaptertests |

D besitzt die SDK-Integration, C die Persistenzinitialisierung. C meldet Abhängigkeiten an D, damit Manifest und Lockdatei nicht parallel auseinanderlaufen.

Grundlage sind [PRODUKT-UND-IMPLEMENTIERUNG.md](../../../PRODUKT-UND-IMPLEMENTIERUNG.md), Abschnitte 6, 10 und 12 bis 14, und die [gemeinsame Arbeitsaufteilung](../../PARALLEL-IMPLEMENTATION.md). Bei Abweichungen gelten deren Hackathon-Entscheidungen E1 bis E10.

### Out of scope und Lieferungen anderer Stränge

| Strang | Dessen Lieferung | Nutzung durch D |
| --- | --- | --- |
| [A: Grundplattform](../platform-foundation/proposal.md) | Cluster, ACR mit Admin-User, PostgreSQL, Diagnose-Storage, Foundation-Ausgaben einschließlich `modelApiKeys` und `opsGithubToken`, Composite Action `foundation-outputs`, Verfügbarkeitstests und Alarme | D nutzt Ausgaben und Composite Action, legt aber keine Azure-Ressourcen oder Azure-Identitäten an. |
| [B: App-Bereitstellung](../app-delivery-and-onboarding/proposal.md) | Katalog, Sollzustand, `app-deploy.yml` mit Eingaben `operationId` und `actionId`, wiederverwendbarer Workflow `app-update-desired-state.yml`, Fehlerszenarien, App-Stack des Agenten mit Kubernetes-Secret, Image-Pull-Secret und Ingress-Regel | D startet den Deploy-Workflow, liefert Image und Chart und ruft den Sollzustands-Workflow auf. D implementiert weder App-Modul noch Sollzustand. |
| [C: Ereignisse und Zustand](../operations-intake-and-state/proposal.md) | Verträge, API, Worker, Zustandsmaschine, Migrations-CLI, Checkpoint-Zugang, Aktionsprotokoll, Operations-Sperre, Heartbeat und Schleifenschutz | D implementiert den aufrufbaren Agenten und liefert strukturierte Ergebnisse. D schreibt keine zweite Queue und keine eigenen Zustands- oder Sperrtabellen. |
| AFFiNE-Kollege (`hackathon-platform-affine`) | AFFiNE-Chart und -Werte, Smoke-Test-Image, Runbook | D führt den Smoke-Test aus und liest das Runbook. |

Nicht Teil des Changes: Mergen von Pull Requests, Pushes auf Standard-Branches, automatische Codekorrekturen, Azure-Diagnose durch den Agenten, Workload Identity, mehrere spezialisierte Agents, eine Chatoberfläche, Managed Deep Agents und unbeaufsichtigte irreversible Datenoperationen.
