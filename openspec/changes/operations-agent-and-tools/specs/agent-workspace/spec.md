# Spec Delta

## Purpose

Der Agent bekommt je Operation einen isolierten Workspace mit Shell und den Werkzeugen eines Plattformentwicklers. Darin kann er Cluster, Azure und GitHub erkunden, Repositories klonen, Code ändern und prüfen. Ändernde Maßnahmen bleiben an die protokollierten Werkzeuge gebunden. (Ergänzung vom 30.10.2026, Cornelius; ersetzt für den Workspace die Einschränkung aus E6.)

## ADDED Requirements

### Requirement: Workspace-Pod je Operation
Der Worker SHALL für eine Operation bei Bedarf genau einen Workspace-Pod `ops-ws-<operationId>` im Namespace des Agenten anlegen. Der Pod SHALL das Toolbox-Image mit festem Digest verwenden, ohne Root-Rechte und ohne Capabilities laufen, ein begrenztes Arbeitsverzeichnis `/workspace` haben und nach Ablauf seiner TTL von Kubernetes beendet werden. Der Worker SHALL den Pod am Ende des Laufs löschen; ein laufender Pod derselben Operation SHALL nach einem Worker-Neustart wiederverwendet werden.

#### Scenario: Erster Shell-Befehl eines Laufs
- **WHEN** das Modell im Lauf zum ersten Mal `execute` oder ein Dateiwerkzeug aufruft
- **THEN** legt der Worker den Workspace-Pod an und wartet, bis er bereit ist
- **AND** meldet er Image-Pull- oder Startfehler als strukturierten Fehler statt als leere Ausgabe

#### Scenario: Offener Workflow-Lauf oder Besitzverlust (geändert 31.10.2026; war: Pod wird am Laufende immer gelöscht)
- **WHEN** der Lauf endet, während ein eigener Workflow-Lauf offen ist oder der Operations-Besitz verloren ging
- **THEN** löscht der Worker den Pod nicht
- **AND** bleibt er für die Wiederaufnahme bis zum Ablauf der TTL stehen

#### Scenario: Worker-Neustart während der Arbeit
- **WHEN** der Worker nach einem Neustart denselben Auftrag wieder übernimmt und der Pod der Operation noch läuft
- **THEN** arbeitet der Agent in derselben Arbeitskopie weiter
- **AND** gehen nicht gepushte Änderungen nicht verloren

### Requirement: Werkzeuge im Workspace
Das Toolbox-Image SHALL mindestens git, gh, kubectl, helm, az, python3 mit uv, Node.js mit npm, jq, yq und ripgrep in festen Versionen enthalten. Das Modell SHALL im Workspace die SDK-Werkzeuge `execute`, `ls`, `read_file`, `write_file`, `edit_file`, `glob` und `grep` nutzen. Jeder Befehl SHALL eine Zeitgrenze (Standard 300 s, höchstens 1800 s) und eine Ausgabegrenze haben. Ausgaben SHALL vor der Rückgabe um bekannte Secrets bereinigt werden.

#### Scenario: Hängender Befehl
- **WHEN** ein Befehl die Zeitgrenze überschreitet
- **THEN** wird er beendet und das Ergebnis nennt Exit-Code 124 und die Zeitüberschreitung
- **AND** läuft der Auftrag weiter

### Requirement: Lesen breit, Ändern nur über protokollierte Werkzeuge
Die Shell im Workspace SHALL nur Leserechte haben: Kubernetes clusterweit lesend ohne Secrets und ohne `pods/exec`, GitHub über ein Installations-Token mit reinen Leserechten, Azure über eine Workload Identity mit Leserrolle, sofern die Foundation eine bereitstellt. Push, Pull Request, Issue-Kommentar, Neustart und Workflow-Dispatch SHALL ausschließlich über die typisierten Werkzeuge mit Aktionsprotokoll und Operations-Sperre laufen. Der schreibende GitHub-Token SHALL nur für den einzelnen Push-Aufruf in den Workspace gelangen.

#### Scenario: Push aus der Shell
- **WHEN** das Modell im Workspace `git push` ausführt
- **THEN** scheitert der Push an fehlenden Schreibrechten des Lese-Tokens
- **AND** gelingt derselbe Stand über `commit_and_push` auf `agent/<operationId>` mit Aktionsdatensatz

#### Scenario: PAT-Rückfall (geändert 31.10.2026)
- **WHEN** keine GitHub App konfiguriert ist und der PAT als Rückfall dient
- **THEN** lässt sich das Token der Shell nicht auf Leserechte einschränken
- **AND** bleibt die Grenze für Änderungen allein die Werkzeugbindung (Betriebsregeln, `commit_and_push` mit Aktionsdatensatz)

#### Scenario: Azure-Leseidentität (geändert 31.10.2026; war: „Workload Identity“ ohne Ausführung)
- **WHEN** der Workspace eine konfigurierte Azure-Client-ID hat
- **THEN** bekommt der Pod ein projiziertes ServiceAccount-Token (Audience `api://AzureADTokenExchange`) ohne AKS-Webhook, und `az-login` meldet die Managed Identity mit Reader und Log Analytics Reader an
- **AND** beendet sich `az-login` ohne Client-ID mit Exit-Code 2, sodass die Diagnose ohne Azure weiterläuft (Stand 31.10.2026: Identität in Staging per az CLI angelegt, Pulumi-Übernahme offen; siehe design.md, Entscheidung 13)

#### Scenario: Ändernder kubectl-Befehl
- **WHEN** das Modell `kubectl scale` oder `kubectl delete` ausführt
- **THEN** verweigert Kubernetes den Aufruf für den ServiceAccount des Workspace

### Requirement: Arbeitskopie im Workspace
Ist ein Workspace aktiv, SHALL `clone_repository` die Arbeitskopie unter `/workspace/repos/<owner>__<name>` auf dem Branch `agent/<operationId>` anlegen, und `repo_diff`, `commit_and_push` und `open_pull_request` SHALL auf derselben Arbeitskopie arbeiten, die das Modell mit den Shell- und Dateiwerkzeugen ändert. Die Grenzen für Repository-Änderungen (freigegebene Repositories, gesperrte Pfade, kein Push auf den Standard-Branch, kein Merge) SHALL unverändert gelten.

#### Scenario: Codeänderung mit Test
- **WHEN** der Agent eine Ursache im Anwendungs- oder Infrastrukturcode findet
- **THEN** ändert er die Dateien im Workspace, führt die passenden Tests aus und pusht über `commit_and_push`
- **AND** nennt der Pull Request die ausgeführten Prüfungen und ihr Ergebnis

### Requirement: Nachvollziehbare Shell-Nutzung
Aufrufe von SDK-Werkzeugen im Workspace SHALL im Laufnachweis mit Name, Ziel (Befehl oder Pfad, bereinigt), Dauer und Status erscheinen. Ergebnisse von `execute` SHALL eine Evidenzreferenz `workspace://<pod>/execute/<n>` tragen, die Befunde als `confirmed` zitieren dürfen.

#### Scenario: Befund aus einer Azure-Abfrage
- **WHEN** der Agent mit `az monitor metrics list` einen Befund belegt
- **THEN** zitiert der Befund die Evidenzreferenz des Befehls
- **AND** steht der Befehl im Laufnachweis unter `GET /runs/{auftragsId}`

### Requirement: Grenzen für Entwicklungsarbeit
Die Standardgrenzen in Staging SHALL Entwicklungsarbeit erlauben: 1800 s Laufzeit, 150 Werkzeugaufrufe, 5 USD Modellkosten und weiterhin 2 Reparaturversuche. Die Grenzen bleiben über die Chart-Werte änderbar.

#### Scenario: Grenze erreicht
- **WHEN** ein Lauf die Werkzeuggrenze erreicht
- **THEN** endet er mit `needs_attention` und den bisherigen Befunden
