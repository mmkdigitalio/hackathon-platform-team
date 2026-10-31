# Tasks

## 1. Annahme und Speicherung (C-Bereich)

- [ ] 1.1 Migration `0002_portal.sql`: Tabellen `portal_access_codes`, `conversation_messages`; Quelle `portal` im Ereignisschema `platform/schemas/events/`.
- [ ] 1.2 CLI `ops portal-code create|list|revoke`; Abschluss durch Test mit erzeugtem, aufgelistetem und widerrufenem Code.
- [ ] 1.3 Endpunkte `POST /portal/conversations`, `POST /portal/conversations/{id}/messages`, `GET /portal/conversations/{id}`, `GET /portal/session` mit Code-Prüfung, Ratenbegrenzung, Deduplizierung und CORS; Abschluss durch API-Tests.
- [ ] 1.4 Worker: `ContextRefs.conversation`, Verlauf als Daten, Dienst `conversation.post` mit Besitzprüfung; Abschluss durch Worker-Test.

## 2. Agent (D-Bereich)

- [ ] 2.1 Werkzeug `reply_to_reporter` und genau eine Ergebnisnachricht je Auftrag (auch nach Wiederaufnahme); Abschluss durch Runner-Tests.
- [ ] 2.2 Betriebsregeln: Chat-Wünsche als Anliegen, Antworten in einfacher Sprache.

## 3. UI

- [ ] 3.1 Chat-Bereich auf `/applications/{appId}` mit Code-Eingabe, Name, Verlauf, Zustandsanzeige, Polling; `/status` liefert `portalUrl`.
- [ ] 3.2 Browser-Prüfung: Meldung senden, Antwort sehen, falscher Code, mobile Breite.

## 4. Betrieb und Nachweis

- [ ] 4.1 Runbook `platform/operations/state/portal.md` (Code erzeugen, weitergeben, widerrufen, Fehlersuche).
- [ ] 4.2 In Staging: Code erzeugen, Meldung für AFFiNE senden, Agentenantwort und Laufnachweis unter `GET /runs/{id}` prüfen.
