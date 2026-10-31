# Parallele Umsetzung von Phase 1

Stand: 31. Oktober 2026 (Entscheidungen E3, E6 und die Mitarbeiterkanal-Notiz nachgeführt; geändert 31.10.2026 (Cornelius); war: Stand 30. Oktober 2026). Dies sind geplante Lieferverträge, keine Behauptung bereits implementierter Schnittstellen. Maßgeblich für den tatsächlichen Stand sind der Code im Repository, die Runbooks unter `platform/operations/` und die Nachweise in den Change-Ordnern.

Maßgeblich ist [PRODUKT-UND-IMPLEMENTIERUNG.md](../PRODUKT-UND-IMPLEMENTIERUNG.md). Die ältere [README](../README.md) beschreibt teilweise einen überholten Stack. Diese Planung verwendet AKS, Pulumi, GitHub Actions und einen eigenen Deep-Agent-Service. Wo die folgenden Hackathon-Entscheidungen vom Produktdokument abweichen, gelten die Entscheidungen.

## Hackathon-Entscheidungen

| Nr. | Entscheidung | Folge |
| --- | --- | --- |
| E1 | **Ein Monorepo** `mmkdigitalio/hackathon-platform-team` mit `platform/` und `operations-agent/`; Workflows unter `.github/` im Root. | Keine repositoryübergreifenden PRs, eine Repo-Secret-Sammlung, eine Release-Referenz (Git-SHA). |
| E2 | **AFFiNE ist die einzige Fach-App.** Sie kommt aus `mmkdigitalio/hackathon-platform-affine`; ein Kollege befüllt dieses Repo und liefert Chart/Werte, Smoke-Test und Runbook. Eine eigene Demo-App gibt es nicht. | Die AFFiNE-Paketierung ist externe Lieferung und nicht Teil der Specs. Die kontrollierten Fehler sind infrastrukturseitig und gefährden keine Daten (siehe Verifikation). |
| E3 | **Contributor-Rechte** genügen für Bootstrap und Foundation-Rollout; keine Entra-App-Registrierungen. Rollenzuweisungen sind die Ausnahme: Cornelius hat auf der Subscription Hackathon die Rolle Owner und kann sie anlegen; genutzt wird das nur für die eine Leseidentität des Agenten-Workspace (siehe E6). GitHub: Teammitglieder können Repositories anlegen und Repo-Secrets setzen. (geändert 31.10.2026 (Cornelius); war: Nur Contributor-Rechte auf der Subscription, keine Rollenzuweisungen, keine Entra-App-Registrierungen.) | Siehe E4 bis E6. Wer nur Contributor hat, lässt `agentWorkspaceIdentity` aus (Standard); der Foundation-Stack bleibt dann im Contributor-Modus lauffähig. |
| E4 | **Der Foundation-Stack wird lokal** von einer Person mit Contributor (`az login`) ausgerollt. GitHub Actions haben keine Azure-Identität. | Bootstrap und Wiederherstellung laufen über ein dokumentiertes lokales Verfahren statt aus GitHub Actions (Abweichung von Produktdokument §4). |
| E5 | **Alle Azure-Ressourcen gehören zum Foundation-Stack**, auch app-bezogene Verfügbarkeitstests und Alarme; sie werden aus den Katalogdateien im Repo erzeugt. App-Stacks verwalten nur Kubernetes-Objekte und Datenbankobjekte über das PostgreSQL-Protokoll. | App-Deployments in CI brauchen nur Pulumi-State-Key, Passphrase und die Foundation-Ausgaben. |
| E6 | **Zugänge über Schlüssel statt Rollen:** Admin-Kubeconfig, ACR-Admin-User, PostgreSQL-Passwort, Storage-Keys. Der Agent nutzt im Cluster einen ServiceAccount, für GitHub eine GitHub App (geändert 2026-10-30 (Cornelius); war: PAT). Einzige Ausnahme: Der Subscription-Owner hat eine **schreibgeschützte Azure-Identität** für den Agenten-Workspace angelegt (Reader auf `rg-mmk-platform-staging` und `rg-mmk-platform-staging-aks-nodes`, Log Analytics Reader); sie ist in der Foundation optional (`agentWorkspaceIdentity`, Standard aus; Entscheidung D14 in `platform/operations/foundation/decisions.md`). (geändert 31.10.2026 (Cornelius); war: Ausschließlich Schlüssel statt Rollen für alle Zugänge.) | Der Agent hat je Operation einen Workspace-Pod mit Shell, git, gh, kubectl, helm und az; alle Zugänge dort sind lesend, Änderungen laufen nur über protokollierte Werkzeuge (siehe [agent-workspace](changes/operations-agent-and-tools/specs/agent-workspace/spec.md)). Diagnose darf damit auch Azure-Ressourcen, Messwerte und Log Analytics lesen, sofern die Identität in der Foundation aktiviert ist; ohne sie bleibt es bei Alarm-Payload, Kubernetes-Zustand, Events, Logs und HTTP-Prüfungen. (geändert 31.10.2026 (Cornelius); war: Der Agent liest in Phase 1 keine Azure-Ressourcen, Messwerte oder Log Analytics.) |
| E7 | **GitHub-Identität des Agenten ist eine GitHub App** (Issues, Contents, Pull Requests, Actions für beide Repos); ein PAT bleibt nur Rückfall (geändert 2026-10-30 (Cornelius); war: Fine-grained PAT eines Teammitglieds). | Kommentare erscheinen unter der App. Schleifenschutz erkennt Agentenkommentare an der Markierung `<!-- mmk-ops-agent -->`, nicht am Autor. |
| E8 | **Keine eigenen Domains.** Hosts heißen `<app-id>.<ingress-ip-mit-bindestrichen>.sslip.io`; die Foundation gibt die Basisdomain aus. | HTTPS per Let's Encrypt; schlägt die Ausstellung fehl, ist HTTP über dieselben Hosts dokumentierter Fallback für Staging. |
| E9 | **Operations-Sperre in PostgreSQL mit Break-glass.** Der Foundation-Stack nimmt keine Sperre. Ist PostgreSQL nicht erreichbar, darf ein App-Deployment mit `--force` ohne Sperre laufen; das wird in der Workflow-Zusammenfassung vermerkt und nach Wiederherstellung ins Aktionsprotokoll nachgetragen. | Recovery ist nicht durch die eigene Datenbank blockiert. |
| E10 | **Agentengesundheit über einen Endpunkt.** `/healthz` antwortet 503, wenn die Datenbank nicht erreichbar oder der letzte Worker-Heartbeat (Zeile in PostgreSQL) älter als 10 Minuten ist. Ein Verfügbarkeitstest darauf alarmiert Menschen per E-Mail. | Kein Log-Search-Alarm und keine Abhängigkeit von Container Insights für die Ausfallmeldung. |
| E11 | **Meldungen von Mitarbeitenden:** Der GitHub-Issue-Webhook ist zurückgestellt. Als erster Mitarbeiterkanal ist ein Chat in der Plattform-UI geplant; dafür folgt ein eigener Change, der hier noch nicht beschrieben ist. Endpunkt `/events/github` und Issue-Form `.github/ISSUE_TEMPLATE/support.yml` bleiben im Code, werden aber nicht als Eingangskanal betrieben. (neu 31.10.2026 (Cornelius); war: GitHub-Issue als Meldekanal der Mitarbeitenden, Szenario 3.) | Szenario 3 (Support-Issue mit Antwort) läuft vorerst nur als Test gegen den Endpunkt. Der Ereignisvertrag unten behält die GitHub-Quelle, bis der Chat-Change sie ersetzt oder ergänzt. |

## Die vier Stränge und der Nachtrag

| Strang | Change | Lieferung | Lokal unabhängig starten |
| --- | --- | --- | --- |
| A | [platform-foundation](changes/platform-foundation/proposal.md) | Monorepo-Gerüst, Konventionen, Bootstrap, State, gemeinsame Azure-Infrastruktur, Verfügbarkeitstests und Alarmziele | Konventionen, Ausgabevertrag, Foundation-Code und Bootstrap vorbereiten |
| B | [app-delivery-and-onboarding](changes/app-delivery-and-onboarding/proposal.md) | Katalog, App-Stacks, Release-Pipelines, AFFiNE-Integration, Fehlerszenarien und Agenten-Deployment | Katalogvalidierung, App-Modul gegen lokalen Cluster, Fehlerszenarien |
| C | [operations-intake-and-state](changes/operations-intake-and-state/proposal.md) | API, Aufträge, Leases, Zustandsregeln, Persistenz, Heartbeat und gemeinsame Operations-Sperre | Lokales PostgreSQL, Beispiel-Webhooks und vertragskonforme Testausführung |
| D | [operations-agent-and-tools](changes/operations-agent-and-tools/proposal.md) | Framework-Nachweis, Agent, Kubernetes- und GitHub-Werkzeuge, externe Abgleiche und Agentenpaket | Echtes Modell mit typisiertem Werkzeug und lokale Adaptertests |

Nachtrag, keinem Strang zugeordnet: [platform-status-ui](changes/platform-status-ui/proposal.md) beschreibt die schreibgeschützte Statusansicht „Plattformstatus“ (Dashboard, Anwendungsliste, App-Details, Aktivitäten), die zuerst ohne Spec nach Staging ausgerollt und danach nachträglich spezifiziert wurde. Seit dem 30.10.2026 sind Spec und Umsetzung abgeglichen (Nachweise in der `tasks.md` des Changes); der Stand ist ein Staging-Stand, kein Produktionsnachweis. (geändert 31.10.2026 (Cornelius); war: Ihr Inhalt ist vorläufig und wird überarbeitet.) Sie ist eine Leseansicht und kein Verwaltungsportal.

Jeder Change enthält `proposal.md`, `design.md`, Capability-Specs und `tasks.md`. Mit `openspec instructions apply --change <name>` beziehungsweise `/opsx:apply <name>` wird der gewünschte Strang ausgewählt.

## Dateizuständigkeit im Monorepo

| Bereich | Zuständig | Grenze |
| --- | --- | --- |
| Root: `.gitignore`, `README.md`, `.github/CODEOWNERS` | A | A integriert gemeinsame Root-Änderungen. |
| `platform/infra/foundation/`, `platform/infra/components/`, `platform/scripts/bootstrap/`, `platform/operations/foundation/` | A | Alle Azure-Ressourcen; keine Kubernetes-Objekte von Apps |
| `platform/schemas/conventions/`, `platform/schemas/foundation-outputs/`, `.github/actions/foundation-outputs/`, `.github/workflows/foundation-*.yml` | A | Konventionen, Ausgabevertrag und Zugangscheck |
| `platform/infra/app-runtime/`, `platform/schemas/app-catalog/`, `platform/catalog/`, `platform/environments/`, `platform/operations/apps/`, `.github/workflows/app-*.yml` | B | App-Stacks und Releases; `platform/catalog/affine/` befüllt der AFFiNE-Kollege nach B's Schema |
| `platform/schemas/events/`, `platform/operations/state/`, `.github/ISSUE_TEMPLATE/`, `.github/workflows/health-check.yml`, `.github/workflows/ops-migrate.yml` | C | Ereignisverträge, Wartungseingang und Erstinitialisierung der Datenbank |
| `platform/operations/agent/` | D | Plattformweite Agenten-Betriebsregeln |
| `operations-agent/src/contracts/`, `src/api/`, `src/persistence/`, `src/worker/` | C | Einstiegspunkte, Besitz, Zustände, Migrationen, Heartbeat |
| `operations-agent/src/agent/`, `src/tools/`, `deploy/`, `toolbox/`, `Dockerfile`, Paketmanifest und Lockdatei, `.github/workflows/agent-*.yml` | D | Agentenausführung, Adapter, Workspace-Toolbox-Image, Image und Chart; C meldet Abhängigkeiten an D. `toolbox/` ergänzt am 31.10.2026 (Workspace-Pod). |
| `operations-agent/tests/` | C und D in getrennten Unterverzeichnissen | Gemeinsame Szenarien mit benannter Federführung |
| `operations-agent/src/api/status.py`, `src/api/status_activity.py`, `src/api/static/`, `operations-agent/scripts/preview-status.py`, `platform/operations/state/status-ui.md` | C (liegt in C's Verzeichnissen; `scripts/preview-status.py` fällt in `.github/CODEOWNERS` unter `/operations-agent/` und damit an D, Abgleich offen) | Statusansicht, siehe [platform-status-ui](changes/platform-status-ui/proposal.md); nur lesend. `status_activity.py` ergänzt am 31.10.2026 (geändert 31.10.2026 (Cornelius); war: nur `status.py`, `static/` und Vorschauskript) |
| `platform/infra/status-ui/`, `.github/workflows/status-ui-package.yml`, `platform/catalog/platform-status/`, `platform/environments/*/platform-status.yaml` | B (App-Paket, Katalog, Sollzustand); `.github/CODEOWNERS` enthält für Chart-Pfad und Package-Workflow keine eigene Zeile (greift `*`, also A), Abgleich offen | Eigenständiges Chart (`Chart.yaml`, `values.yaml`, `files/serve.py`, `templates/app.yaml`) und Katalogeintrag der Statusansicht; Chart-Pfad und Package-Workflow lagen vorher in keiner Zeile der Tabelle |
| `mmkdigitalio/hackathon-platform-affine` | AFFiNE-Kollege | Chart/Werte, Smoke-Test-Image, Runbook; außerhalb dieser Specs |

Jeder Strang arbeitet in einem eigenen Branch. Eine Änderung an einem fremden Bereich wird dem zuständigen Strang übergeben.

## Repo-Secrets

| Secret | Inhalt | Gesetzt durch |
| --- | --- | --- |
| `PULUMI_BACKEND_KEY`, `PULUMI_BACKEND_ACCOUNT` | Zugriff auf das State-Backend | A nach dem Bootstrap |
| `PULUMI_CONFIG_PASSPHRASE` | Gemeinsame Passphrase aller Stacks | A |
| `OPS_GITHUB_TOKEN` | Optional: PAT als Rückfall, solange der App-Schlüssel `githubAppPrivateKey` fehlt (geändert 2026-10-30 (Cornelius); war: Pflicht-PAT aus E7) | Ein Teammitglied |

Alle übrigen Werte (Kubeconfig, ACR-Zugang, Datenbank, Webhook-Keys) liest ein Workflow über die Composite Action `foundation-outputs` aus dem State.

## Gemeinsame Verträge v0.1

Der zuständige Strang veröffentlicht seinen Vertrag früh in seinem Bereich. Brechende Änderungen erfordern eine neue Vertragsversion.

### Foundation-Ausgaben, A liefert an B, C und D

Maßgeblich ist die Tabelle im Requirement „Versionierter Foundation-Ausgabevertrag“ in [shared-platform-runtime](changes/platform-foundation/specs/shared-platform-runtime/spec.md). Namen sind camelCase, Versionsfeld ist `contractVersion`. Geheime Ausgaben sind Pulumi-Secrets und werden nur maskiert an Workflows oder als Kubernetes-Secret an Pods weitergegeben.

### App-Katalog, B liefert an A, C und D

- `platform/catalog/<app-id>/app.yaml`, umgebungsunabhängig: `schemaVersion`, `appId`, Herkunft und Repository, Chart-Quelle, unterstützte Datenbausteine, Secret-Referenzen, Verfügbarkeitsprüfung (Pfad), Smoke-Test-Image, Runbook, erlaubte Aktionen, Datenverfahren.
- `platform/environments/<env>/<app-id>.yaml`, Sollzustand: Image-Digest, Chart-Version, Konfigurationswerte, Größen.
- Namespace und Host werden aus App-ID und Umgebung abgeleitet und nicht eingetragen.

A erzeugt aus `app.yaml` je App und Umgebung einen Verfügbarkeitstest mit Alarm an `ops-intake`; ausgenommen ist `operations-agent`, der nur über `/healthz` an `ops-humans` überwacht wird. C validiert App-/Umgebungszuordnung. D lädt Release, erlaubte Aktionen und Betriebswissen.

### Ereignisse, C liefert an A und die Eingangsquellen

`schemaVersion`, `source`, `deliveryId`, `externalRef`, `appId`, `environment`, `eventType`, `severity`, `state`, `occurredAt`, `receivedAt` sowie Referenz auf die Quelldaten.

- **Azure Monitor:** Alarmregeln heißen `alert-<app-id>-<env>-<check>` und tragen `app-id` und `environment` als Custom Properties der Aktion. C liest zuerst die Custom Properties, sonst den Regelnamen.
- **GitHub-Issue:** Eine Issue-Form mit Dropdowns für App-ID und Umgebung; C liest die gerenderten Abschnitte `### App` und `### Umgebung`. Kommentare mit `<!-- mmk-ops-agent -->` werden ignoriert. **Zurückgestellt (E11):** Der Webhook wird nicht als Eingangskanal betrieben; Endpunkt und Issue-Form bleiben im Code. Ein Chat in der Plattform-UI ist als erster Mitarbeiterkanal geplant (eigener Change folgt). (geändert 31.10.2026 (Cornelius); war: GitHub-Issue als vorgesehener Meldekanal ohne Vorbehalt.)
- **Wartung:** Header `X-Api-Key` mit `maintenanceApiKey`.

Fired und Resolved bleiben unterscheidbar. Deduplizierung erfolgt über `source` und `deliveryId`. Quellenübergreifende Vorfallkorrelation ist nicht Teil von Phase 1.

### Agentenausführung, C und D verwenden denselben Vertrag

C besitzt den Vertrag unter `operations-agent/src/contracts/` und liefert Auftrag, Vorfall, Ausführung, App/Umgebung, Kontextreferenzen, gültigen Besitz, Budget und Wiederaufnahmeinformationen. C's Worker ruft genau eine Agentenausführung aus D auf und erneuert seinen Auftragsbesitz. D liefert strukturierte Befunde, Aktionsreferenzen, Verbrauchsdaten, Verifikationsnachweise und einen Ergebnisvorschlag. C prüft und persistiert Zustandsübergänge.

### Aktionen und Sperre, C liefert an B und D

Eine Operation besitzt stabile `operationId`, App/Umgebung, Besitzerreferenz, Status und externe Referenzen. Einzelne Aktionen haben eine eigene `actionId`. Sperrbesitz und Auftrags-Lease sind unterschiedliche Dinge.

- C liefert Bibliothek und CLI für Erwerb, Erneuerung, Besitzprüfung, Freigabe und Aktionsnachweise gegen PostgreSQL. Runner nutzen sie mit dem Passwort aus den Foundation-Ausgaben, ohne laufende Agenten-API.
- **Konfliktregel:** Ein Workflow wartet bis zu 15 Minuten auf die Sperre und bricht dann ab. Der Agent wartet nicht, sondern beendet den Auftrag mit `needs_attention` und dem Grund „gesperrt durch Operation X“.
- **Break-glass (E9):** `--force` nur, wenn PostgreSQL nicht erreichbar ist; Vermerk in der Workflow-Zusammenfassung und Nachtrag ins Aktionsprotokoll.
- D protokolliert einen Workflow-Dispatch vor dem Aufruf mit stabiler Korrelation und hält beim Warten keine Ziel-Sperre. Das Deployment übernimmt die Sperre im Workflow.
- Ein abgelaufener Besitz mit ungeklärter externer Änderung wird nicht blind neu vergeben.

### Workflows, B liefert an D

- `app-plan.yml`: Plan-Prüfung für Pull Requests, erforderlicher Check für `main`.
- `app-deploy.yml`: Push auf `main` unter `platform/environments/**` und `workflow_dispatch` mit `appId`, `environment`, `operationId`, `actionId`, `force`. Der `run-name` enthält App, Umgebung, `operationId` und `actionId`; der Agent ordnet Läufe über die `actionId` zu.
- `app-update-desired-state.yml`: wiederverwendbar per `workflow_call`, schreibt den Sollzustand mit einem Installations-Token der GitHub App, ersatzweise `OPS_GITHUB_TOKEN` (geändert 2026-10-30 (Cornelius); war: `OPS_GITHUB_TOKEN`); D's `agent-build.yml` ruft ihn auf.

### Migrationen und Gesundheit, C liefert an D

- Vor dem ersten App-Deployment führt C's Workflow `ops-migrate.yml` die Migrations-CLI einmal aus, weil die Sperre ihr Schema braucht. Gleichzeitige Migrationen serialisiert eine Advisory-Lock.
- Das Agenten-Chart führt C's Migrations-CLI als Helm-Hook-Job (`pre-install,pre-upgrade`) aus. API und Worker prüfen beim Start nur die Schema-Version.
- Der Worker schreibt mindestens jede Minute einen Heartbeat in PostgreSQL. `/healthz` der API wertet ihn nach E10 aus.

### Verifikation, B definiert und D führt aus

- Ein Smoke-Test ist ein Container-Image, das als Kubernetes-Job mit `TARGET_URL` läuft, mit Exit-Code endet und ein JSON-Ergebnis auf stdout schreibt.
- Ein Ergebnis enthält `appId`, `environment`, tatsächlichen Release, `checkRef`, Ausführungszeitraum, Erfolg/Fehlschlag und Evidenzreferenzen. D ergänzt den aktuellen Kubernetes-Zustand. C prüft beim Abschluss, dass die Nachweise nach der Maßnahme entstanden sind und zum Zielstand passen.
- **Reparierbarer Fehler:** Das AFFiNE-Deployment wird auf 0 Replicas skaliert. Bekannte Reparatur: Sollzustand über den App-Deploy-Workflow wiederherstellen.
- **Nicht reparierbarer Fehler:** Ein Commit setzt einen ungültigen AFFiNE-Konfigurationswert im Sollzustand. Neustart und erneutes Deployment reproduzieren den Fehler; erwartetes Ergebnis ist `needs_attention` mit Verweis auf den Commit.

## Start und Integration

1. A legt Konventionen fest und veröffentlicht Ausgabe-Schema v0 mit Platzhalterwerten; B und C veröffentlichen Katalog- und Ereignisbeispiele; C und D fixieren Aufruf- und Aktionsverträge.
2. Alle vier Stränge beginnen lokal. D liefert P1-02 mit echtem Modell früh. A bootstrapt und rollt die Foundation lokal aus.
3. A liefert Foundation und erreichbare Datenbank. C liefert Migrationen, führt `ops-migrate.yml` einmal aus und liefert den standalone Operations-Client. B kann damit AFFiNE und das erste Agentenpaket ausrollen, auch wenn die Agenten-API noch nicht läuft.
4. D liefert das Paket mit C's API-/Worker-Einstiegspunkten. B integriert Digest, Chart und Konfiguration im Katalog. A erzeugt die Verfügbarkeitstests aus dem Katalog.
5. Gemeinsame Abnahme: AFFiNE-Fehler, Alarm oder Issue, Diagnose, Reparatur, unabhängiger Test und sichtbares Ergebnis. Danach die Widerstandsfähigkeitsszenarien.

Die Integration wartet auf konkrete Lieferungen, nicht auf den vollständigen Abschluss eines anderen Changes. Lokale Tests dürfen Adapter ersetzen; der Plattformnachweis benötigt echte Schnittstellen.

## Federführung für die zehn Abnahmeszenarien

Die Nummern entsprechen Abschnitt 14 des Produktplans. Wegen E2 ist die „zweite App“ in Szenario 2 der Betriebsagent selbst.

| Nr. | Szenario | Federführung | Beiträge |
| --- | --- | --- | --- |
| 1 | Reproduzierbare Bereitstellung | A | B: App-Deployments; C: Migration/Sperrclient; D: Paket |
| 2 | AFFiNE und Agent laufen gleichzeitig über denselben App-Pfad | B | A: Laufzeit; AFFiNE-Kollege: Paket |
| 3 | Support-Issue mit Antwort | C | D: Issue-Werkzeug; B: gültige App-Zuordnung. Kanal zurückgestellt (E11): vorerst Test gegen den Endpunkt, später Chat. |
| 4 | Echte Störung autonom beheben | D | A: Verfügbarkeitstest und Alarm; B: Fehlerszenario und Deploy-Workflow; C: Auftrag und Zustandsregeln |
| 5 | Keine Scheinlösung | D | B: nicht reparierbarer Fall; C: Abschlussprüfung |
| 6 | Doppelte Zustellung | C | D: gezählte Aktionsaufrufe |
| 7 | Neustart nach Workflow-Start | C | D: externen Lauf wiederfinden; B: korrelierbarer Workflow |
| 8 | Deployment und Agent konkurrieren | C | B und D: denselben Client verwenden; A: Erreichbarkeit |
| 9 | AFFiNE-Daten nach Neustart erhalten | B | AFFiNE-Kollege: Smoke-Test; A: Datenbausteine |
| 10 | Durchgängige Nachvollziehbarkeit | C | A, B und D: Ressourcen-, Release-, Tool- und Testnachweise |

Jeder Strang dokumentiert seine Start-, Stopp-, Wiederanlauf- und Fehlerverfahren. Phase 1 umfasst kein Verwaltungsportal (die schreibgeschützte Statusansicht ist die einzige Ausnahme, siehe [platform-status-ui](changes/platform-status-ui/proposal.md)), keine beliebige Softwareaufnahme, keine automatisch übernommene Codekorrektur (der Agent darf Branch und Pull Request vorschlagen, merged aber nie) und keine unbeaufsichtigte Wiederherstellung von Produktionsdaten. Azure-Diagnose durch den Agenten ist nur lesend über die optionale Workspace-Identität möglich (E6). (geändert 31.10.2026 (Cornelius); war: „… keine automatische Codekorrektur, keine Azure-Diagnose durch den Agenten und keine unbeaufsichtigte Wiederherstellung von Produktionsdaten.“)
