# Spec Delta

## Purpose

Typisierte Betriebswerkzeuge verbinden den Agenten mit Kubernetes, GitHub und der laufenden App und machen Diagnose, Änderungen, externe Ausführungen und Verifikation nachvollziehbar.

## ADDED Requirements

### Requirement: Typisierte und nachvollziehbare Werkzeugergebnisse
Werkzeuge SHALL validierte Eingaben und strukturierte Ergebnisse mit Ziel, Status, Zeitbezug und Evidenzreferenzen liefern. Fehler, Zeitüberschreitungen und fehlende Berechtigungen SHALL von erfolgreichen Ergebnissen unterscheidbar sein. Aufrufe SHALL mit Auftrag und, soweit zutreffend, `operationId` und `actionId` verknüpft sein.

#### Scenario: Kubernetes-Zugriff verweigert
- **WHEN** ein Werkzeug wegen fehlender Berechtigung keine Logs lesen kann
- **THEN** meldet es einen strukturierten Zugriffsfehler
- **AND** gibt es weder leere Logs als Erfolg noch einen erfundenen Systemzustand zurück

### Requirement: Kubernetes-Zugang über ServiceAccount
Kubernetes-Werkzeuge SHALL ausschließlich den ServiceAccount `operations-agent` verwenden, den das Agenten-Chart mit ClusterRole und ClusterRoleBinding anlegt. Die Rolle SHALL nur Lesen von Workloads, Pods, Logs, Events und Jobs, `patch` auf Deployments sowie Anlegen und Löschen von Jobs erlauben.

#### Scenario: Agent im Cluster
- **WHEN** der Agent im Namespace `operations-agent-staging` läuft
- **THEN** greifen seine Kubernetes-Werkzeuge mit dem Token des ServiceAccounts `operations-agent` zu
- **AND** benötigt er weder Admin-Kubeconfig noch Workload Identity

### Requirement: Zielbeschränkung auf Katalog-Namespaces
Kubernetes-Werkzeuge SHALL Ziele im Code auf Namespaces `<app-id>-<env>` von Apps aus dem Katalog beschränken, obwohl die Bindung clusterweit gilt.

#### Scenario: Fremder Namespace
- **WHEN** ein Werkzeugaufruf einen Pod in `kube-system` neu starten soll
- **THEN** lehnt das Werkzeug den Aufruf vor jedem Kubernetes-Request ab
- **AND** meldet es einen strukturierten Validierungsfehler

### Requirement: Kubernetes-Diagnose
Werkzeuge SHALL Deployment- und Pod-Zustand, Replica-Zahlen, Image-Digest, Neustartzähler, Events und begrenzte Logausschnitte einer App mit Zeitbezug lesen können.

#### Scenario: AFFiNE ohne Pods
- **WHEN** der Agent das AFFiNE-Deployment in `affine-staging` untersucht
- **THEN** erhält er Soll- und bereite Replicas, zugehörige Events und die letzten Logzeilen mit Zeitraum
- **AND** sind die Angaben mit Namespace, Ressourcenname und Abfragezeitpunkt belegt

### Requirement: Diagnose aus Alarm, Kubernetes und HTTP
Die Diagnose SHALL Alarm-Payload, Kubernetes-Zustand, Events, Logs und eigene HTTP-Prüfungen kombinieren. Werkzeuge für Azure Resource Manager, Azure-Messwerte oder Log Analytics SHALL in Phase 1 nicht existieren. Diese Abweichung von P1-07 SHALL in den Agenten-Betriebsregeln stehen.

#### Scenario: Alarm untersuchen
- **WHEN** der Agent einen echten Verfügbarkeitsalarm für `affine` untersucht
- **THEN** wertet er Regelname, Zeitpunkt und Testergebnis aus der Alarm-Payload, den Kubernetes-Zustand und eine eigene HTTP-Prüfung aus
- **AND** dokumentiert er Zeiträume und Ressourcenreferenzen jeder Quelle

### Requirement: Eigene HTTP-Prüfungen
Ein Werkzeug SHALL den Prüfpfad aus dem Katalog unter `<app-id>.<baseDomain>` aufrufen und Statuscode, Dauer, Zeitpunkt und Fehlerart liefern. Es SHALL nur Hosts unter `baseDomain` aufrufen.

#### Scenario: App nicht erreichbar
- **WHEN** AFFiNE auf den Prüfpfad mit 503 antwortet
- **THEN** liefert das Werkzeug Status 503, Dauer und Zeitpunkt als Befund
- **AND** wertet es das nicht als Werkzeugfehler

### Requirement: Kontrollierter Neustart
Ein Werkzeug SHALL einen im Katalog erlaubten Neustart auslösen, indem es am Pod-Template die Annotation `ops.mmk/action-id=<actionId>` setzt. Vorher SHALL es einen Aktionsdatensatz anlegen und die gemeinsame Operations-Sperre erwerben.

#### Scenario: AFFiNE neu starten
- **WHEN** ein erlaubter Neustart für `affine` in `staging` mit gültigem Operations-Besitz ausgeführt wird
- **THEN** trägt das Pod-Template die `actionId` und ein Rollout beginnt
- **AND** protokolliert das Werkzeug Ziel-Deployment, `actionId` und Rollout-Referenz

#### Scenario: Neustart ohne gültigen Besitz
- **WHEN** die Sperre nicht erworben wurde oder der Besitz verloren ging
- **THEN** wird kein ändernder Kubernetes-Aufruf ausgeführt
- **AND** meldet das Werkzeug den Konflikt mit der `operationId` des Besitzers

### Requirement: Abgleich eines Neustarts
Der Abgleich SHALL anhand der Annotation `ops.mmk/action-id` am Pod-Template entscheiden, ob ein Neustart bereits lief. Trägt das Template die gesuchte `actionId`, SHALL der Abgleich nur den Rollout beobachten; sonst gilt die Aktion als nicht ausgeführt.

#### Scenario: Worker-Neustart nach Patch
- **WHEN** der Worker nach einem Neustart eine Neustart-Aktion ohne bestätigtes Ergebnis wieder aufnimmt und das Template ihre `actionId` trägt
- **THEN** patcht das Werkzeug das Deployment nicht erneut
- **AND** setzt es die Beobachtung des Rollouts fort

### Requirement: GitHub-Zugang über PAT
GitHub-Werkzeuge SHALL den Fine-grained PAT aus der Foundation-Ausgabe `opsGithubToken` verwenden. Eine GitHub App SHALL nicht vorausgesetzt werden.

#### Scenario: Token fehlt die Berechtigung
- **WHEN** der PAT keine Actions-Rechte besitzt und ein Dispatch angefordert wird
- **THEN** meldet das Werkzeug einen strukturierten Berechtigungsfehler mit dem fehlenden Recht

### Requirement: Versionierten Kontext aus GitHub lesen
Werkzeuge SHALL Dateien und Commit-Historie aus dem Monorepo zu einer Git-Referenz lesen können, insbesondere Katalog, Sollzustand und Runbook-Verweise. Für eine Sollzustandsdatei SHALL der letzte Commit mit SHA, Autor, Zeitpunkt und Diff abrufbar sein.

#### Scenario: Letzte Sollzustandsänderung
- **WHEN** der Agent `platform/environments/staging/affine.yaml` untersucht
- **THEN** erhält er den letzten Commit auf diese Datei mit SHA, Autor, Zeitpunkt und geändertem Wert

### Requirement: Issue-Rückmeldung mit Markierung
Werkzeuge SHALL Befunde, Rückfragen und Ergebnisse im ursprünglichen Support-Issue kommentieren können. Jeder Kommentar SHALL `<!-- mmk-ops-agent -->` und die Auftragsreferenz enthalten, damit der Schleifenschutz ihn unabhängig vom Autor erkennt.

#### Scenario: Supportmeldung beantworten
- **WHEN** ein Auftrag aus einem GitHub-Issue untersucht wurde
- **THEN** erscheint im ursprünglichen Issue ein Kommentar mit Befund, Auftragsreferenz und Markierung
- **AND** erzeugt dieser Kommentar keinen neuen Auftrag

### Requirement: Vorgegebene Workflows starten und verfolgen
Ein Werkzeug SHALL nur Workflows aus einer konfigurierten Liste per Dispatch mit Eingabe `operationId` starten. Es SHALL vor dem Dispatch einen Aktionsdatensatz anlegen, den zugehörigen Lauf zuordnen, dessen Lauf-ID speichern und den tatsächlichen Abschluss abrufen. Ein Dispatch SHALL nicht als abgeschlossenes Deployment gelten.

#### Scenario: Workflow läuft noch
- **WHEN** ein Workflow gestartet wurde und noch kein Abschluss vorliegt
- **THEN** liefert das Werkzeug einen laufenden oder noch zuzuordnenden Status
- **AND** meldet es weder Deployment-Erfolg noch behobenen Vorfall

#### Scenario: Nicht gelisteter Workflow
- **WHEN** ein Dispatch für einen Workflow außerhalb der Liste angefordert wird
- **THEN** lehnt das Werkzeug ihn ohne GitHub-Aufruf ab

### Requirement: Eindeutige Zuordnung von Workflow-Läufen
Jeder Dispatch SHALL `operationId` und `actionId` als Workflow-Eingaben übergeben. Ein Lauf SHALL nur über gespeicherte Lauf-ID oder die im `run-name` sichtbare `actionId` zugeordnet werden. Ein nur zeitlich ähnlicher Lauf SHALL nie genügen.

#### Scenario: Bestätigten Lauf wiederfinden
- **WHEN** für eine gespeicherte Aktion bereits ein externer Lauf existiert
- **THEN** ruft der Adapter genau diesen Lauf über Lauf-ID oder `actionId` ab
- **AND** startet er keinen weiteren Lauf

### Requirement: Keine Repository-Änderungen durch den Agenten
Der Agent SHALL in Phase 1 keine Branches, Commits, Pull Requests oder Merges erzeugen und Pulumi oder Helm nicht aus seiner Sitzung ausführen. Dauerhafte Änderungen SHALL ausschließlich über vorgegebene Workflows und den versionierten Sollzustand erfolgen.

#### Scenario: Reparatur braucht Konfigurationsänderung
- **WHEN** die Diagnose ergibt, dass nur eine Änderung am Sollzustand hilft
- **THEN** erzeugt der Agent keinen Commit und keinen Pull Request
- **AND** liefert er `needs_attention` mit der nötigen Änderung als Befund

### Requirement: Unklare externe Ausgänge abgleichen
Jeder ändernde Adapter SHALL einen Abgleich für unklare Ergebnisse unterstützen. Ist ein Aufruf möglicherweise ausgeführt worden, SHALL vor Wiederholung dessen externer Zustand geprüft werden. Ein nicht eindeutig aufklärbarer Ausgang SHALL zu `needs_attention` führen.

#### Scenario: Timeout nach Dispatch
- **WHEN** ein Dispatch möglicherweise angenommen wurde, die Antwort aber verloren ging
- **THEN** sucht der Adapter einen nicht zugeordneten Lauf mit derselben `operationId`
- **AND** startet er ohne eindeutige Klärung keinen weiteren Lauf

#### Scenario: Mehrere passende Läufe
- **WHEN** nach einem Neustart mehrere nicht zugeordnete Läufe dieselbe `operationId` tragen
- **THEN** wählt der Adapter keinen davon aus
- **AND** endet der Auftrag mit `needs_attention` und den Lauf-IDs

### Requirement: Smoke-Test als Kubernetes-Job
Das Verifikationswerkzeug SHALL das Smoke-Test-Image aus dem Katalog als Kubernetes-Job im App-Namespace mit `TARGET_URL`, den Werten aus `smokeTest.env` und den Test-Secrets aus `smokeTest.secrets` (per `secretKeyRef` auf `<app-id>-platform`) starten. Referenzen der Form `acr:<repo>@sha256:…` SHALL es mit dem ACR-Login-Server der Foundation auflösen. Es SHALL Exit-Code und JSON-Ergebnis auf stdout auswerten und Testergebnisse nicht durch Modellbewertungen ersetzen.

#### Scenario: AFFiNE nach Reparatur prüfen
- **WHEN** AFFiNE repariert wurde
- **THEN** läuft der Smoke-Test-Job mit `TARGET_URL=https://affine.<baseDomain>` gegen die reale Staging-App
- **AND** enthält das Ergebnis Exit-Code, JSON-Ergebnis, Jobname und Ausführungszeitraum

#### Scenario: Job endet nicht
- **WHEN** der Job innerhalb der Laufzeitgrenze keinen Exit-Code liefert
- **THEN** meldet das Werkzeug eine Zeitüberschreitung und keinen Erfolg

### Requirement: Verifikationsnachweis mit Kubernetes-Zustand
Das Verifikationswerkzeug SHALL zum Smoke-Test-Ergebnis den aktuellen Kubernetes-Zustand ergänzen: bereite Replicas, Image-Digest, Neustartzähler und Rollout-Status. Der Nachweis SHALL `appId`, `environment`, tatsächlichen Release, `checkRef`, Zeitraum, Ergebnis und Evidenzreferenzen enthalten.

#### Scenario: Nachweis an die Auftragsverwaltung
- **WHEN** der Smoke-Test nach einer Maßnahme abgeschlossen ist
- **THEN** erhält die Auftragsverwaltung den Nachweis mit Kubernetes-Zustand
- **AND** liegt dessen Ausführungszeitraum nach der Maßnahme

## Abgrenzung

D implementiert die Adapter, den Abgleich und RBAC im Agenten-Chart. A liefert Foundation-Ausgaben, `opsGithubToken` und `modelApiKeys`; B liefert Katalog, Sollzustand, Deploy-Workflow mit `operationId` und den Smoke-Test-Vertrag, der AFFiNE-Kollege das Smoke-Test-Image; C liefert Aktionsprotokoll, Sperrclient, Zustandsregeln und Schleifenschutz und erzwingt den Abgleich vor weiteren Änderungen.
