# Spec Delta

## Purpose

Die Plattform lässt sich aus dokumentierten Voraussetzungen und versioniertem Code reproduzierbar initialisieren und unabhängig vom Betriebsagenten wieder bereitstellen.

## ADDED Requirements

### Requirement: Repository-Grundgerüste
Die Plattform SHALL mit den Repositories `mmk-platform`, `mmk-operations-agent` und `mmk-platform-demo` starten. Ihre Grundgerüste SHALL die Verzeichnisstruktur aus Abschnitt 9 des Produktdokuments enthalten und die Bereiche der einzelnen Verantwortlichen so trennen, dass parallele Arbeit ohne spätere Umstrukturierung möglich ist.

#### Scenario: Paralleler Implementierungsstart
- **WHEN** die Implementierung beginnt
- **THEN** existieren die drei Repositories mit ihren Grundgerüsten und einer Zuordnung der Verzeichnisse zu Verantwortlichen
- **AND** ist `staging` als erste Umgebung angelegt

### Requirement: Verbindliche Bezeichner
App-IDs SHALL dem Muster `^[a-z][a-z0-9-]{1,30}$` folgen. Umgebungen SHALL `staging` und später `production` heißen. Eine Release-Referenz SHALL aus Git-SHA, Image-Digest und Chart-Version bestehen. Die Konventionen SHALL maschinenlesbar unter `mmk-platform/schemas/conventions/` liegen.

#### Scenario: Ungültige App-ID
- **WHEN** ein Katalogeintrag oder Workflow die App-ID `Demo_App` verwendet
- **THEN** lehnt die Konventionsprüfung sie vor jeder Änderung ab

### Requirement: Ableitbare Ressourcennamen
Aus App-ID und Umgebung SHALL sich alle Namen eindeutig ableiten lassen: Pulumi-Stack `organization/app-<app-id>/<env>`, Namespace `<app-id>-<env>`, Resource Group `rg-<app-id>-<env>` für app-eigene Azure-Ressourcen und Host `<app-id>.<baseDomain>`. Der Foundation-Stack SHALL `organization/foundation/<env>` heißen, seine Resource Group `rg-mmk-platform-<env>`. Alle Azure-Ressourcen SHALL die Tags `app-id`, `environment`, `stack` und `managed-by` tragen.

#### Scenario: Namen für eine neue App ableiten
- **WHEN** ein App-Stack für die App-ID `platform-demo` in `staging` angelegt wird
- **THEN** heißen Stack, Namespace und Host `organization/app-platform-demo/staging`, `platform-demo-staging` und `platform-demo.<baseDomain>`
- **AND** ist keine Absprache über weitere Namen nötig

### Requirement: Wiederholbarer State-Bootstrap
Die Plattform SHALL ein vom App-Lebenszyklus unabhängiges Pulumi-State-Backend in einem eigenen Azure-Storage-Account besitzen. Ein idempotentes Azure-CLI-Skript SHALL Resource Group, Storage-Account und State-Container nur anlegen, wenn sie fehlen. Eine Wiederholung SHALL bestehende Ressourcen übernehmen, ohne State oder Passphrase zu ersetzen.

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
Der State-Container SHALL Blob-Versionierung und Soft Delete mit mindestens 30 Tagen Aufbewahrung verwenden. Der State-Storage-Account SHALL eine `CanNotDelete`-Sperre tragen. Foundation-Workflows SHALL über eine gemeinsame GitHub-Actions-`concurrency`-Gruppe pro Umgebung serialisiert laufen.

#### Scenario: State versehentlich überschrieben
- **WHEN** die State-Datei des Foundation-Stacks beschädigt oder überschrieben wird
- **THEN** lässt sich die vorherige Version über die Blob-Versionierung nach dokumentiertem Verfahren wiederherstellen

#### Scenario: Zwei Foundation-Läufe gleichzeitig
- **WHEN** ein zweiter Foundation-Workflow für dieselbe Umgebung startet, während der erste läuft
- **THEN** wartet der zweite, bis der erste beendet ist

### Requirement: Einfache Secret-Verwaltung für Phase 1
Phase 1 SHALL Secrets mit geringem Aufwand verwalten und keine separate Secret-Infrastruktur voraussetzen. Zugangsdaten SHALL nicht in Git und nicht in Prompts liegen. Darüber hinaus sind folgende Ablageorte für Staging ausreichend:

| Secret | Ablage | Erzeugt durch |
| --- | --- | --- |
| Storage-Key des State-Backends, Pulumi-Passphrase | GitHub-Organisations-Secrets `PULUMI_BACKEND_KEY`, `PULUMI_CONFIG_PASSPHRASE` | Bootstrap-Skript bzw. einmalig manuell |
| PostgreSQL-Admin-Passwort | Geheime Foundation-Ausgabe | Foundation-Stack (zufällig generiert) |
| API-Key für den Alarm-Webhook, GitHub-Webhook-Secret | Geheime Foundation-Ausgaben | Foundation-Stack (zufällig generiert) |
| Modellzugang (Anthropic-Key, Azure-OpenAI-Key) und GitHub-App-Private-Key | Geheime Foundation-Konfiguration, als geheime Ausgabe weitergereicht | Einmalig manuell per `pulumi config set --secret` |

Key Vault, Secret-Rotation und getrennte Secrets pro Verbraucher SHALL NOT Voraussetzung für Phase 1 sein. Die Dokumentation SHALL diese Vereinfachung als bewusst akzeptiertes Risiko für Staging nennen.

#### Scenario: Secret für einen Verbraucher bereitstellen
- **WHEN** ein Verbraucher das PostgreSQL-Passwort oder einen Webhook-Key benötigt
- **THEN** bezieht er es über den Foundation-Ausgabevertrag
- **AND** steht der Wert weder in Git noch in Workflow-Logs im Klartext

### Requirement: Tatsächlich nutzbare Identitäten und Zugänge
Die Plattform SHALL die folgenden Identitäten einrichten. Azure-Zugriffe aus GitHub Actions SHALL über GitHub-OIDC ohne Client-Secret erfolgen. Phase 1 vergibt bewusst breite Rechte.

| Identität | Art | Verwendet von | Rechte |
| --- | --- | --- | --- |
| `platform-deployer` | Entra-App mit Federated Credentials für `mmk-platform` (Branch `main` und Environment `staging`) | Foundation- und App-Deployment-Workflows im Plattform-Repository | Owner auf der Subscription, weil Foundation- und App-Stacks Rollenzuweisungen anlegen; darüber auch Admin-Kubeconfig für AKS |
| `artifact-publisher` | Entra-App mit Federated Credentials für `mmk-operations-agent` und `mmk-platform-demo` (Branch `main`) | Build-Workflows der App-Repositories | `AcrPush` auf der ACR |
| `operations-agent` | User-assigned Managed Identity mit AKS-Workload-Identity-Federation auf ServiceAccount `operations-agent` im Namespace `operations-agent-staging` | API und Worker des Betriebsagenten | Contributor auf der Subscription, `Log Analytics Reader`, `Storage Blob Data Contributor` auf dem Diagnose-Storage, `Cognitive Services OpenAI User` auf der Azure-OpenAI-Ressource |
| AKS-Kubelet-Identität | Von AKS verwaltet | Image-Pulls | `AcrPull` auf der ACR |
| `mmk-ops-agent` | GitHub App, einmalig manuell von einem Org-Admin angelegt | Betriebsagent und repositoryübergreifende Versions-PRs aus App-Pipelines | Issues, Contents, Pull Requests und Actions (lesen/schreiben) auf `mmk-platform`, `mmk-operations-agent`, `mmk-platform-demo` |

Der Kubernetes-Zugriff des Agenten innerhalb des Clusters SHALL über seinen ServiceAccount und eine ClusterRoleBinding laufen, die das Agenten-Chart mitbringt. Die Federated Credential der Agentenidentität SHALL im Foundation-Stack liegen, damit sie nach einer Neuerstellung des Clusters auf den neuen OIDC-Issuer zeigt.

#### Scenario: Deployment-Identität prüfen
- **WHEN** ein Workflow aus `main` oder dem Environment `staging` des Plattform-Repositories läuft
- **THEN** meldet er sich ohne Client-Secret per OIDC bei Azure an und kann den Pulumi-State und Azure-Ressourcen verwenden

#### Scenario: Agentenidentität prüfen
- **WHEN** ein Test-Pod mit dem ServiceAccount `operations-agent` im Namespace `operations-agent-staging` startet
- **THEN** erhält er per Workload Identity ein Azure-Token und kann Azure-Ressourcen lesen, Log Analytics abfragen und in den Diagnose-Storage schreiben

#### Scenario: Pull-Request aus einem Branch
- **WHEN** ein Workflow für einen Pull Request läuft
- **THEN** erhält er keine Token von `platform-deployer`

### Requirement: Dokumentierte manuelle Voraussetzungen
Die Bootstrap-Dokumentation SHALL alle Schritte nennen, die nicht automatisiert sind, und die dafür nötigen Rechte: Owner auf der Subscription, Recht zum Anlegen von Entra-App-Registrierungen, GitHub-Org-Admin für die GitHub App und die Organisations-Secrets sowie die Delegation der Basisdomain. Ein Vorab-Check SHALL prüfen, ob die ausführende Person diese Rechte hat.

#### Scenario: Fehlende Entra-Rechte
- **WHEN** die ausführende Person keine Entra-App-Registrierungen anlegen darf
- **THEN** bricht der Vorab-Check vor dem ersten Ressourcenanlegen ab und nennt das fehlende Recht

### Requirement: Nachgewiesene Zugänge
Ein Zugangscheck-Workflow SHALL jeden benötigten Zugriff tatsächlich ausüben: Admin-Kubeconfig abrufen und `kubectl auth can-i` ausführen, sich an PostgreSQL anmelden, im Diagnose-Storage schreiben, lesen und löschen sowie eine Log-Analytics-Abfrage ausführen. Er SHALL auf verzögert wirksame Rollenzuweisungen bis zu zehn Minuten warten.

#### Scenario: Vollständige Zugänge
- **WHEN** der Zugangscheck nach einem erfolgreichen Foundation-Lauf ausgeführt wird
- **THEN** meldet er jede Zugriffskategorie einzeln als erfolgreich

#### Scenario: Unvollständige Zugriffsrechte
- **WHEN** Azure-Ressourcen erreichbar sind, aber Kubernetes- oder Datenzugriff auch nach der Wartezeit fehlt
- **THEN** schlägt die Zugangsprüfung mit der betroffenen Zugriffskategorie fehl
- **AND** wird der Bootstrap nicht als vollständig funktionsfähig gemeldet

### Requirement: Eindeutiger Ressourcenbesitz
Jede verwaltete Ressource SHALL genau einem Stack zugeordnet sein. Es gilt folgende Grundregel: Was eine bestimmte App beschreibt oder überwacht, gehört in deren App-Stack. Was mehrere Apps gemeinsam nutzen, gehört in den Foundation-Stack. Ausnahme ist die Überwachung des Betriebsagenten selbst. Sie liegt im Foundation-Stack, damit sie nicht zusammen mit einem defekten oder entfernten Agenten-Release verschwindet.

| Ressource | Besitzer |
| --- | --- |
| Resource Group der Plattform, AKS, ACR, Log Analytics, Application Insights, Diagnose-Storage | Foundation-Stack |
| Ingress-Controller, Zertifikatsverwaltung, DNS-Zone und Wildcard-Eintrag der Basisdomain | Foundation-Stack |
| PostgreSQL-Server und Datenbank `operations` des Agenten | Foundation-Stack |
| Identitäten und Rollenzuweisungen aus der Identitätstabelle | Foundation-Stack |
| Action Groups `ops-intake` und `ops-humans`, Budget-Alarm | Foundation-Stack |
| Heartbeat-Alarm und Verfügbarkeitstest des Betriebsagenten | Foundation-Stack |
| Namespaces, Helm-Releases, Kubernetes-Secrets und -ConfigMaps einer App | App-Stack |
| Ingress-Regeln und app-spezifische Azure-Ressourcen (z. B. AFFiNE-Datenbank und Speicher) | App-Stack |
| Alarmregeln und Funktionstests einer App, einschließlich des Demo-App-Alarms | App-Stack; verweist auf die Action-Group-ID aus dem Ausgabevertrag |

#### Scenario: Einzelne App veröffentlichen
- **WHEN** eine neue Version der Demo-App ausgerollt wird
- **THEN** wird dafür weder ein Foundation-Rebuild noch die Übernahme gemeinsamer Ressourcen in den App-Stack benötigt

#### Scenario: Keine gegenseitigen Änderungen
- **WHEN** nach einem erfolgreichen App-Release ein Preview des Foundation-Stacks läuft
- **THEN** zeigt er keine Änderungen
- **AND** zeigt umgekehrt ein Preview des App-Stacks nach einem Foundation-Release keine Änderungen an Foundation-Ressourcen

### Requirement: Bereitstellung ohne Agenten
Foundation-Bereitstellung, Zugangscheck und dokumentierte Wiederherstellung SHALL über GitHub Actions ohne laufende Agenten-API oder Worker ausführbar sein. Kein Foundation-Workflow SHALL eine Antwort des Agenten voraussetzen.

#### Scenario: Agenten-Release entfernt
- **WHEN** das Agenten-Release vollständig gelöscht ist
- **THEN** laufen Foundation-Workflow und Zugangscheck erfolgreich durch

### Requirement: Dokumentierte Wiederherstellung
Die Betriebsdokumentation SHALL für Phase 1 drei Wiederherstellungsfälle beschreiben: verlorener Cluster, beschädigter State und verlorene Agentendatenbank. Ein verlorener Cluster SHALL allein durch den Foundation-Workflow und anschließende App-Releases wiederherstellbar sein. Für die Datenbank genügt in Phase 1 ein dokumentiertes manuelles Point-in-Time-Restore mit 7 Tagen Backup-Aufbewahrung.

#### Scenario: Cluster gelöscht
- **WHEN** der AKS-Cluster gelöscht wurde und der Foundation-Workflow läuft
- **THEN** entsteht ein neuer Cluster und die Federated Credential der Agentenidentität zeigt auf dessen OIDC-Issuer
- **AND** bleiben PostgreSQL-Daten, Diagnoseartefakte und `contractVersion` unverändert
- **AND** lassen sich die Apps danach mit ihren bestehenden Release-Workflows wieder ausrollen

### Requirement: Nachvollziehbare Bootstrap-Ergebnisse
Ein erfolgreicher Foundation-Lauf SHALL Stack-Namen, Ressourcenreferenzen, Git-SHA und Folgekommandos ohne Secret-Werte in der Workflow-Zusammenfassung ausgeben. Werte für Verbraucher SHALL ausschließlich über den Foundation-Ausgabevertrag (siehe `shared-platform-runtime`) übergeben werden.

#### Scenario: Übergabe an die Verbraucher
- **WHEN** der Bootstrap als abgeschlossen gemeldet wird
- **THEN** finden App-Stacks, Agentenentwicklung und Persistenzschicht Referenzen und Ergebnisse des Zugangschecks in der Workflow-Zusammenfassung und im Ausgabevertrag
- **AND** enthalten Workflow-Zusammenfassung und Logs keine Zugangsdaten im Klartext

### Requirement: Kostenkontrolle für Staging
Die Plattform SHALL einen Azure-Budget-Alarm für die Plattform- und App-Resource-Groups an `ops-humans` senden. Die Betriebsdokumentation SHALL beschreiben, wie AKS-Cluster und PostgreSQL-Server zur Kostenersparnis gestoppt und in umgekehrter Reihenfolge wieder gestartet werden.

#### Scenario: Plattform über Nacht stoppen
- **WHEN** Cluster und Datenbank nach Dokumentation gestoppt und am nächsten Tag wieder gestartet werden
- **THEN** sind Apps, Agent und Daten danach ohne Foundation-Lauf wieder verfügbar

## Abgrenzung

A liefert Bootstrap, Identitäten, einfache Secret-Ablage, Konventionen und Repository-Grundgerüste. B liefert App-Stacks und App-Releases, C Datenmodell und Operations-Client, D Agentencode und Anwendungspaket. Deren Implementierung bleibt außerhalb dieses Changes; der Bootstrap stellt ihre Infrastrukturvoraussetzungen bereit.
