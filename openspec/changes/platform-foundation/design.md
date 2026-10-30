# Design

## Context

Das Repository enthält Produktplanung und OpenSpec-Gerüste, noch keine Plattformimplementierung. Motivation und Umfang stehen im [Proposal](proposal.md). Die vorgesehenen Ziel-Repositories sind getrennt; diese Planung verwendet ihre Pfade als Zuständigkeitsgrenzen.

Anforderungen stehen in [platform-bootstrap](specs/platform-bootstrap/spec.md) und [shared-platform-runtime](specs/shared-platform-runtime/spec.md). Die [gemeinsame Übersicht](../../PARALLEL-IMPLEMENTATION.md) definiert Verträge und Integrationsreihenfolge.

## Goals / Non-Goals

Goals:

- Foundation und Bootstrap bleiben unabhängig von App- und Agenten-Releases ausführbar.
- Verbraucher können gegen versionierte Ausgabe-Beispiele entwickeln, bevor Azure bereitsteht.
- Ressourcen besitzen eindeutige Stack-Eigner und überprüfte reale Zugriffswege.

Non-Goals:

- Keine App-Ressourcen im Foundation-Stack und keine Anwendungstabellen in Infrastrukturcode.
- Kein zweiter Deploymentmechanismus und keine laufende Agenten-API als Bootstrap-Voraussetzung.
- Out of scope bleiben B's App-Modul, C's Zustandsverwaltung und D's Agentenpaket; ihre Lieferungen stehen im Proposal.

## Decisions

### 1. Bootstrap vor Foundation, eigener Besitz für App-Stacks

A dokumentiert und automatisiert zuerst State-Backend, Verschlüsselungsreferenzen und initiale Identitäten. Der TypeScript-Foundation-Stack verwendet anschließend Azure-Native- und Kubernetes-Bausteine. B's App-Stacks referenzieren ihn und besitzen ihre eigenen Ressourcen. Ein gemeinsamer Stack für alles würde App-Releases an Foundation-Änderungen koppeln und wird nicht verwendet.

A integriert Repository-Grundgerüste und gemeinsame Root-Konfiguration aus P1-01. B und D erhalten ihre Paket-/Workflow-Bereiche, C und D ein gemeinsames Agenten-Codepaket. Bereits beim Gerüst wird die ältere README als historische Diskussion gekennzeichnet und auf den Produktplan verwiesen.

### 2. Foundation-Vertrag zuerst veröffentlichen

Die Ausgaben aus der gemeinsamen Übersicht werden als versioniertes Schema mit nicht geheimem Beispiel veröffentlicht. Das Schema liegt im Foundation-Bereich und wird von A gepflegt. Verbraucher prüfen die Version, statt Ressourcennamen selbst herzuleiten. Konkrete Azure-Namen, Region und Größen sind Eingabekonfiguration; sensible Verbindungen bleiben geschützt.

### 3. Externe Persistenz und dokumentierte Erreichbarkeit

Agenten-PostgreSQL liegt außerhalb des Clusters. A besitzt Server, Datenbankressource, Identitäten und Netzwerk, C besitzt Tabellen, Migrationen und Operations-Client. Diagnoseartefakte liegen in Blob Storage.

Für Staging wird ein ausführbarer Runner-Netzpfad gewählt und tatsächlich gegen AKS und Operations-Speicher geprüft. Die konkrete Netzkonfiguration hängt vom Abonnement ab und wird als Bootstrap-Konfiguration festgehalten. Ein angenommener Zugriffsweg reicht nicht. Die standalone Nutzung von C's Client vermeidet eine Abhängigkeit vom laufenden Agenten beim ersten Agenten-Release.

### 4. Monitoring gemeinsam bereitstellen, App-Ziele referenzieren

A erstellt Workspace, gemeinsame Beobachtung, Action Group und unabhängige Agenten-Ausfallmeldung. B liefert App-/Prüfreferenzen und Alarmkriterien; C liefert Empfänger und Authentifizierungsanforderungen; D liefert strukturierte Werkzeug-/Verbrauchsdaten. Die konkreten Monitor-Ressourcen bleiben bei A, damit sie nicht zusätzlich in App-Stacks verwaltet werden. API und Worker stellen getrennte Lebenssignale bereit.

Der echte Alarmtransport wird erst nach Vorliegen der Empfänger-URL integriert. Foundation-Code und Monitoring-Ressourcen können vorher entstehen. Ein endgültiger Alarmempfänger wird nicht durch eine ungesicherte temporäre API ersetzt.

### 5. Pipeline-basierter unabhängiger Wiederanlauf

Foundation-Workflows liegen getrennt von App-Workflows. Sie verwenden OIDC und das dokumentierte State-Backend. Konfiguration und Wiederanlauf-Anleitung liegen in Git. Für den Agentenausfall wird ein konfigurierbarer unabhängiger Benachrichtigungsempfänger vorgesehen und vor Abnahme getestet. Ein anderes autonomes Betriebssystem ist dafür nicht erforderlich.

## Risks / Trade-offs

- Vollständiger AKS-Ausfall betrifft auch den Agenten. Externe Datenbank, unabhängige Benachrichtigung und GitHub-Actions-Wiederherstellung begrenzen die Abhängigkeit, beseitigen sie aber nicht.
- Region, Quotas oder Netzwerkzugang passen nicht zur Anfangsannahme. Bootstrap prüft Verfügbarkeit und echte Verbindungen, bevor die anderen Stränge Cloud-Integration melden.
- Gemeinsame Pulumi-Dateien verursachen Überschneidungen. A besitzt Foundation-Manifest und gemeinsame Komponenten; B legt App-Komponenten unter `infra/app-runtime/` an.
- Kosten entstehen vor dem vollständigen Produktnachweis. D's echter Prototyp wird früh als Integrationsvoraussetzung geprüft; Kapazität und Region bleiben konfigurierbar.

## Migration Plan

1. Repository-Gerüste, Konventionen und Ausgabebeispiele veröffentlichen. Foundation-Code parallel zu D's Prototyp vorbereiten.
2. Nach Prototypnachweis Bootstrap und Foundation im verfügbaren Azure-Abonnement ausführen und separat Azure-, Cluster- und Datenzugriff testen.
3. Ausgaben an B, C und D übergeben. C initialisiert das Schema; B deployt Apps mit C's standalone Client.
4. Echten Alarm und Agenten-Ausfallbenachrichtigung verbinden und prüfen. Bei Ausfall der App-Integration bleibt die Foundation separat wiederholbar.
5. Rollback über vorherige geprüfte Foundation-Konfiguration planen. State und Datenbank werden nicht durch pauschales Löschen und Neuerstellen repariert. Unterstützte AKS-Upgrades und Wiederherstellungswege werden gesondert dokumentiert; ein Versionsrückschritt wird nicht vorausgesetzt.

## Open Questions

- Konkrete Azure-Region, Staging-Kapazität und unterstützte AKS-Version werden beim Bootstrap anhand des Abonnements gewählt.
- Der unabhängige Benachrichtigungsempfänger und die Testdomain werden als Umgebungswerte vor Staging-Abnahme eingetragen.
