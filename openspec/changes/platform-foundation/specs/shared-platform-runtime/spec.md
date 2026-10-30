# Spec Delta

## Purpose

Gemeinsame Laufzeit-, Daten- und Monitoringdienste bilden die stabile Grundlage für getrennte App-Releases und den selbst gehosteten Betriebsagenten.

## ADDED Requirements

### Requirement: Reproduzierbare Staging-Laufzeit
Die Plattform SHALL einen AKS-Cluster mit aktivierten lokalen Accounts, eine ACR mit Admin-User, Log Analytics und eine workspace-basierte Application-Insights-Ressource reproduzierbar bereitstellen. Die Foundation-Konfiguration SHALL Region, Knotengröße und -anzahl sowie die AKS-Version festlegen; die Dokumentation SHALL das Upgrade-Verfahren beschreiben.

#### Scenario: Staging aus versioniertem Zustand bereitstellen
- **WHEN** Foundation-Konfiguration und Bootstrap vorliegen
- **THEN** kann der lokale Foundation-Lauf die gemeinsame Laufzeit bereitstellen
- **AND** sind Cluster, Registry und Monitoring über den Ausgabevertrag erreichbar

### Requirement: Container-Logs für Menschen
Container-Logs aller Namespaces SHALL über Container Insights in Log Analytics abfragbar sein, soweit sich das ohne Rollenzuweisung einrichten lässt. Weder der Agent noch die Ausfallmeldung des Agenten SHALL davon abhängen.

#### Scenario: Container-Logs abfragen
- **WHEN** ein Pod in einem App-Namespace auf stdout schreibt
- **THEN** ist die Zeile nach wenigen Minuten im Azure-Portal per Log-Analytics-Abfrage auffindbar

### Requirement: Gemeinsamer HTTP(S)-Zugang ohne eigene Domain
Die Plattform SHALL einen gepflegten Ingress-Controller mit öffentlicher IP und eine automatische Zertifikatsverwaltung mit Let's Encrypt bereitstellen. Die Ausgabe `baseDomain` SHALL `<ingress-ip-mit-bindestrichen>.sslip.io` sein, sodass jeder Host `<app-id>.<baseDomain>` ohne eigene DNS-Zone auflöst. Wahl und Begründung des Ingress-Controllers SHALL dokumentiert sein. Schlägt die Zertifikatsausstellung fehl, ist HTTP über dieselben Hosts der dokumentierte Fallback für Staging.

#### Scenario: Neue App erreichbar
- **WHEN** ein App-Stack eine Ingress-Regel für `<app-id>.<baseDomain>` anlegt
- **THEN** ist die App ohne DNS-Änderung per HTTPS mit gültigem Zertifikat erreichbar

#### Scenario: Ingress-IP bleibt stabil
- **WHEN** der Foundation-Stack erneut ausgerollt wird, ohne den Ingress-Controller zu ersetzen
- **THEN** bleibt `baseDomain` unverändert

### Requirement: Einfacher öffentlicher Netzzugang
Phase 1 SHALL ohne private Netzwerke und ohne Self-hosted Runner auskommen. GitHub-hosted Runner und Pods SHALL alle gemeinsamen Dienste über öffentliche Endpunkte erreichen:

| Dienst | Erreichbarkeit | Schutz |
| --- | --- | --- |
| AKS-API-Server | Öffentlich, ohne IP-Einschränkung | Admin-Kubeconfig |
| PostgreSQL Flexible Server | Öffentlich, Firewall erlaubt alle IPv4-Adressen | TLS erzwungen, Passwort |
| ACR | Öffentlich | Admin-User |
| Diagnose-Storage | Öffentlich | Storage-Key |
| Öffentliche Endpunkte des Agenten | Über den Ingress | API-Key bzw. GitHub-Webhook-Signatur, geprüft durch den Ereigniseingang |

Die Dokumentation SHALL diese Vereinfachung als akzeptiertes Risiko für Staging nennen.

#### Scenario: GitHub-hosted Runner erreicht gemeinsame Dienste
- **WHEN** der Zugangscheck auf einem GitHub-hosted Runner läuft
- **THEN** erreicht er AKS-API, ACR, Diagnose-Storage und die Datenbank `operations`
- **AND** ist dafür weder ein Self-hosted Runner noch eine IP-Freigabe nötig

### Requirement: Gemeinsamer PostgreSQL-Server
Die Plattform SHALL einen PostgreSQL Flexible Server außerhalb des AKS-Clusters mit 7 Tagen Backup-Aufbewahrung und der Datenbank `operations` für den Agenten bereitstellen. App-Stacks DÜRFEN über das PostgreSQL-Protokoll eigene Datenbanken und Logins auf diesem Server anlegen; der Server SHALL dafür den Admin-Login über den Ausgabevertrag bereitstellen. Der Foundation-Stack SHALL keine Tabellen, Checkpoint-Strukturen oder Auftragslogik verwalten.

#### Scenario: Agentenprozesse ersetzen
- **WHEN** API und Worker gelöscht und neu gestartet werden
- **THEN** bleiben die Daten in `operations` erhalten
- **AND** können Migrationen und Persistenzzugriffe des Agenten dieselbe Datenbank weiterverwenden

#### Scenario: App-Datenbank anlegen
- **WHEN** der AFFiNE-App-Stack eine Datenbank `affine` mit eigenem Login benötigt
- **THEN** legt er sie ohne Azure-Anmeldung über das PostgreSQL-Protokoll an

### Requirement: Speicher für Diagnoseartefakte
Die Plattform SHALL einen Blob-Container `diagnostics` bereitstellen. Eine Lifecycle-Regel SHALL Artefakte nach 30 Tagen löschen.

#### Scenario: Artefakt überlebt Agentenneustart
- **WHEN** der Agent ein Diagnosepaket ablegt und danach neu startet
- **THEN** kann er das Paket bis zum Ablauf der Aufbewahrung wieder lesen

### Requirement: Versionierter Foundation-Ausgabevertrag
Der Ausgabevertrag SHALL aus den Stack-Ausgaben von `organization/foundation/staging` bestehen. `contractVersion` SHALL eine ganze Zahl sein, die bei jeder inkompatiblen Änderung steigt; Hinzufügen ist kompatibel, Umbenennen oder Entfernen nicht. Das Schema SHALL unter `platform/schemas/foundation-outputs/` liegen und mindestens beschreiben:

| Ausgabe | Inhalt | Geheim |
| --- | --- | --- |
| `contractVersion`, `environment`, `location`, `resourceGroupName` | Vertrag und Ort | nein |
| `baseDomain`, `ingressIp` | Hosts `<app-id>.<baseDomain>` | nein |
| `aksClusterName`, `kubeconfig` | Cluster und Admin-Kubeconfig | nur Kubeconfig |
| `acrLoginServer`, `acrUsername`, `acrPassword` | Registry | nur Passwort |
| `appInsightsConnectionString` | Monitoring | nein |
| `actionGroupIntakeId`, `actionGroupHumansId` | Alarmziele | nein |
| `operationsApiHost` | `operations-agent.<baseDomain>` | nein |
| `postgresHost`, `postgresAdminUser`, `postgresAdminPassword`, `postgresOperationsDatabase` | Datenbank | nur Passwort |
| `diagnosticsStorageAccount`, `diagnosticsContainer`, `diagnosticsStorageKey` | Diagnoseartefakte | nur Key |
| `alertWebhookKey`, `maintenanceApiKey`, `githubWebhookSecret` | Schutz der Eingänge | ja |
| `modelApiKeys`, `githubAppPrivateKey`, `opsGithubToken` | Modell- und GitHub-Zugang des Agenten: privater Schlüssel (PEM) der GitHub App; `opsGithubToken` ist ein optionaler PAT als Rückfall und leer, wenn kein PAT hinterlegt ist (geändert 2026-10-30 (Erik); war: PAT als einziger GitHub-Zugang; additive Ergänzung, `contractVersion` bleibt 1) | ja |

#### Scenario: Neue Ausgabe ergänzen
- **WHEN** der Foundation-Stack eine zusätzliche Ausgabe erhält
- **THEN** bleibt `contractVersion` gleich und bestehende Verbraucher laufen unverändert weiter

### Requirement: Zugriffswege auf den Ausgabevertrag
Verbraucher SHALL den Vertrag auf einem von drei Wegen lesen: Pulumi-App-Stacks über eine `StackReference`; GitHub-Workflows über die Composite Action `.github/actions/foundation-outputs`; Prozesse im Cluster über Kubernetes-Secrets und -ConfigMaps, die ihr App-Stack aus der `StackReference` anlegt. Die Composite Action SHALL sich nur mit den Repo-Secrets des State-Backends anmelden, Werte als Step-Ausgaben liefern und geheime Werte maskieren.

#### Scenario: Verbraucher gegen Foundation anbinden
- **WHEN** ein App-Stack, ein Build-Workflow oder ein Agentenprozess konfiguriert wird
- **THEN** bezieht er die benötigten Werte über einen der drei Wege
- **AND** müssen keine Azure-Ressourcennamen aus Logs oder dem Portal abgeleitet werden

#### Scenario: Inkompatible Ausgaben
- **WHEN** ein Workflow die Composite Action mit einer anderen Vertragsversion als der veröffentlichten aufruft
- **THEN** bricht die Action vor jeder Änderung mit einer Meldung ab, die erwartete und vorhandene Version nennt

### Requirement: Alarmziel für Apps
Die Plattform SHALL die Action Group `ops-intake` bereitstellen. Sie sendet Alarme im Common Alert Schema an `https://<operationsApiHost>/events/azure-monitor?key=<alertWebhookKey>`.

#### Scenario: Action Group testen
- **WHEN** die Testbenachrichtigung von `ops-intake` ausgelöst wird, während der Ereigniseingang läuft
- **THEN** erreicht ein Request mit Common Alert Schema und gültigem Key den Eingang
- **AND** lehnt der Eingang denselben Request ohne Key ab

### Requirement: Verfügbarkeitstests aus dem Katalog
Der Foundation-Stack SHALL für jeden Eintrag unter `platform/catalog/<app-id>/app.yaml` einen Application-Insights-Standardtest auf `https://<app-id>.<baseDomain><Prüfpfad>` und eine Metrik-Alarmregel `alert-<app-id>-staging-availability` anlegen. Die Regel SHALL an `ops-intake` senden, `app-id` und `environment` als Custom Properties mitgeben und automatische Auflösung aktivieren, damit auch Resolved-Ereignisse entstehen. Ausgenommen ist `operations-agent`: Er wird nur über den `/healthz`-Test an `ops-humans` überwacht, damit er sich nicht selbst alarmiert. Der Ende-zu-Ende-Nachweis mit einem echten Alarm gehört zur gemeinsamen Abnahme P1-09.

#### Scenario: Kein Alarm des Agenten an sich selbst
- **WHEN** der Katalog einen Eintrag `operations-agent` enthält
- **THEN** entsteht für ihn kein Test an `ops-intake`

#### Scenario: AFFiNE nicht erreichbar
- **WHEN** AFFiNE fünf Minuten lang nicht antwortet
- **THEN** sendet `ops-intake` ein Fired-Ereignis mit `app-id` `affine` und `environment` `staging`
- **AND** sendet es nach Wiederherstellung ein Resolved-Ereignis

### Requirement: Benachrichtigungskanal für Menschen
Die Plattform SHALL die Action Group `ops-humans` mit E-Mail an einen dokumentierten Team-Verteiler bereitstellen. Sie SHALL keinen Webhook zum Agenten-Eingang enthalten. Kanal und Empfänger SHALL vor der Staging-Abnahme mit einer Testbenachrichtigung geprüft sein.

#### Scenario: Testbenachrichtigung
- **WHEN** die Testbenachrichtigung von `ops-humans` ausgelöst wird
- **THEN** erhält der Team-Verteiler eine E-Mail
- **AND** erreicht kein Request den Agenten-Eingang

### Requirement: Unabhängiges Signal bei Agentenausfall
Der Foundation-Stack SHALL einen Application-Insights-Standardtest anlegen, der alle fünf Minuten `https://<operationsApiHost>/healthz` aufruft, und bei Fehlschlag an `ops-humans` alarmieren. Der Endpunkt antwortet 503, wenn die Datenbank nicht erreichbar oder der letzte Worker-Heartbeat älter als zehn Minuten ist.

#### Scenario: Worker bleibt ausgefallen
- **WHEN** der Worker zehn Minuten lang keinen Heartbeat schreibt
- **THEN** erhält der Team-Verteiler eine E-Mail mit Umgebung und dem Namen des Tests
- **AND** ist dafür weder ein Modellaufruf noch ein laufender Agent nötig

#### Scenario: Agenten-Release entfernt
- **WHEN** das Agenten-Release vollständig gelöscht wird
- **THEN** bleibt der Verfügbarkeitstest bestehen und schlägt an

## Abgrenzung

A besitzt alle Azure-Ressourcen einschließlich der aus dem Katalog erzeugten Verfügbarkeitstests. B liefert Katalogeinträge, App-Stacks, Ingress-Regeln und App-Datenbanken und veröffentlicht die Agenten-API unter `operationsApiHost`. C liefert den Alarmempfänger mit Key-Prüfung, `/healthz` mit Heartbeat-Auswertung und das Auftrags-/Checkpoint-Schema. D liefert Werkzeuge und Agentenpaket einschließlich ServiceAccount `operations-agent` und ClusterRoleBinding.
