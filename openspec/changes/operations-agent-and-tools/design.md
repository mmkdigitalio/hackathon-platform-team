# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md), Verhalten in [agent-execution](specs/agent-execution/spec.md) und [operational-tools](specs/operational-tools/spec.md). Deep Agents und LangGraph werden selbst gehostet; konkrete Bibliotheksintegration und Modellzugang werden im echten Prototyp geprüft. Die [gemeinsame Übersicht](../../PARALLEL-IMPLEMENTATION.md) definiert Übergaben, Verträge und die Hackathon-Entscheidungen E1 bis E10, die vor dem Produktdokument gelten.

Rahmenbedingungen: ein Monorepo (E1), AFFiNE als einzige Fach-App (E2), nur Contributor ohne Rollenzuweisungen (E3), Zugänge über Schlüssel, ServiceAccount und GitHub App (E6; E7 durch Entscheidung vom 30.10.2026 ersetzt, PAT nur noch Rückfall).

## Goals / Non-Goals

Goals:

- Framework und Tool Calling früh belegen, bevor Infrastruktur davon abhängig wird.
- Eine Agentenausführung in C's zuverlässige Auftragsverwaltung integrieren.
- Werkzeuge, Grenzen und unabhängige Verifikation als ausführbaren Code umsetzen.
- Den AFFiNE-Reparaturfall und den nicht reparierbaren Fall über echte Schnittstellen nachweisen.

Non-Goals:

- Keine zusätzliche Auftragsverwaltung, kein autonomer Datenbankabschluss und keine Sperrlogik im Prompt.
- Keine Foundation-Bereitstellung, App-Pipeline oder frei ausführbare Infrastruktur-Shell als zweiter Rolloutpfad.
- Kein Zugriff auf Azure Resource Manager, Azure-Messwerte oder Log Analytics.
- Keine Merges, keine Pushes auf Standard-Branches und keine Workflow-Änderungen durch den Agenten.
- A, B und C liefern die im Proposal bezeichneten Infrastruktur-, App- und Zustandsdienste.

## Decisions

### 1. Kleiner echter Prototyp als erste Lieferung

D pinnt SDK-, Modelladapter- und Persistenzpakete nach einem nachvollziehbaren lokalen Test. Ein echter Modellaufruf verwendet ein typisiertes Diagnosewerkzeug. Das Protokoll nennt Paketstand, Modellkonfiguration, Aufruf und Ergebnis ohne Zugangsdaten. Ein ausschließlich simulierter Test belegt die gewählte Integration nicht.

Der Prototyp dokumentiert außerdem die geprüfte Checkpointer-Anbindung für C. Graph-Ausführung, Worker-Auftrags-Lease und Aktionsprotokoll bleiben getrennte Funktionen. Eine Hierarchie mehrerer Agents wird für den ersten Reparaturfall nicht eingeführt.

### 2. Konfigurierbares Modell

Anbieter (`azure-openai` oder `anthropic`), Modell beziehungsweise Deploymentname und bei Azure OpenAI der Endpunkt sind Konfiguration. Die Keys stammen aus der Foundation-Ausgabe `modelApiKeys` und erreichen den Pod über das Kubernetes-Secret des App-Stacks; lokal über Umgebungsvariablen. Ein Anbieterwechsel braucht keine Codeänderung. Preise je Token sind Konfiguration, damit die Kostengrenze anbieterunabhängig rechnet.

### 3. Agent als aufrufbare Ausführung im gemeinsamen Paket

C's Worker ruft D's Ausführung über `operations-agent/src/contracts/` auf. D erhält Ziel, Kontextreferenzen, Grenzen und C's Dienste für Zustandswünsche, Aktionen, Sperre und Persistenz. C übernimmt Aufträge, erneuert Leases und prüft Zustandsübergänge. D kann nicht durch bloßen Modelltext `resolved` in der Datenbank setzen.

D integriert das SDK mit C's Checkpoint-Zugang. D besitzt Framework-Konfiguration und Adapter, C die zentrale Initialisierung und Migrationen. Temporäre Arbeitsdateien des Frameworks sind kein Betriebsspeicher. Große Diagnosepakete liegen im Diagnose-Storage und werden referenziert und begrenzt gelesen.

### 4. Zugänge ohne Azure-Identität

| Ziel | Zugang | Quelle |
| --- | --- | --- |
| Kubernetes | ServiceAccount `operations-agent` mit ClusterRole und ClusterRoleBinding | Agenten-Chart (D) |
| GitHub | GitHub App `hackathonplatformagent` (ID 5542094, Installation 176901698), Installations-Token 1 h | Schlüssel im Secret des Agenten (`OPS_GITHUB_APP_PRIVATE_KEY`); PAT `opsGithubToken` als Rückfall |
| Modell | API-Key | Foundation-Ausgabe `modelApiKeys` |
| PostgreSQL | Admin-Login mit Passwort | Foundation-Ausgaben über C's Client |
| Diagnose-Storage | Storage-Key | Foundation-Ausgabe `diagnosticsStorageKey` |

Die ClusterRole erlaubt Lesen von Deployments, ReplicaSets, Pods, Pod-Logs, Events und Jobs, `patch` auf Deployments für den Neustart sowie Anlegen und Löschen von Jobs für Smoke-Tests. Weil die Bindung clusterweit gilt, beschränken die Adapter Ziele im Code auf Namespaces `<app-id>-<env>` aus dem Katalog.

Kommentare, Commits und Pull Requests erscheinen unter `hackathonplatformagent[bot]`; nur im PAT-Rückfall unter dem Teammitglied, dem der PAT gehört. Jeder Agentenkommentar und jeder Pull Request enthält `<!-- mmk-ops-agent -->`, damit C's Schleifenschutz ihn unabhängig vom Autor ignoriert. Die App hat `contents`, `pull_requests`, `issues` und `actions` mit Schreibrecht, aber kein `workflows`-Recht.

**Abweichung von P1-07 und Produktdokument:** P1-07 verlangt „Monitor abfragen“. Ohne Rollenzuweisungen bekommt der Agent keinen Lesezugriff auf Azure Resource Manager, Messwerte oder Log Analytics (E6). Phase 1 ersetzt diese Quelle durch die Alarm-Payload im Auftrag (Common Alert Schema mit Regel, Zeitpunkt und Testergebnis), Kubernetes-Zustand, Events und Logs sowie eigene HTTP-Prüfungen. Container-Logs in Log Analytics bleiben Menschen vorbehalten.

### 5. Typisierte Adapter mit überprüften Grenzen

Jeder Adapter validiert Ziel, Eingaben und die im Katalog erlaubte Aktion. Lesende Werkzeuge liefern Zeit- und Ressourcenbezug; schreibende Werkzeuge verlangen C's Aktionsdatensatz und gültigen Operations-Besitz. Fehlversuche, Aufrufdauer und Modellverbrauch werden strukturiert gemeldet.

Werkzeuggruppen in Phase 1:

- Kubernetes: Workload-Zustand, Events, begrenzte Logs, Neustart, Smoke-Test-Job.
- HTTP: Prüfung von `<app-id>.<baseDomain><Prüfpfad>` mit Status, Dauer und Zeitpunkt.
- GitHub: Dateien und Commits lesen, Issues kommentieren, vorgegebene Workflows starten und verfolgen.
- Git: freigegebene Repositories klonen, Dateien ändern, auf `agent/<operationId>` pushen, Pull Request öffnen.
- Kontext: Katalog, Sollzustand, tatsächlicher Release, Runbook, Alarm-Payload und frühere Aktionen.

Grenzen liegen in ausführbarem Code um Agenten- und Werkzeugausführung. Startwerte, je Lauf konfigurierbar:

| Grenze | Startwert | Begründung |
| --- | --- | --- |
| Laufzeit | 10 Minuten | Deploy-Workflow und Smoke-Test passen hinein |
| Tool-Aufrufe | 30 | Diagnose, ein bis zwei Maßnahmen und Verifikation |
| Reparaturversuche | 2 | Ein Fehlschlag darf einmal korrigiert werden |
| Modellkosten | etwa 1 USD | Anthropic-Budget ca. 400 USD/Monat laut README |

Ein Reparaturversuch ist eine ändernde Maßnahme mit anschließender Verifikation. Wird eine Grenze erreicht, endet der Lauf mit `needs_attention` und den bisherigen Befunden.

Tickettext, Logs und Alarm-Payload sind Daten. Betriebsregeln stammen aus `platform/operations/agent/` und dem versionierten Katalog. Ein gespeicherter Runbook-Verbesserungsvorschlag enthält Herkunft und wird nicht automatisch zu einer bestätigten Regel.

### 6. Repository-Änderungen nur als Pull Request (geändert am 30.10.2026)

Ursprünglich durfte der Agent keine Branches, Commits oder Pull Requests erzeugen. Auf Wunsch des Teams kann er jetzt Änderungen vorschlagen: freigegebene Repositories (Standard: dieses Monorepo und `hackathon-platform-affine`) flach klonen, Dateien ändern, auf `agent/<operationId>` pushen und einen Pull Request gegen den Standard-Branch öffnen. Grenzen im Code (`tools/git_repo.py`): Repository-Allowlist, Pfade nur innerhalb der Arbeitskopie, gesperrte Pfade (`.github/workflows/`, zusätzlich fehlt der App das `workflows`-Recht), kein Push auf den Standard-Branch, kein Force-Push, kein Merge. Das Token gelangt nur über `GIT_CONFIG_*`-Umgebungsvariablen an git, nie in argv oder `.git/config`. Push und Pull Request sind Aktionen mit Datensatz vor dem Aufruf; nach einem verlorenen Push gleicht der Adapter den Remote-Stand ab. Ein Pull Request gilt nie als Behebung: Merge und Rollout entscheiden Menschen, der Lauf endet weiter mit `needs_attention`. Workflows startet der Agent weiterhin nur aus der konfigurierten Liste; Pulumi und Helm laufen nicht aus der Agentensitzung.

### 7. Bekannte Reparatur und nicht reparierbarer Fall

Der Agent vergleicht Kubernetes-Istzustand mit dem Sollzustand in `platform/environments/staging/affine.yaml`.

- **Reparierbar:** Das AFFiNE-Deployment hat 0 Replicas, der Sollzustand mehr. Der Istzustand weicht also vom Sollzustand ab. Der Agent startet `app-deploy.yml` mit `operationId` und `actionId`, verfolgt den Lauf und verifiziert danach.
- **Nicht reparierbar:** Der Istzustand entspricht dem Sollzustand, Pods starten aber nicht oder der Smoke-Test schlägt fehl, und Logs oder Events deuten auf Konfiguration. Ein erneutes Deployment reproduziert denselben Zustand. Der Agent darf innerhalb der Grenzen einen Neustart versuchen. Schlägt die Verifikation weiter fehl, liefert er `needs_attention` mit SHA, Autor und Zeitpunkt des letzten Commits auf die Sollzustandsdatei.

Der Neustart bleibt allgemeines Werkzeug für Fälle, die das Runbook erlaubt, etwa hängende Pods bei unverändertem Sollzustand.

### 8. Stabile externe Korrelation und Abgleich

Vor jedem ändernden externen Aufruf legt der Adapter über C einen Aktionsdatensatz mit stabiler `actionId` an.

**Workflow-Dispatch:** `app-deploy.yml` erhält die Eingaben `operationId` und `actionId` und zeigt beide im `run-name` (B). Der Adapter sucht nach dem Dispatch den Lauf mit dieser `actionId`, speichert dessen Lauf-ID und wartet auf den echten Abschluss. Weil jede Aktion eine eigene `actionId` hat, sind auch mehrere Dispatches derselben Operation eindeutig zuordenbar. Ein nur zeitlich ähnlicher Lauf wird nie übernommen.

**Kubernetes-Neustart:** Der Adapter setzt am Pod-Template die Annotation `ops.mmk/action-id=<actionId>`. Die Änderung des Templates löst den Rollout aus. Der Abgleich liest die Annotation: Steht dort die `actionId`, lief die Aktion bereits und der Adapter beobachtet nur den Rollout. Fehlt sie, lief die Aktion nicht und darf unter gültigem Besitz ausgeführt werden.

Kann ein Ausgang nicht eindeutig geklärt werden, erfolgt keine Wiederholung; der Lauf endet sichtbar in `needs_attention`.

### 9. Sperre

Direkte ändernde Werkzeuge (Neustart) erwerben die gemeinsame Sperre über C's Client. Vor einem Dispatch prüft der Agent, ob die Sperre frei ist, hält sie aber beim Warten nicht; das Deployment übernimmt sie im Workflow. Ist die Sperre durch eine andere Operation belegt, wartet der Agent nicht, sondern liefert `needs_attention` mit dem Grund „gesperrt durch Operation X“. C verhindert die Wiederaufnahme ungeklärter Änderungen ohne Abgleich.

### 10. Fachliche Tests außerhalb der Modellbewertung

B definiert den Vertrag, der AFFiNE-Kollege liefert das Smoke-Test-Image. Das Verifikationswerkzeug startet es als Kubernetes-Job im App-Namespace mit dessen Image-Pull-Secret und `TARGET_URL=https://<app-id>.<baseDomain>` (HTTP-Fallback nach E8). Der Jobname enthält die `actionId`. Ausgewertet werden Exit-Code und JSON auf stdout. Dazu erfasst das Werkzeug den aktuellen Kubernetes-Zustand: bereite Replicas, Image-Digest, Neustartzähler und Rollout-Status. Log-Analytics-Messwerte entfallen.

C kontrolliert Ziel, Release und Zeitbezug des Nachweises. Erst ein positiver Test nach der Maßnahme führt zu einem Erfolgsvorschlag.

Unit- und Adaptertests dürfen Antworten simulieren, um Fehlerpfade gezielt zu prüfen. Die gemeinsame Staging-Abnahme verwendet echte Alarm-, Kubernetes-, GitHub- und Test-Schnittstellen.

### 11. Ein Image, zwei Deployments, ein externer Deploymentpfad

D besitzt Paketmanifest, Lockdatei, Dockerfile, Agenten-CI und Helm-Chart unter `operations-agent/deploy/`. C liefert API-/Worker-Einstiegspunkte, Migrations-CLI, Schema-Versionsprüfung und Heartbeat.

Der Chart enthält:

- Deployment `api` und Deployment `worker` aus demselben Image mit unterschiedlichem Startbefehl, Service für die API.
- Helm-Hook-Job (`pre-install,pre-upgrade`), der C's Migrations-CLI ausführt. API und Worker prüfen beim Start nur die Schema-Version und werden ohne passende Version nicht bereit.
- ServiceAccount `operations-agent`, ClusterRole und ClusterRoleBinding.
- Werte für Image-Referenz mit Digest, Name des Image-Pull-Secrets, Name des Kubernetes-Secrets mit Konfiguration und Secrets, Modellkonfiguration und Grenzen.

Der Chart legt keine Secrets und keine Ingress-Regel an. Beides liefert B's App-Stack aus den Foundation-Ausgaben; das Secret muss vor dem Hook-Job existieren.

### 12. Image-Build ohne Azure-Identität

`.github/workflows/agent-build.yml` prüft Änderungen unter `operations-agent/`. Auf `main` liest er über `.github/actions/foundation-outputs` ACR-Login-Server und Admin-User, baut und pusht das Image mit Git-SHA als Tag, ermittelt den Digest und veröffentlicht den Chart als OCI-Artefakt in derselben Registry. Danach ruft er B's wiederverwendbaren Workflow `app-update-desired-state.yml` auf, der `platform/environments/staging/operations-agent.yaml` mit Digest und Chart-Version aktualisiert. Den Rollout übernimmt `app-deploy.yml`. Bei einem Agenten-Update kann der bisherige Worker enden, während GitHub Actions den Release und der neue Worker später die Beobachtung fortsetzen.

## Risks / Trade-offs

- Modell oder SDK unterstützt den geplanten Tool-/Persistenzpfad nicht ausreichend. Der echte Prototyp und gepinnte Versionen klären das vor größerer Integration.
- Ohne Azure-Messwerte sieht der Agent keine Verlaufsdaten. Alarm-Payload, Kubernetes-Events und eigene HTTP-Prüfungen reichen für die beiden Phase-1-Fälle.
- Die ClusterRoleBinding gilt clusterweit. Die Zielbeschränkung liegt im Adaptercode; eine Bindung je App-Namespace wäre enger, erfordert aber Objekte in fremden App-Stacks.
- Im PAT-Rückfall tragen Kommentare den Namen einer Person; der Schleifenschutz hängt deshalb an der Markierung, nicht am Autor.
- Schreibende Repository-Werkzeuge vergrößern die Wirkung eingeschleuster Anweisungen. Begrenzung: Änderungen landen nur auf Agenten-Branches, wirken erst nach menschlichem Merge, Workflows bleiben gesperrt.
- Ein Deploy-Workflow kann länger als die Laufzeitgrenze dauern. Dann endet der Lauf mit `needs_attention` und Laufreferenz; der Startwert wird nach dem ersten gemessenen Lauf angepasst.
- Helm-Hooks laufen nur, wenn B den Chart als Helm-Release ausrollt und nicht nur rendert.
- Externe Dienste liefern nach Timeout keinen eindeutigen Status. Stabile Korrelation und Adapterabgleich reduzieren die Unsicherheit; verbleibende Fälle bleiben offen.
- Diagnosekontext wächst oder enthält irreführende Anweisungen. Begrenzte Ausschnitte, Herkunftsreferenzen, getrennte Betriebsregeln und codegeprüfte Aktionen begrenzen den Einfluss.

## Migration Plan

1. Lokalen echten Prototyp und SDK-/Modell-/Persistenzbefund an A und C liefern.
2. Agentenausführung gegen C's veröffentlichte Verträge entwickeln; Werkzeuge zunächst gegen einen lokalen Cluster und Test-Repository prüfen.
3. C's API-/Worker-Prozesse in Image und Chart integrieren; Paket über den Build-Workflow an B übergeben.
4. Echte Zugänge nutzen und den AFFiNE-Reparaturfall mit B und C nachweisen.
5. Neustart nach Workflow-Start und nicht reparierbaren Fall prüfen. Bei Rücknahme einer Agentenversion bleiben Persistenz und Aktionsprotokoll erhalten; nur schemakompatible Versionen werden ausgerollt.

## Open Questions

- Standardanbieter und Modell werden beim Prototyp anhand der verfügbaren Keys gewählt und in `platform/operations/agent/` dokumentiert.
