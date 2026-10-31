# Tasks

## 1. Annahme und Speicherung (C-Bereich)

- [x] 1.1 Migration `0002_portal.sql`: Tabellen `portal_access_codes`, `conversation_messages`; Quelle `portal` im Ereignisschema `platform/schemas/events/`. Nachweis: `ops-migrate` Lauf 41180982262 („Migrationen eingespielt: [2]“), Ereignisschema-Tests.
- [x] 1.2 CLI `ops portal-code create|list|revoke`; Abschluss durch Test mit erzeugtem, aufgelistetem und widerrufenem Code. Nachweis: `tests/state/test_portal.py`; erster Staging-Code „MMK Team“ am 31.10. erzeugt.
- [x] 1.3 Endpunkte `POST /portal/conversations`, `POST /portal/conversations/{id}/messages`, `GET /portal/conversations/{id}`, `GET /portal/session` mit Code-Prüfung, Ratenbegrenzung, Deduplizierung und CORS; Abschluss durch API-Tests. Nachweis: `tests/state/test_portal.py`; live 31.10.: 401 ohne/mit falschem Code, 200 mit gültigem Code, CORS nur für `platform-status`.
- [x] 1.4 Worker: `ContextRefs.conversation`, Verlauf als Daten, Dienst `conversation.post` mit Besitzprüfung; Abschluss durch Worker-Test. Nachweis: `tests/state/test_portal_worker.py` (366 Tests grün am 31.10.).

## 2. Agent (D-Bereich)

- [x] 2.1 Werkzeug `reply_to_reporter` und genau eine Ergebnisnachricht je Auftrag (auch nach Wiederaufnahme); Abschluss durch Runner-Tests. Nachweis: `tests/agent/test_chat.py`.
- [x] 2.2 Betriebsregeln: Chat-Wünsche als Anliegen, Antworten in einfacher Sprache.

## 3. UI

- [x] 3.1 Chat-Bereich auf `/applications/{appId}` mit Code-Eingabe, Name, Verlauf, Zustandsanzeige, Polling; `/status` liefert `portalUrl`. Nachweis: Plattform-UI 0.1.26-Image in Staging (`app-deploy` 41181312627), `/status` liefert `portal`.
- [x] 3.2 Browser-Prüfung: Meldung senden, Antwort sehen, falscher Code, mobile Breite. Nachweis: lokaler Durchlauf im Browser mit Demo-Ausführung (Meldung, Antwort, Ergebnis, falscher Code, 375 px ohne Querscrollen); in Staging nur Code-Eingabe angesehen.

## 4. Betrieb und Nachweis

- [x] 4.1 Runbook `platform/operations/state/portal.md` (Code erzeugen, weitergeben, widerrufen, Fehlersuche).
- [ ] 4.2 In Staging: Code erzeugen, Meldung für AFFiNE senden, Agentenantwort und Laufnachweis unter `GET /runs/{id}` prüfen. Stand 31.10.: Code erzeugt und geprüft; noch keine echte Meldung in Staging gesendet.
