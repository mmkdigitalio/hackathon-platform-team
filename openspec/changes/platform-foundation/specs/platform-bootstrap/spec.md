# Spec Delta

## Purpose

Die Plattform lässt sich aus dokumentierten Voraussetzungen und versioniertem Code reproduzierbar initialisieren und unabhängig vom Betriebsagenten wieder bereitstellen, auch mit reinen Contributor-Rechten.

## ADDED Requirements

### Requirement: Monorepo-Grundgerüst
Die Plattform SHALL im Repository `mmkdigitalio/hackathon-platform-team` mit den Bereichen `platform/` und `operations-agent/` sowie Workflows unter `.github/` starten. Das Grundgerüst SHALL die Bereiche gemäß `openspec/PARALLEL-IMPLEMENTATION.md` den Verantwortlichen zuordnen, sodass parallele Arbeit ohne spätere Umstrukturierung möglich ist.

#### Scenario: Paralleler Implementierungsstart
- **WHEN** die Implementierung beginnt
- **THEN** existieren `platform/` und `operations-agent/` mit ihren Unterverzeichnissen und eine `CODEOWNERS`-Datei, die sie den Verantwortlichen zuordnet
- **AND** ist `staging` als erste Umgebung unter `platform/environments/` angelegt

### Requirement: Verbindliche Bezeichner
App-IDs SHALL dem Muster `^[a-z][a-z0-9-]{1,30}$` folgen. Umgebungen SHALL `staging` und später `production` heißen. Eine Release-Referenz SHALL aus Git-SHA, Image-Digest und Chart-Version bestehen. Die Konventionen SHALL maschinenlesbar unter `platform/schemas/conventions/` liegen.

#### Scenario: Ungültige App-ID
- **WHEN** ein Katalogeintrag oder Workflow die App-ID `Demo_App` verwendet
- **THEN** lehnt die Konventionsprüfung sie vor jeder Änderung ab

### Requirement: Ableitbare Ressourcennamen
Aus App-ID und Umgebung SHALL sich alle Namen eindeutig ableiten lassen: Pulumi-Stack `organization/app-<app-id>/<env>`, Namespace `<app-id>-<env>`, Host `<app-id>.<baseDomain>` und Alarmregel `alert-<app-id>-<env>-<check>`. Der Foundation-Stack SHALL `organization/foundation/<env>` heißen, seine Resource Group `rg-mmk-platform-<env>`. Alle Azure-Ressourcen SHALL die Tags `app-id`, `environment`, `stack` und `managed-by` tragen.

#### Scenario: Namen für eine App ableiten
- **WHEN** ein App-Stack für die App-ID `affine` in `staging` angelegt wird
- **THEN** heißen Stack, Namespace und Host `organization/app-affine/staging`, `affine-staging` und `affine.<baseDomain>`
- **AND** ist keine Absprache über weitere Namen nötig

### Requirement: Wiederholbarer State-Bootstrap
Die Plattform SHALL ein vom App-Lebenszyklus unabhängiges Pulumi-State-Backend in einem eigenen Azure-Storage-Account besitzen. Ein idempotentes Azure-CLI-Skript unter `platform/scripts/bootstrap/` SHALL Resource Group, Storage-Account und State-Container nur anlegen, wenn sie fehlen, und mit Contributor-Rechten auskommen. Eine Wiederholung SHALL bestehende Ressourcen übernehmen, ohne State oder Passphrase zu ersetzen.

#### Scenario: Bootstrap erneut ausführen
- **WHEN** der dokumentierte Bootstrap in einer bereits initialisierten Staging-Umgebung erneut ausgeführt wird
- **THEN** bleiben vorhandene State-Daten und die Passphrase unverändert
- **AND** werden die nachfolgenden Stacks mit denselben Backend-Referenzen ausführbar

#### Scenario: Abgebrochener erster Bootstrap
- **WHEN** der erste Bootstrap nach dem Anlegen des Storage-Accounts abbricht
- **THEN** vervollständigt eine Wiederholung die fehlenden Ressourcen ohne manuelle Aufräumarbeiten

### Requirement: Gemeinsamer Secrets-Provider
Alle Stacks der Plattform SHALL dasselbe Backend und denselben Passphrase-Secrets-Provider verwenden, damit Stack-Referenzen auch geheime Ausgaben lesen können.

#### Scenario: Geheime Ausgabe referenzieren
- **WHEN** ein App-Stack per Stack-Referenz das PostgreSQL-Passwort der Foundation liest
- **THEN** erhält er den entschlüsselten Wert ohne zusätzliche Schlüsselkonfiguration

### Requirement: Geschützter State
Der State-Container SHALL Blob-Versionierung und Soft Delete mit mindestens 30 Tagen Aufbewahrung verwenden. Gleichzeitige Läufe desselben Stacks SHALL durch das Locking des Pulumi-Backends verhindert werden.

#### Scenario: State versehentlich überschrieben
- **WHEN** die State-Datei des Foundation-Stacks beschädigt oder überschrieben wird
- **THEN** lässt sich die vorherige Version über die Blob-Versionierung nach dokumentiertem Verfahren wiederherstellen

#### Scenario: Zwei Foundation-Läufe gleichzeitig
- **WHEN** eine zweite Person den Foundation-Stack ausrollt, während ein Lauf aktiv ist
- **THEN** bricht der zweite Lauf mit einem Lock-Hinweis ab, ohne den State zu verändern

### Requirement: Einfache Secret-Verwaltung für Phase 1
Phase 1 SHALL Secrets mit geringem Aufwand verwalten und keine separate Secret-Infrastruktur voraussetzen. Zugangsdaten SHALL nicht in Git und nicht in Prompts liegen. Folgende Ablageorte sind für Staging ausreichend:

| Secret | Ablage | Erzeugt durch |
| --- | --- | --- |
| Storage-Account und -Key des State-Backends, Pulumi-Passphrase | Repo-Secrets `PULUMI_BACKEND_ACCOUNT`, `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` | Bootstrap-Skript bzw. einmalig manuell |
| Admin-Kubeconfig, ACR-Admin-Zugang, Storage-Key des Diagnose-Speichers | Geheime Foundation-Ausgaben | Foundation-Stack (von Azure abgerufen) |
| PostgreSQL-Admin-Passwort, `alertWebhookKey`, `maintenanceApiKey` | Geheime Foundation-Ausgaben | Foundation-Stack (zufällig generiert) |
| Modell-Keys, `githubWebhookSecret`, PAT des Agenten | Geheime Foundation-Konfiguration, als geheime Ausgabe weitergereicht | Einmalig manuell per `pulumi config set --secret` |
| PAT für Workflows | Repo-Secret `OPS_GITHUB_TOKEN` | Ein Teammitglied |

Key Vault, Secret-Rotation und getrennte Secrets pro Verbraucher SHALL NOT Voraussetzung für Phase 1 sein. Die Dokumentation SHALL diese Vereinfachung als bewusst akzeptiertes Risiko für Staging nennen.

#### Scenario: Secret für einen Verbraucher bereitstellen
- **WHEN** ein Verbraucher das PostgreSQL-Passwort oder einen Webhook-Key benötigt
- **THEN** bezieht er es über den Foundation-Ausgabevertrag
- **AND** steht der Wert weder in Git noch in Workflow-Logs im Klartext

### Requirement: Zugänge ohne Rollenzuweisungen
Die Plattform SHALL ohne Azure-Rollenzuweisungen und ohne Entra-App-Registrierungen funktionieren, weil nur Contributor-Rechte vorhanden sind. Zugänge entstehen wie folgt:

| Wer | Ziel | Zugang |
| --- | --- | --- |
| Person mit Contributor (lokal) | Azure-Ressourcen des Foundation-Stacks | `az login` |
| GitHub-Workflows | Pulumi-State | Storage-Key aus Repo-Secret |
| GitHub-Workflows | AKS | Admin-Kubeconfig aus den Foundation-Ausgaben; lokale Accounts bleiben aktiviert |
| GitHub-Workflows | ACR (Push) | ACR-Admin-User aus den Foundation-Ausgaben |
| AKS | ACR (Pull) | Image-Pull-Secret, das der App-Stack aus dem ACR-Admin-User anlegt |
| Workflows und Agent | PostgreSQL | Admin-Login mit Passwort, TLS erzwungen |
| Agent | Diagnose-Storage | Storage-Key |
| Agent | Kubernetes | ServiceAccount `operations-agent` mit ClusterRoleBinding aus dem Agenten-Chart |
| Agent und Workflows | GitHub | Fine-grained PAT eines Teammitglieds |

Der Agent SHALL in Phase 1 keinen Zugriff auf Azure Resource Manager, Azure-Messwerte oder Log Analytics benötigen.

#### Scenario: Workflow ohne Azure-Identität
- **WHEN** ein App-Deploy-Workflow auf einem GitHub-hosted Runner läuft
- **THEN** liest er den Foundation-State, rollt Kubernetes-Objekte aus und meldet sich an PostgreSQL an
- **AND** benötigt er dafür keine Azure-Anmeldung

#### Scenario: Kein Schritt braucht Owner
- **WHEN** Bootstrap und Foundation-Stack mit einer Contributor-Identität ausgerollt werden
- **THEN** schlägt kein Schritt wegen fehlender Berechtigung für Rollenzuweisungen fehl

### Requirement: Dokumentierte manuelle Voraussetzungen
Die Bootstrap-Dokumentation SHALL alle nicht automatisierten Schritte nennen und die dafür nötigen Rechte: Contributor auf der Subscription, Admin-Rechte auf dem Repository für Repo-Secrets und den Issue-Webhook, ein Fine-grained PAT mit Issues-, Contents-, Pull-Request- und Actions-Rechten für `hackathon-platform-team` und `hackathon-platform-affine`. Ein Vorab-Check SHALL diese Voraussetzungen prüfen, bevor Ressourcen angelegt werden.

#### Scenario: PAT ohne nötige Rechte
- **WHEN** der hinterlegte PAT keine Actions-Rechte besitzt
- **THEN** bricht der Vorab-Check ab und nennt das fehlende Recht

### Requirement: Nachgewiesene Zugänge
Ein Zugangscheck-Workflow `.github/workflows/foundation-access-check.yml` SHALL jeden Zugang aus CI-Sicht tatsächlich ausüben: Foundation-State lesen, mit der Admin-Kubeconfig `kubectl auth can-i '*' '*'` ausführen, sich an ACR und PostgreSQL anmelden, im Diagnose-Storage schreiben, lesen und löschen sowie mit dem PAT Repository und Issues lesen.

#### Scenario: Vollständige Zugänge
- **WHEN** der Zugangscheck nach einem erfolgreichen Foundation-Lauf ausgeführt wird
- **THEN** meldet er jede Zugriffskategorie einzeln als erfolgreich

#### Scenario: Unvollständiger Zugang
- **WHEN** ein Zugang fehlschlägt
- **THEN** schlägt der Check mit der betroffenen Zugriffskategorie fehl
- **AND** wird der Bootstrap nicht als vollständig funktionsfähig gemeldet

### Requirement: Eindeutiger Ressourcenbesitz
Jede verwaltete Ressource SHALL genau einem Stack zugeordnet sein. Grundregel: Azure-Ressourcen gehören zum Foundation-Stack, weil nur dieser mit Azure-Anmeldung läuft. App-Stacks verwalten Kubernetes-Objekte und Datenbankobjekte über das PostgreSQL-Protokoll.

| Ressource | Besitzer |
| --- | --- |
| Resource Group, AKS, ACR, Log Analytics, Application Insights, Diagnose-Storage, PostgreSQL-Server und Datenbank `operations` | Foundation-Stack |
| Ingress-Controller und Zertifikatsverwaltung | Foundation-Stack |
| Action Groups `ops-intake` und `ops-humans`, Budget-Alarm | Foundation-Stack |
| Verfügbarkeitstests und Alarmregeln je App, erzeugt aus `platform/catalog/<app-id>/app.yaml` | Foundation-Stack |
| Verfügbarkeitstest des Betriebsagenten | Foundation-Stack |
| Namespaces, Helm-Releases, Kubernetes-Secrets, -ConfigMaps und Ingress-Regeln einer App | App-Stack |
| App-eigene Datenbanken und Logins auf dem gemeinsamen PostgreSQL-Server | App-Stack (über das PostgreSQL-Protokoll) |

#### Scenario: Einzelne App veröffentlichen
- **WHEN** eine neue Version von AFFiNE ausgerollt wird
- **THEN** wird dafür weder ein Foundation-Lauf noch die Übernahme gemeinsamer Ressourcen in den App-Stack benötigt

#### Scenario: Neue App im Katalog
- **WHEN** ein neuer Katalogeintrag hinzukommt
- **THEN** erzeugt der nächste Foundation-Lauf dessen Verfügbarkeitstest und Alarmregel
- **AND** bleibt der App-Stack frei von Azure-Ressourcen

#### Scenario: Keine gegenseitigen Änderungen
- **WHEN** nach einem erfolgreichen App-Release ein Preview des Foundation-Stacks läuft
- **THEN** zeigt er keine Änderungen
- **AND** zeigt umgekehrt ein Preview des App-Stacks nach einem Foundation-Release keine Änderungen

### Requirement: Bereitstellung ohne Agenten
Foundation-Bereitstellung, Zugangscheck und dokumentierte Wiederherstellung SHALL ohne laufende Agenten-API oder Worker ausführbar sein. Der Foundation-Stack SHALL keine Operations-Sperre erwerben.

#### Scenario: Agenten-Release entfernt
- **WHEN** das Agenten-Release vollständig gelöscht ist
- **THEN** laufen Foundation-Lauf und Zugangscheck erfolgreich durch

#### Scenario: Agentendatenbank nicht erreichbar
- **WHEN** PostgreSQL nicht erreichbar ist
- **THEN** kann der Foundation-Lauf trotzdem starten und den Server reparieren oder neu anlegen

### Requirement: Dokumentierte Wiederherstellung
Die Betriebsdokumentation SHALL drei Wiederherstellungsfälle beschreiben: verlorener Cluster, beschädigter State und verlorene Agentendatenbank. Ein verlorener Cluster SHALL allein durch einen lokalen Foundation-Lauf und anschließende App-Releases wiederherstellbar sein. Für die Datenbank genügt ein dokumentiertes manuelles Point-in-Time-Restore mit 7 Tagen Backup-Aufbewahrung. Die Dokumentation SHALL vermerken, dass Wiederherstellung wegen fehlender Rollenzuweisungen nicht aus GitHub Actions läuft.

#### Scenario: Cluster gelöscht
- **WHEN** der AKS-Cluster gelöscht wurde und der Foundation-Stack lokal ausgerollt wird
- **THEN** entsteht ein neuer Cluster mit neuer Admin-Kubeconfig in den Ausgaben
- **AND** bleiben PostgreSQL-Daten, Diagnoseartefakte und `contractVersion` unverändert
- **AND** lassen sich die Apps danach mit ihren bestehenden Release-Workflows wieder ausrollen

### Requirement: Nachvollziehbare Bootstrap-Ergebnisse
Ein erfolgreicher Foundation-Lauf SHALL Stack-Namen, Ressourcenreferenzen, Git-SHA und Folgekommandos ohne Secret-Werte ausgeben. Werte für Verbraucher SHALL ausschließlich über den Foundation-Ausgabevertrag (siehe `shared-platform-runtime`) übergeben werden.

#### Scenario: Übergabe an die Verbraucher
- **WHEN** der Bootstrap als abgeschlossen gemeldet wird
- **THEN** finden App-Stacks, Agentenentwicklung und Persistenzschicht Referenzen im Ausgabevertrag und Ergebnisse im Zugangscheck-Workflow
- **AND** enthalten Ausgaben und Logs keine Zugangsdaten im Klartext

### Requirement: Kostenkontrolle für Staging
Die Plattform SHALL einen Azure-Budget-Alarm für die Plattform-Resource-Group an `ops-humans` senden. Die Betriebsdokumentation SHALL beschreiben, wie AKS-Cluster und PostgreSQL-Server zur Kostenersparnis gestoppt und in umgekehrter Reihenfolge wieder gestartet werden.

#### Scenario: Plattform über Nacht stoppen
- **WHEN** Cluster und Datenbank nach Dokumentation gestoppt und am nächsten Tag wieder gestartet werden
- **THEN** sind Apps, Agent und Daten danach ohne Foundation-Lauf wieder verfügbar

## Abgrenzung

A liefert Monorepo-Gerüst, Konventionen, Bootstrap, Zugänge, einfache Secret-Ablage und alle Azure-Ressourcen. B liefert App-Stacks und App-Releases, C Datenmodell und Operations-Client, D Agentencode und Anwendungspaket. Die AFFiNE-Paketierung kommt aus `hackathon-platform-affine` und liegt außerhalb dieser Specs.
