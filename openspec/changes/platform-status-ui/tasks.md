# Tasks

## 1. Aufgaben und Vertrag

- [x] 1.1 Ausgangsseite aufnehmen und UX-Lücken dokumentieren.
- [x] 1.2 User Stories US-1 bis US-6, Journeys J1 bis J5 und getrennte Seiten festlegen.
- [x] 1.3 Capability mit festen Admin-Anforderungen aktualisieren und validieren.

## 2. Lesende Projektion

- [x] 2.1 Monitoring-Konfiguration, letzte Signale und App-bezogene Kennzahlen liefern.
- [x] 2.2 Erkennungen, Agenten-/Systemaktionen und Abschlüsse sicher projizieren.
- [x] 2.3 Verifizierte Behebungen und dokumentierte Rollouts korrekt abgrenzen.
- [x] 2.4 Umgebung, Zeitfenster, Limits, unbekannte Werte und Schutz privater Felder testen.

## 3. Oberfläche

- [x] 3.1 Status-Dashboard mit priorisiertem Handlungsbedarf und Analyse-Links umsetzen.
- [x] 3.2 Suchbare Anwendungsliste und Detailseiten mit Monitoring und Verlauf umsetzen.
- [x] 3.3 Aktivitätenseite mit URL-Filtern und nachvollziehbaren Ergebnissen umsetzen.
- [x] 3.4 Mobile Navigation, Tastaturbedienung, Aktualisierung und Datenfehler prüfen.

## 4. Abschluss

- [x] 4.1 Relevante Backend- und Routingtests, JavaScript-Prüfung und Chart-Prüfung abschließen.
- [x] 4.2 Journeys mit realer Vorschau und gezielten Testdaten visuell prüfen.
- [x] 4.3 Runbook und Datenlimits aktualisieren, Änderungen committen und pushen.

## Prüfnachweise

- Vollständige CI-Agenten-Suite für `7988e96`: 304 bestanden, einer übersprungen. Zusätzliche Image-Prüfstufe: 192 bestanden, 113 übersprungen.
- Abschließende gezielte Status-/Admin-Suite: 20 bestanden, einschließlich Quellen, jüngster Verifikation, Datenschutz, Umgebung, 100er-Limit, Sieben-Tage-Grenze, Rollout-Nachweisen, nichtschreibenden Routen und Cache-Wechsel nach Deployment.
- JavaScript-Syntax, Helm-Lint und OpenSpec-Validierung erfolgreich.
- Browser-Journeys mit ausdrücklich als QA-Testdaten markierter Vorschau: Störung zur App-Analyse, Suche/Filter und Browser-Zurück, Behebungsfilter mit 7-Tage-URL, Filtererhalt beim Aktualisieren, Verbindungsfehler und Erholung.
- Mobile Prüfung bei 390 × 844: Navigation sichtbar, kein Seitenüberlauf auf Aktivitäten und App-Details. Screenshots im Chat: `admin-before.jpg`, `admin-detail-qa.jpg`, `admin-activity-qa.jpg`, `admin-mobile-qa.jpg`. Screenshots bestätigen Layout und getestete Wege, keine vollständige Accessibility-Zertifizierung.
- Finaler Azure-Rollout [41125342717](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41125342717) erfolgreich, Smoke-Test erfolgreich, Release entspricht dem Sollzustand, kein Break-glass.
- Live-Journeys am 30.10.2026: vorhandenen Browser-Tab normal neu geladen, Dashboard mit drei erreichbaren Apps, App-Suche → AFFiNE-Details → Zurück mit erhaltenem Filter, Monitoring-/Rollout-Nachweise und Systemaktivitäten für AFFiNE geprüft. Statusprojektion verfügbar, elf reale Aktivitäten zum Prüfzeitpunkt. Screenshots: `admin-status-azure.jpg`, `admin-activity-azure.jpg`.
- Alle Seiten, `/healthz` und `/status` über gültiges HTTPS mit HTTP 200 geprüft; `/docs`, `/redoc` und `/openapi.json` des eigenständigen Dienstes liefern HTTP 404. OpenSpec mit `--strict` ohne Warnungen gültig.
