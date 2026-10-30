# Admin-Aufgaben und Journeys

Zielgruppe ist der Administrator der Staging-Plattform. Er braucht zuerst eine schnelle Entscheidung über Handlungsbedarf und danach belastbare Details. Die vorhandene MMK-Gestaltung bleibt erhalten.

## Befund am 30.10.2026

Screenshot der Ausgangsseite: `admin-before.jpg`, im Chat festgehalten. Die Seite zeigt Erreichbarkeit und Auftragszähler verständlich, bietet aber nur Sprungmarken auf derselben Seite. Es fehlen Anwendungssuche, Monitoring-Details und ein nachvollziehbarer Verlauf. Die Tabelle nennt nur Auftragszustände; System-Deployments, erkannte Signale und belegte Behebungen lassen sich nicht auseinanderhalten. Für kleine Mobilansichten ist die Navigation ausgeblendet.

## User Stories

| ID | Als Admin möchte ich … | Nutzen und Akzeptanz |
| --- | --- | --- |
| US-1 | in etwa 30 Sekunden erkennen, wo Handlungsbedarf besteht | Dashboard priorisiert gestörte Apps und eskalierte Aufträge. Jede Zusammenfassung verlinkt auf die gefilterte Analyse. Ein veralteter Stand ist als solcher erkennbar. |
| US-2 | eine deployte Anwendung schnell finden und ihren Zustand untersuchen | Suchbare Anwendungsliste mit Zustandsfilter, direkter Detail-URL und Rückweg zur Liste. Ein verifizierter Rollout wird getrennt vom Konfigurationsstand im Image gezeigt. |
| US-3 | verstehen, was eine App überwacht und wohin Alarme gehen | Detailseite nennt Prüfpfad, TLS-Prüfung, Takt und Alarmziel der konfigurierten Überwachung sowie vorhandene empfangene Signale. Konfiguration wird nicht als nachgewiesene Azure-Aktivierung ausgegeben. |
| US-4 | erkennen, was Agent und System zuletzt getan haben | Eigene Aktivitätenseite mit Filtern nach App, Quelle/Kategorie, Ergebnis und Zeitraum. Erkennung, Agentenaktion, System-Deployment und Abschluss sind getrennt beschriftet. |
| US-5 | nachvollziehen, welche Probleme tatsächlich behoben wurden | „Verifiziert abgeschlossen“ erfordert einen aufgelösten Auftrag und eine erfolgreiche jüngste Verifikation desselben Auftrags. Ein abgeschlossenes Deployment oder ein Abschluss ohne Nachweis wird nicht so bezeichnet. |
| US-6 | auch bei fehlenden Daten sicher entscheiden | Fehlender Verlauf erscheint als unbekannt/unverfügbar, nicht als „keine Probleme“. Such- und Filterzustände stehen in der URL und bleiben beim Aktualisieren erhalten. |

## Journeys

### J1: Tagescheck

1. `/` öffnen. Gesamtzustand und Datenalter lesen.
2. Gestörte Anwendungen oder „Aufmerksamkeit“ wählen.
3. Die gefilterte App-Liste bzw. Aktivitätenseite zeigt die betroffenen Einträge.
4. Eintrag zur Anwendung öffnen. Ende: Der Admin weiß, ob er eingreifen oder die laufende Bearbeitung beobachten muss.

### J2: Eine Anwendung analysieren

1. `/applications` öffnen, Namen oder Host eingeben, optional Zustand filtern.
2. App öffnen. Detailseite bündelt Zustand, letzten dokumentierten Rollout, Monitoring und Aktivitäten dieser App.
3. Bei Bedarf den verifizierten Deployment-Lauf in GitHub öffnen.
4. Über Browser-Zurück zur unveränderten Suchansicht zurückkehren.

### J3: Monitoring prüfen

1. App-Liste nach fehlendem HTTP-Prüfziel filtern oder eine App auswählen.
2. Auf der Detailseite „Überwachung“ lesen: aktuelle HTTP-Prüfung, konfigurierte externe Verfügbarkeit und Smoke-Test.
3. Live-Prüfzeit, letztes empfangenes Signal und fehlende Nachweise unterscheiden.
4. Ende: Der Admin kennt die Abdeckung und die Grenzen der Aussage, ohne eine erfundene Monitoring-Garantie.

### J4: Agenten- und Systemarbeit nachvollziehen

1. `/activity` öffnen. Zeitraum 24 Stunden oder sieben Tage wählen.
2. Nach Anwendung und „Agent“, „System“, „Erkannt“ oder „Verifiziert abgeschlossen“ filtern.
3. Zeit, feste Tätigkeitsbeschreibung, Ergebnis und App lesen.
4. Bei Bedarf zur App-Detailseite wechseln. Leere Treffer und nicht verfügbare Daten werden unterschiedlich erklärt.

### J5: Eine Behebung kontrollieren

1. Vom Dashboard „Verifizierte Abschlüsse · 24 h“ öffnen.
2. Eintrag und Verifikationskennzeichnung prüfen.
3. App öffnen und aktuellen HTTP-Zustand mit dem historischen Behebungsnachweis vergleichen.
4. Ein aktuell gestörter Dienst bleibt gestört, auch wenn ein älteres Problem behoben wurde.

## Informationsarchitektur

- `/`: Tagescheck, Handlungsbedarf, wenige aktuelle Aktivitäten und Plattformkomponenten.
- `/applications`: durchsuchbarer Anwendungsbestand, Filter und Links zu Details.
- `/applications/<app-id>`: Zustand, dokumentierter Rollout, Überwachung und appbezogener Verlauf.
- `/activity`: vollständiger verfügbarer Ausschnitt des Verlaufs mit URL-Filtern.

Alle Seiten haben echte Links, Seitentitel, sichtbare aktive Navigation und mobilen Zugang. Die bestehende Status-JSON-Projektion bleibt kompatibel und wird um erlaubte strukturierte Daten ergänzt. Der Dienst bleibt eine lesende Staging-Ansicht; Rohtexte, Vorfallinhalte, Secrets und interne IDs werden nicht veröffentlicht. Es gibt keine Betriebsaktionen.
