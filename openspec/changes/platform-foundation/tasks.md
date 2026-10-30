# Tasks

A kann Grundgerüste, Verträge und Foundation-Code sofort vorbereiten. Größere davon abhängige Plattformarbeit setzt D's echten P1-02-Nachweis voraus. App-Deployment, Migrationen und Agentenpaket kommen aus B, C und D; siehe [Abgrenzung](proposal.md#out-of-scope-und-lieferungen-anderer-stränge).

## 1. Repository-Grundlage und Verträge, P1-01

- [ ] 1.1 Die drei Repository-Grundgerüste und Dateizuständigkeiten bereitstellen; Abschluss durch vorhandene Gerüste und dokumentierte Übergabe an B, C und D prüfen.
- [ ] 1.2 App-IDs, erste Umgebung `staging`, Stack-Namen und unveränderliche Release-Konventionen festlegen; Abschluss anhand je eines konsistenten Beispiels für alle drei Repositories prüfen.
- [ ] 1.3 Foundation-Ausgabeschema v0.1 mit nicht geheimem Beispiel liefern; Abschluss durch Versionsvalidierung bei B und Konfigurationsprüfung bei C/D prüfen.
- [ ] 1.4 Die ältere README im Implementierungsstart auf den Produktplan ausrichten und Bootstrap-Voraussetzungen dokumentieren; Abschluss durch widerspruchsfreien Stack-Verweis und nachvollziehbare Startanleitung prüfen.

## 2. Bootstrap und Zugänge, P1-03

- [ ] 2.1 D's echten Modell-/Werkzeugnachweis als Integrationsvoraussetzung referenzieren; Abschluss durch ein auffindbares Prototypprotokoll mit Paketstand prüfen.
- [ ] 2.2 State-Backend und geschützte Verschlüsselungskonfiguration initialisieren; Abschluss durch erfolgreichen Zugriff und erneuten Bootstrap ohne ersetzte State-Daten prüfen.
- [ ] 2.3 GitHub-OIDC sowie benötigte Azure-, Kubernetes- und Datenidentitäten einrichten; Abschluss durch getrennte reale Zugriffsprüfungen ohne Secret-Ausgaben prüfen.
- [ ] 2.4 GitHub-Identität für repositoryübergreifende Release-Änderungen und Webhook-Einrichtung an B/C übergeben; Abschluss durch autorisierten Testzugriff im vereinbarten Repository prüfen.
- [ ] 2.5 Bootstrap, Wiederholung und Secret-Wiederherstellung dokumentieren; Abschluss durch Ausführen der dokumentierten Kommandos mit nachvollziehbaren Ausgaben prüfen.

## 3. Gemeinsame Laufzeit, P1-04

- [ ] 3.1 Region, Kapazität und unterstützte AKS-Version als Konfiguration festhalten und Cluster/Registry bereitstellen; Abschluss durch reproduzierbaren Pulumi-Lauf und erfolgreiche Registry-/Cluster-Prüfung prüfen.
- [ ] 3.2 Gemeinsames Netzwerk, Ingress/HTTPS und Namespace-Konventionen bereitstellen; Abschluss durch erreichbare Testadresse und dokumentierten Verbindungsweg prüfen.
- [ ] 3.3 Log Analytics und gemeinsame Monitoring-Anbindung einrichten; Abschluss durch auffindbare aktuelle Staging-Logs und Messwerte prüfen.
- [ ] 3.4 Getrennten Foundation-Workflow liefern und Upgrade-/Wiederanlaufverfahren dokumentieren; Abschluss durch erneuten Lauf ohne aktive Agentenprozesse und ohne unbeabsichtigte App-Ressourcenübernahme prüfen.

## 4. Externe Persistenz und Übergabe, Anteil P1-08

- [ ] 4.1 Externes Agenten-PostgreSQL und Diagnoseartefakt-Speicher mit geschützten Verbindungsreferenzen liefern; Abschluss durch reale Daten-/Artefaktzugriffe und unveränderte Ressourcen nach App-Prozessneustart prüfen.
- [ ] 4.2 Runner- und Workload-Netzpfade für Cluster und Operations-Speicher einrichten; Abschluss durch C's standalone Client aus einem tatsächlichen GitHub-Runner ohne laufende Agenten-API prüfen.
- [ ] 4.3 Foundation-Ausgaben und Zugangsverfahren an B/C/D übergeben; Abschluss durch Verbraucherprüfung der Version und dokumentierte Abgrenzung zwischen Infrastruktur und C's Migrationen prüfen.

## 5. Alarmtransport und Agentenüberwachung, Anteil P1-09

- [ ] 5.1 B's Demo-App-Prüfreferenzen und C's Empfänger/Auth-Vertrag verdrahten; Abschluss durch einen echten Fired- und Resolved-Alarm mit korrekter App-/Umgebungszuordnung prüfen.
- [ ] 5.2 Unabhängige Agenten-Ausfallmeldung für API und Worker konfigurieren; Abschluss durch kontrollierten Ausfall und tatsächlich empfangene separate Benachrichtigung prüfen.
- [ ] 5.3 Monitoring-Zuordnung, Empfänger und Diagnoseverfahren dokumentieren; Abschluss durch Nachverfolgung eines Alarms bis zu C's Auftragsreferenz prüfen.

## 6. Gemeinsame Integration, Anteil P1-12

- [ ] 6.1 Abnahmeszenario 1 federführend mit B/C/D nachweisen; Abschluss durch dokumentierte Staging-Bereitstellung mit URLs, Stack-Namen, Versionen und ohne manuelle Ressourcenreparatur prüfen.
- [ ] 6.2 Bei gestopptem Agenten den unabhängigen Foundation-Wiederanlauf praktisch durchführen; Abschluss durch erfolgreichen Workflow und weiterhin vorhandene externe Persistenz prüfen.
