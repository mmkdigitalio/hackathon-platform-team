# Spec Delta

## Purpose

Ein selbst gehosteter Betriebsagent untersucht bekannte AFFiNE-Störungen, führt begrenzte Maßnahmen aus und liefert überprüfbare Ergebnisse an die deterministische Auftragsverwaltung.

## ADDED Requirements

### Requirement: Früher Nachweis mit echtem Modell
Ein lokal ausführbarer Prototyp SHALL gepinnte Paketversionen, ein konfigurierbares Tool-Calling-Modell und ein typisiertes Diagnosewerkzeug verwenden. Der Nachweis SHALL einen echten Modell- und Werkzeugaufruf enthalten, bevor davon abhängige Plattformarbeit integriert wird.

#### Scenario: Prototyp aus sauberer Umgebung ausführen
- **WHEN** dokumentierte Modellkonfiguration und Zugang vorhanden sind
- **THEN** führt der Prototyp einen echten typisierten Werkzeugaufruf aus
- **AND** dokumentiert er Paketstand, Modellkonfiguration und Ergebnis ohne Secret-Werte

### Requirement: Konfigurierbarer Modellanbieter
Der Agent SHALL Azure OpenAI und Anthropic als Anbieter unterstützen. Anbieter, Modell beziehungsweise Deploymentname, Endpunkt und Tokenpreise SHALL Konfiguration sein. API-Keys SHALL aus der Foundation-Ausgabe `modelApiKeys` stammen und weder in Git noch in Prompts oder Logs erscheinen.

#### Scenario: Anbieter wechseln
- **WHEN** die Konfiguration von `anthropic` auf `azure-openai` umgestellt wird
- **THEN** läuft der Agent nach einem Neustart mit dem anderen Anbieter
- **AND** ist dafür keine Codeänderung nötig

### Requirement: Aufrufbare Agentenausführung
Die Agentenausführung SHALL durch den Worker aufrufbar sein und den versionierten Auftrag-/Ergebnisvertrag sowie die Zustands-, Checkpoint-, Sperr- und Aktionsdienste der Auftragsverwaltung verwenden. Sie SHALL keine zweite Auftragsverwaltung und keine eigene Abschlussentscheidung persistieren.

#### Scenario: Worker übergibt Auftrag
- **WHEN** der Worker einen gültig übernommenen Auftrag übergibt
- **THEN** erhält die Agentenausführung App-, Umgebungs-, Kontext- und Wiederaufnahmeinformationen
- **AND** liefert sie strukturierte Befunde, Aktionsreferenzen, Verbrauchsdaten und einen Ergebnisvorschlag über den vereinbarten Vertrag

### Requirement: Versionierter Kontext und untrusted Eingaben
Der Agent SHALL Katalog, Sollzustand, tatsächlichen Release, Runbook und frühere Aktionen versionsbezogen laden. Tickettexte, Alarm-Payloads, Logs und Repository-Inhalte SHALL als Daten gelten und weder Rechte noch Betriebsregeln ändern. Vermutungen SHALL von bestätigtem Wissen unterscheidbar bleiben.

#### Scenario: Log enthält Betriebsanweisung
- **WHEN** ein geladener Logtext zusätzliche Rechte oder eine unbekannte Betriebsaktion verlangt
- **THEN** erweitert der Agent dadurch keine erlaubten Aktionen
- **AND** bleibt die Eingabe als Kontextdaten nachvollziehbar

### Requirement: Diagnose ohne Azure-Zugriff
Der Agent SHALL in Phase 1 ausschließlich aus Alarm-Payload, Kubernetes-Zustand, Events, Logs, eigenen HTTP-Prüfungen und versioniertem Repository-Kontext diagnostizieren. Er SHALL keinen Zugriff auf Azure Resource Manager, Azure-Messwerte oder Log Analytics benötigen.

#### Scenario: Agent ohne Azure-Anmeldung
- **WHEN** der Agent im Cluster ohne Azure-Identität und ohne Azure-Zugangsdaten läuft
- **THEN** kann er einen AFFiNE-Alarm vollständig untersuchen
- **AND** schlägt kein Diagnoseschritt wegen fehlender Azure-Rechte fehl

### Requirement: Begrenzte autonome Maßnahmen
Der Agent SHALL im Katalog erlaubte Maßnahmen ohne zusätzliche menschliche Routinefreigabe ausführen. Erreichte Grenzen oder unbekannte Fehler SHALL zu einem sichtbaren ungelösten Ergebnis führen.

#### Scenario: Bekannter Fehler innerhalb der Grenzen
- **WHEN** eine katalogisierte Reparatur für den AFFiNE-Fehler passt
- **THEN** führt der Agent sie mit gültiger Operations-Berechtigung aus
- **AND** benötigt er keine manuelle Bestätigung

### Requirement: Konfigurierbare Laufgrenzen
Code um die Agentenausführung SHALL je Lauf Laufzeit, Tool-Aufrufe, Reparaturversuche und Modellkosten begrenzen. Startwerte SHALL 10 Minuten, 30 Tool-Aufrufe, 2 Reparaturversuche und etwa 1 USD sein und ohne Codeänderung anpassbar bleiben. Ein Reparaturversuch SHALL eine ändernde Maßnahme mit anschließender Verifikation sein.

#### Scenario: Grenze erreicht
- **WHEN** eine Grenze erreicht ist und keine bestätigte Lösung vorliegt
- **THEN** beendet der Agent weitere Modell- und Werkzeugaufrufe kontrolliert
- **AND** liefert er `needs_attention` mit erreichter Grenze, Befunden und bisherigen Aktionen

#### Scenario: Dritter Reparaturversuch
- **WHEN** zwei Reparaturversuche ohne positive Verifikation abgeschlossen sind
- **THEN** führt der Agent keine weitere ändernde Maßnahme aus

### Requirement: Belegte Operations-Sperre
Ist die Operations-Sperre für App und Umgebung durch eine andere Operation belegt, SHALL der Agent nicht warten. Er SHALL den Lauf ohne ändernde Maßnahme mit `needs_attention` und dem Grund „gesperrt durch Operation X“ beenden.

#### Scenario: Deployment läuft bereits
- **WHEN** ein Deploy-Workflow die Sperre für `affine` in `staging` hält und der Agent eine Reparatur beginnen will
- **THEN** startet der Agent weder Neustart noch Dispatch
- **AND** nennt das Ergebnis die `operationId` des Sperrbesitzers

### Requirement: Bekannte Reparatur des AFFiNE-Deployments
Weicht das AFFiNE-Deployment vom Sollzustand ab, weil es auf 0 Replicas skaliert ist, SHALL der Agent den Sollzustand per Dispatch von `app-deploy.yml` mit `operationId` und `actionId` wiederherstellen, den Lauf bis zum Abschluss verfolgen und danach verifizieren.

#### Scenario: AFFiNE auf 0 Replicas
- **WHEN** ein Verfügbarkeitsalarm für `affine` eintrifft und das Deployment 0 Replicas hat, der Sollzustand aber mehr
- **THEN** startet der Agent `app-deploy.yml` mit der `operationId` des Auftrags und einer neuen `actionId`
- **AND** schlägt er `resolved` erst nach erfolgreichem Lauf und positivem Smoke-Test vor

### Requirement: Nicht reparierbarer Sollzustand
Entspricht der Istzustand dem Sollzustand und deuten Logs oder Events auf einen Konfigurationsfehler, SHALL der Agent ein erneutes Deployment nicht als Reparatur werten. Bleibt die Verifikation nach erlaubten Maßnahmen negativ, SHALL er `needs_attention` mit SHA, Autor und Zeitpunkt des letzten Commits auf die Sollzustandsdatei liefern.

#### Scenario: Ungültiger Konfigurationswert per Commit
- **WHEN** ein Commit einen ungültigen AFFiNE-Konfigurationswert in den Sollzustand gesetzt hat und ausgerollt wurde
- **THEN** behauptet der Agent keinen Erfolg
- **AND** verweist das Ergebnis `needs_attention` auf diesen Commit und die betroffenen Logzeilen

### Requirement: Keine Scheinlösung
Nach einer Maßnahme SHALL der Agent den unabhängigen Smoke-Test und den aktuellen Kubernetes-Zustand auswerten. Ein erfolgreicher Werkzeugaufruf, ein gestarteter Workflow oder ein zurückgesetzter Alarm SHALL nicht allein als Reparaturnachweis gelten.

#### Scenario: Echter Reparaturnachweis
- **WHEN** der AFFiNE-Fehler über echte Schnittstellen repariert wurde
- **THEN** liefert der Agent ein positives Smoke-Test-Ergebnis und den aktuellen Kubernetes-Zustand an die Auftragsverwaltung
- **AND** kann die Auftragsverwaltung erst damit den Vorfall abschließen

#### Scenario: Test schlägt weiter fehl
- **WHEN** erlaubte Maßnahmen ausgeführt wurden, der Smoke-Test aber weiter fehlschlägt
- **THEN** liefert der Agent ein ungelöstes Ergebnis mit konkreten Befunden

### Requirement: Wiederaufnahme ohne blindes Wiederholen
Bei Wiederaufnahme SHALL der Agent Checkpoints und Aktionsprotokoll gemeinsam berücksichtigen. Gestartete oder unklare Maßnahmen SHALL vor erneutem Ausführen abgeglichen werden; ein bekannter Workflow-Lauf SHALL weiterbeobachtet werden.

#### Scenario: Neustart während Workflow
- **WHEN** der Worker nach einem Neustart denselben Auftrag mit bekanntem Workflow-Lauf übergibt
- **THEN** setzt der Agent die Beobachtung dieses Laufs fort
- **AND** startet er keinen Ersatzlauf allein wegen des Neustarts

### Requirement: Agentenpaket aus einem Image
Das Agentenpaket SHALL aus einem unveränderlichen Image und einem versionierten Helm-Chart bestehen. Der Chart SHALL API und Worker als getrennte Deployments aus demselben Image starten und einen Service für die API enthalten. Secrets und Ingress-Regel SHALL nicht Teil des Charts sein.

#### Scenario: Paket ausrollen
- **WHEN** der Deploy-Workflow den Chart mit Image-Digest und Chart-Version aus dem Sollzustand ausrollt
- **THEN** laufen API und Worker als getrennte Deployments mit demselben Image-Digest
- **AND** muss der Deploy-Workflow weder Prozesse noch Chart-Struktur selbst definieren

### Requirement: Migration als Helm-Hook
Der Chart SHALL die Migrations-CLI der Auftragsverwaltung als Helm-Hook-Job mit `pre-install,pre-upgrade` ausführen. API und Worker SHALL beim Start nur die Schema-Version prüfen und ohne passende Version nicht bereit werden.

#### Scenario: Upgrade mit neuer Migration
- **WHEN** eine Agentenversion mit neuer Migration ausgerollt wird
- **THEN** läuft die Migration genau einmal im Hook-Job vor dem Start der neuen Pods
- **AND** führen API und Worker selbst keine Schemaänderung aus

### Requirement: Konfiguration aus dem App-Stack
Der Chart SHALL Konfiguration und Secrets aus genau einem Kubernetes-Secret lesen, das der App-Stack aus den Foundation-Ausgaben anlegt. Name dieses Secrets und Name des Image-Pull-Secrets SHALL Chart-Werte sein. Hook-Job, API und Worker SHALL dieselben Werte verwenden.

#### Scenario: Secret-Namen vom App-Stack
- **WHEN** der App-Stack Secret- und Image-Pull-Secret-Namen als Werte übergibt
- **THEN** ziehen alle Pods das Image mit diesem Pull-Secret und lesen Datenbank-, Modell- und GitHub-Zugang aus dem Secret
- **AND** enthalten Chart und Werte-Dateien keine Secret-Werte

### Requirement: Image-Build ohne Azure-Identität
Ein Workflow unter `.github/workflows/agent-*.yml` SHALL das Image mit dem ACR-Admin-User aus der Composite Action `foundation-outputs` nach ACR pushen und den Chart veröffentlichen. Danach SHALL er den wiederverwendbaren Workflow `app-update-desired-state.yml` für `platform/environments/staging/operations-agent.yaml` aufrufen.

#### Scenario: Merge auf main
- **WHEN** eine Änderung unter `operations-agent/` auf `main` landet und die Prüfungen bestehen
- **THEN** liegen Image mit Digest und Chart-Version in ACR
- **AND** enthält der Sollzustand des Agenten danach diesen Digest und diese Chart-Version, ohne dass der Workflow eine Azure-Anmeldung nutzt

## Abgrenzung

D besitzt Agentenausführung, Image, Chart und Build-Workflow. C liefert Auftragsverwaltung, Zustandsregeln, Migrations-CLI, Sperre und Persistenz; A liefert Foundation-Ausgaben und Composite Action; B liefert App-Stack des Agenten, Sollzustand, Sollzustands- und Deploy-Workflow. Der Paketbau ist kein zweiter Deploymentpfad.
