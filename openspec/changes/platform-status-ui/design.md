# Design

## Aufgaben und Oberfläche

[user-journeys.md](user-journeys.md) definiert Zielgruppe, Aufgaben, Wege und Ausgangsbefund. Das Dashboard ist eine Priorisierungsansicht; Analysen liegen auf eigenen Seiten. Die vorhandenen Farb-, Typografie- und Abstands-Tokens bleiben bestehen. Tabellen, Zeitangaben und kurze feste Tätigkeitsbeschreibungen ersetzen technische Rohdaten.

## Routing und Interaktion

FastAPI liefert dieselbe schlanke HTML-Hülle für die vier Seitentypen. Echte Pfade und Links ermöglichen Reload, Deep Links und Browser-Zurück. Der Client rendert die zur URL passende Ansicht und lädt ausschließlich die lesende `/status`-Projektion. Filter `q`, `state`, `app`, `category`, `result`, `range` stehen in der URL. Aktualisierung ersetzt Daten, nicht Eingaben oder Fokus. Ungültige Apps liefern HTTP 404.

## Datenquellen

- App-Katalog und Sollzustand im Image liefern Beschreibung, Host, Prüfpfad und deklarierte Version. Die UI bezeichnet diese Angaben als Konfigurationsstand im Image, nicht als tatsächlichen Containerstand. Beispiel am 31.10.2026: `/status` meldete für `operations-agent` Version 0.1.22, während `platform/environments/staging/operations-agent.yaml` im Repository bereits Chart 0.1.23 enthält; die Statusansicht trägt den Sollzustand ihres eigenen, älteren Images. (ergänzt 31.10.2026 (Cornelius))
- Die laufende HTTP-Prüfung liefert Erreichbarkeit, Statuscode, Latenz und Prüfzeit.
- Die Foundation-IaC beschreibt externe Verfügbarkeitstests mit 300 Sekunden Takt, fünf Standorten, Alarm bei zwei fehlschlagenden Standorten und Alarmziel. Dies ist Konfiguration, keine Abfrage des aktiven Azure-Monitors. Die UI liest die IaC nicht; Takt (300 s), fünf Standorte und zwei fehlschlagende Standorte sind fest in `status.py` hinterlegt und stimmten am 31.10.2026 mit `platform/infra/foundation/monitoring.ts` überein. Ändert sich die IaC, muss `status.py` angepasst werden, sonst zeigt die UI veraltete Konfiguration. Das Alarmziel ist `ops-humans` für `operations-agent`, sonst `ops-intake`. (ergänzt 31.10.2026 (Cornelius)) Empfangenes Azure-Signal und Zeitpunkt werden separat aus `events` gelesen.
- Die Operations-Datenbank liefert strukturierte Ereignisse, Aktionen, terminale Aufträge, jüngste Verifikationen und den letzten dokumentierten Deployment-Lauf je App.
- Ein Deployment gilt als verifiziert dokumentiert, wenn sein gespeichertes Ergebnis erfolgreich ist und `verification.success` sowie `releaseMatchesDesired` wahr sind. Die Anzeige ist kein Live-Kubernetes-Inventar.

## Verlauf und Sicherheit

Der Verlauf ist auf die jüngsten 100 Einträge aus sieben Tagen beschränkt. Filter gelten für diesen sichtbaren Ausschnitt; die Grenze ist genannt. Reihenfolge ist absteigend nach Zeitpunkt mit stabiler Gleichstandsregel. Quellen, Aktionen und Ergebnisse werden ausschließlich auf feste Labels abgebildet. Freie Meldungen, Gründe, Findingtexte, Targets, Payloads, rohe IDs und beliebige externe URLs werden nicht ausgegeben. GitHub-Läufe werden nur für das bekannte Plattform-Repository verlinkt.

Ein Problem ist nur verifiziert behoben, wenn `jobs.state = resolved` und die jüngste Verifikation desselben Auftrags erfolgreich ist. Ein fehlender oder jüngst fehlgeschlagener Prüfnachweis reicht nicht. System-Deployments und Agentenmaßnahmen sind eigenständige Aktivitäten und keine Behebungsbeweise.

Die zusätzlichen Abfragen sind parametriert, auf Umgebung und Zeitfenster beschränkt und laufen in lesenden Transaktionen mit Statement-Timeout. Eine gestörte Verlaufsprojektion beeinflusst nicht fälschlich die Erreichbarkeit einer App, sondern zeigt einen eigenen Datenhinweis. Das bestehende Staging-Zugriffsmodell bleibt bestehen: Die Seiten und `/status` sind ohne Anmeldung erreichbar (am 31.10.2026 per HTTP 200 ohne Zugangsdaten bestätigt). Die Administrator-Datenbankrolle ist eine bestehende technische Einschränkung; der Code nutzt ausschließlich lesende Transaktionen.

## Aktualisierung und Grenzfälle

Snapshot-Cache zehn Sekunden, Aktualisierung anhand `refreshAfterSeconds`, HTTP-Timeout zwanzig Sekunden. Bei Abruffehler oder Alter über 90 Sekunden sind Gesamtzustand und Datenalter als unbekannt/veraltet markiert. Leere Ergebnisse, nicht verfügbare Daten, nicht konfigurierte Prüfziele und unbekannte App werden unterschiedlich behandelt. Eigene Prozessgesundheit wird getrennt von App- und Monitoring-Gesundheit ausgewiesen.

## Bereitstellung

Dasselbe Python-Image liefert Agenten-API und eigenständige Status-App. Der bestehende IaC-App-Pfad bleibt erhalten. Chart und Vorschau müssen alle lesenden Seiten unterstützen. API-Dokumentation am eigenständigen Dienst ist abgeschaltet; die Agenten-API behält ihre bisherigen Endpunkte. Neue Rohdaten- oder Schreibendpunkte werden nicht hinzugefügt.

## Offene Punkte (Stand 31.10.2026)

- Keine Anmeldung. Für Produktion oder freie Texte (zum Beispiel Chat-Inhalte) wäre ein eigener Zugangsschutz nötig.
- Die Status-App nutzt die PostgreSQL-Administratorrolle aus der Foundation (`postgresAdminUser`); der Code nutzt nur lesende Transaktionen, eine eigene Leserolle gibt es nicht.
- Monitoring-Parameter sind im Code dupliziert (siehe Datenquellen).
- Ein Chat in der Plattform-UI als Mitarbeiterkanal ist geplant (siehe E11 in [PARALLEL-IMPLEMENTATION.md](../../PARALLEL-IMPLEMENTATION.md)). Er ist nicht Teil dieses Changes und widerspricht der Anforderung „Ausschließlich lesende Statusansicht“, falls er in dieselbe App kommt. Der Chat-Change muss entweder eine getrennte App vorsehen oder diese Anforderung ausdrücklich ändern.
- `.github/CODEOWNERS` hat keine Zeile für `platform/infra/status-ui/` und `status-ui-package.yml` (greift `*`, also Strang A), PARALLEL-IMPLEMENTATION.md nennt B.
