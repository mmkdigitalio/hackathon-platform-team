# Spec Delta

## Purpose

Gemeinsame Laufzeit-, Daten- und Monitoringdienste bilden die stabile Grundlage für getrennte App-Releases und den selbst gehosteten Betriebsagenten.

## ADDED Requirements

### Requirement: Reproduzierbare Staging-Laufzeit
Die Plattform SHALL einen AKS-Cluster, ACR, Log Analytics und eine workspace-basierte Application-Insights-Ressource reproduzierbar bereitstellen. Die Foundation-Konfiguration SHALL Region, Knotengröße und -anzahl sowie die AKS-Version festlegen; die Dokumentation SHALL das Upgrade-Verfahren beschreiben.

#### Scenario: Staging aus versioniertem Zustand bereitstellen
- **WHEN** Foundation-Konfiguration und Bootstrap vorliegen
- **THEN** kann der Foundation-Workflow die gemeinsame Laufzeit bereitstellen
- **AND** sind Cluster, Registry und Monitoring über den Ausgabevertrag erreichbar

### Requirement: Container-Logs in Log Analytics
Container Insights SHALL aktiviert sein, damit Container-Logs aller Namespaces in Log Analytics abfragbar sind.

#### Scenario: Container-Logs abfragen
- **WHEN** ein Pod in einem beliebigen App-Namespace auf stdout schreibt
- **THEN** ist die Zeile nach wenigen Minuten per Log-Analytics-Abfrage mit Namespace und Pod-Namen auffindbar

### Requirement: Gemeinsamer HTTPS-Zugang
Die Plattform SHALL einen gemeinsamen Ingress-Controller, eine automatische Zertifikatsverwaltung und für die konfigurierte Basisdomain eine Azure-DNS-Zone mit Wildcard-Eintrag `*.<baseDomain>` auf die Ingress-IP bereitstellen. Der Ingress-Controller SHALL zum Entscheidungszeitpunkt aktiv gepflegt sein; die Wahl und ihre Begründung SHALL dokumentiert werden. App-Stacks SHALL nur Ingress-Regeln für ihren Host anlegen, keine DNS-Einträge.

#### Scenario: Neue App unter HTTPS erreichbar
- **WHEN** ein App-Stack eine Ingress-Regel für `<app-id>.<baseDomain>` anlegt
- **THEN** ist die App ohne weitere DNS- oder Zertifikatsänderung per HTTPS mit gültigem Zertifikat erreichbar

### Requirement: Einfacher öffentlicher Netzzugang
Phase 1 SHALL ohne private Netzwerke und ohne Self-hosted Runner auskommen. GitHub-hosted Runner und Pods im Cluster SHALL alle gemeinsamen Dienste über öffentliche Endpunkte erreichen. Geschützt wird jeweils mit einem einfachen Mechanismus:

| Dienst | Erreichbarkeit | Schutz |
| --- | --- | --- |
| AKS-API-Server | Öffentlich, ohne IP-Einschränkung | Admin-Kubeconfig nur für Identitäten mit Azure-Rolle auf dem Cluster; lokale Accounts bleiben aktiviert |
| PostgreSQL Flexible Server | Öffentlich, Firewall erlaubt alle IPv4-Adressen | TLS erzwungen, Login mit generiertem Passwort |
| Diagnose-Storage und ACR | Öffentlich | Entra-Rollen der Identitäten |
| Öffentliche Endpunkte des Betriebsagenten | Über den gemeinsamen Ingress per HTTPS | API-Key bzw. GitHub-Webhook-Signatur, geprüft durch den Ereigniseingang |

Die Dokumentation SHALL diese Vereinfachung als akzeptiertes Risiko für Staging nennen.

#### Scenario: GitHub-hosted Runner erreicht gemeinsame Dienste
- **WHEN** der Zugangscheck auf einem GitHub-hosted Runner läuft
- **THEN** erreicht er die AKS-API mit der Admin-Kubeconfig und öffnet eine TLS-Verbindung zur Datenbank `operations`
- **AND** ist dafür weder ein Self-hosted Runner noch eine IP-Freigabe nötig

#### Scenario: Agentenprozess erreicht seine Dienste
- **WHEN** ein Pod mit der Agentenidentität im Cluster startet
- **THEN** erreicht er PostgreSQL, Diagnose-Storage und Log Analytics

### Requirement: Agentendatenbank außerhalb des Clusters
Die Plattform SHALL dem Agenten einen PostgreSQL Flexible Server außerhalb des AKS-Clusters mit der Datenbank `operations` und 7 Tagen Backup-Aufbewahrung bereitstellen. Phase 1 verwendet einen gemeinsamen Admin-Login für Migrationen und Laufzeit. Der Foundation-Stack SHALL keine Tabellen, Checkpoint-Strukturen oder Auftragslogik verwalten.

#### Scenario: Agentenprozesse ersetzen
- **WHEN** API und Worker gelöscht und neu gestartet werden
- **THEN** bleiben die Daten in `operations` erhalten
- **AND** können Migrationen und Persistenzzugriffe des Agenten dieselbe Datenbank weiterverwenden

### Requirement: Speicher für Diagnoseartefakte
Die Plattform SHALL einen Blob-Container `diagnostics` bereitstellen. Eine Lifecycle-Regel SHALL Artefakte nach 30 Tagen löschen.

#### Scenario: Artefakt überlebt Agentenneustart
- **WHEN** der Agent ein Diagnosepaket ablegt und danach neu startet
- **THEN** kann er das Paket bis zum Ablauf der Aufbewahrung wieder lesen

### Requirement: Versionierter Foundation-Ausgabevertrag
Der Ausgabevertrag SHALL aus den Stack-Ausgaben von `organization/foundation/staging` bestehen. Die Ausgabe `contractVersion` SHALL eine ganze Zahl sein, die bei jeder inkompatiblen Änderung steigt. Hinzufügen ist kompatibel, Umbenennen oder Entfernen nicht. Das Schema SHALL unter `mmk-platform/schemas/foundation-outputs/` liegen und mindestens folgende Ausgaben beschreiben:

| Ausgabe | Inhalt | Geheim |
| --- | --- | --- |
| `contractVersion` | Vertragsversion | nein |
| `environment`, `location`, `resourceGroupName`, `baseDomain` | Umgebung, Ort und Basisdomain | nein |
| `aksClusterName`, `aksOidcIssuerUrl` | Cluster | nein |
| `acrLoginServer` | Registry | nein |
| `logAnalyticsWorkspaceId`, `appInsightsConnectionString` | Monitoring | nein |
| `actionGroupIntakeId`, `actionGroupHumansId` | Alarmziele | nein |
| `operationsApiHost` | `operations-agent.<baseDomain>`, unter dem das Agenten-Release seine API veröffentlichen muss | nein |
| `postgresHost`, `postgresDatabase`, `postgresUser`, `postgresPassword` | Agentendatenbank | nur Passwort |
| `diagnosticsStorageAccount`, `diagnosticsContainer` | Diagnoseartefakte | nein |
| `operationsAgentClientId` | Client-ID der Agentenidentität für Workload Identity | nein |
| `alertWebhookKey`, `githubWebhookSecret` | Schutz der Eingänge | ja |
| `modelApiKeys`, `githubAppId`, `githubAppPrivateKey` | Modell- und GitHub-Zugang des Agenten | ja, außer App-ID |

#### Scenario: Neue Ausgabe ergänzen
- **WHEN** der Foundation-Stack eine zusätzliche Ausgabe erhält
- **THEN** bleibt `contractVersion` gleich und bestehende Verbraucher laufen unverändert weiter

### Requirement: Zugriffswege auf den Ausgabevertrag
Verbraucher SHALL den Vertrag auf einem von drei Wegen lesen: Pulumi-App-Stacks über eine `StackReference`; GitHub-Workflows über die Composite Action `mmk-platform/.github/actions/foundation-outputs`; Prozesse im Cluster über Kubernetes-Secrets und -ConfigMaps, die ihr App-Stack aus der `StackReference` anlegt. Die Composite Action SHALL Werte als Step-Ausgaben liefern, geheime Werte maskieren und für alle Repositories der Organisation freigegeben sein.

#### Scenario: Verbraucher gegen Foundation anbinden
- **WHEN** ein App-Stack, ein Build-Workflow oder ein Agentenprozess konfiguriert wird
- **THEN** bezieht er die benötigten Werte über einen der drei Wege
- **AND** müssen keine Azure-Ressourcennamen aus Logs oder dem Portal abgeleitet werden

#### Scenario: Inkompatible Ausgaben
- **WHEN** ein Workflow die Composite Action mit einer anderen Vertragsversion als der veröffentlichten aufruft
- **THEN** bricht die Action vor jeder Änderung mit einer Meldung ab, die erwartete und vorhandene Version nennt

### Requirement: Alarmziele für Apps
Die Plattform SHALL die Action Group `ops-intake` bereitstellen. Sie sendet Alarme im Common Alert Schema an `https://<operationsApiHost>/events/azure-monitor?key=<alertWebhookKey>`. Alarmregeln einzelner Apps SHALL im jeweiligen App-Stack liegen, `actionGroupIntakeId` referenzieren und automatische Auflösung aktivieren, damit auch Resolved-Ereignisse gesendet werden. Der Ende-zu-Ende-Nachweis mit einem echten Demo-App-Alarm gehört zur gemeinsamen Abnahme P1-09.

#### Scenario: Action Group testen
- **WHEN** die Testbenachrichtigung von `ops-intake` ausgelöst wird, während der Ereigniseingang läuft
- **THEN** erreicht ein Request mit Common Alert Schema und gültigem Key den Eingang
- **AND** lehnt der Eingang denselben Request ohne Key ab

### Requirement: Benachrichtigungskanal für Menschen
Die Plattform SHALL die Action Group `ops-humans` mit E-Mail an einen dokumentierten Team-Verteiler bereitstellen. Sie SHALL keinen Webhook zum Agenten-Eingang enthalten. Kanal und Empfänger SHALL vor der Staging-Abnahme mit einer Testbenachrichtigung geprüft sein.

#### Scenario: Testbenachrichtigung
- **WHEN** die Testbenachrichtigung von `ops-humans` ausgelöst wird
- **THEN** erhält der Team-Verteiler eine E-Mail
- **AND** erreicht kein Request den Agenten-Eingang

### Requirement: Unabhängiges Signal bei Agentenausfall
Der Foundation-Stack SHALL zwei Alarme an `ops-humans` bereitstellen: einen Log-Search-Alarm, wenn zehn Minuten lang keine stdout-Zeile mit `"event": "worker_heartbeat"` aus `operations-agent-staging` eingeht, und einen Application-Insights-Verfügbarkeitstest, der alle fünf Minuten `https://<operationsApiHost>/healthz` aufruft. Der Worker schreibt die Heartbeat-Zeile mindestens jede Minute.

#### Scenario: Worker bleibt ausgefallen
- **WHEN** der Worker zehn Minuten lang kein Heartbeat schreibt
- **THEN** erhält der Team-Verteiler eine E-Mail mit Umgebung und betroffenem Dienst
- **AND** ist dafür weder ein Modellaufruf noch ein laufender Agent nötig

#### Scenario: Agenten-Release entfernt
- **WHEN** das Agenten-Release vollständig gelöscht wird
- **THEN** bleiben Heartbeat-Alarm und Verfügbarkeitstest bestehen und schlagen an

## Abgrenzung

A besitzt gemeinsame Azure-/Cluster-Ressourcen, HTTPS-Zugang, Alarmziele und die Überwachung des Agenten. B liefert App-Pakete, App-Datenressourcen, Ingress-Regeln, App-Alarmregeln und Prüfziele und veröffentlicht die Agenten-API unter `operationsApiHost`. C liefert den Alarmempfänger mit Key-Prüfung, den `/healthz`-Endpunkt sowie Auftrags-/Checkpoint-Schema. C und D sorgen gemeinsam dafür, dass der Worker das Heartbeat-Format einhält. D liefert Werkzeuge und Agentenpaket einschließlich ServiceAccount `operations-agent` und ClusterRoleBinding. A verdrahtet diese Lieferungen, implementiert sie aber nicht erneut.
