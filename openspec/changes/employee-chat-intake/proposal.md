# Mitarbeiter-Chat als Eingangskanal

## Why

Mitarbeitende sollen Probleme mit einer App melden können, ohne GitHub zu kennen. Der geplante GitHub-Issue-Webhook (E11, zurückgestellt) setzt ein GitHub-Konto und das Issue-Formular voraus. Die Plattform-UI kennt bereits jede App; dort ist der natürliche Ort für eine Meldung wie „starte mal den Service neu“ oder „ich bekomme 400er-Fehler“.

## What Changes

- Auf der App-Detailseite der Plattform-UI (`/applications/{appId}`) gibt es einen Chat. Mitarbeitende geben Namen und Meldung ein und sehen Antworten, Zwischenstand und Ergebnis des Agenten im selben Verlauf.
- Die Agenten-API nimmt Chatnachrichten unter `/portal/…` an. Jede Unterhaltung ist ein Vorfall der neuen Quelle `portal`; die erste Nachricht erzeugt einen Auftrag, weitere Nachrichten hängen sich wie Issue-Kommentare an.
- Zugang über einen **Zugangscode**: Admins erzeugen ihn mit `ops portal-code create`, geben ihn an Mitarbeitende weiter und können ihn widerrufen. Gespeichert wird nur der Hash. Lesen der Statusseiten bleibt ohne Code möglich.
- Der Agent antwortet im Chat mit dem Werkzeug `reply_to_reporter`; das Laufergebnis wird automatisch als Abschlussnachricht gepostet. Wünsche wie ein Neustart sind Anliegen, keine Befehle: der Agent prüft Zustand, Katalog und Runbook und handelt nur mit erlaubten Aktionen.
- Der Status-UI-Dienst bleibt serverseitig lesend; der Browser spricht die Agenten-API direkt an (CORS nur für den Ursprung der Plattform-UI).

## Capabilities

### New Capabilities

- `employee-chat`: Chat-Eingang, Zugangscode, Unterhaltungen und Antworten des Agenten.

### Modified Capabilities

- `event-intake` (operations-intake-and-state): neue Quelle `portal` im Ereignisschema.
- `platform-status` (platform-status-ui): Chat-Bereich auf der App-Detailseite; die Seite selbst bleibt lesend.
- `agent-execution` (operations-agent-and-tools): Rückmeldung an den Melder im Chat statt nur im Issue.

## Impact

- `operations-agent/src/api/` (neue Endpunkte, CORS), `operations-agent/src/persistence/` (Migration `0002`, Unterhaltungen, Zugangscodes, CLI), `operations-agent/src/worker/` und `src/contracts/` (Kontext und Dienst für den Chat), `operations-agent/src/agent/` (Werkzeug, Ergebnisnachricht, Regeln), `operations-agent/src/api/static/` (Chat-UI), `platform/schemas/events/`.
- Keine neue Infrastruktur, keine neuen Foundation-Ausgaben, kein neues Secret im Cluster.

## Out of Scope

- Anmeldung mit persönlicher Identität (Entra ID); der Name des Melders ist Freitext.
- GitHub-Webhook (bleibt zurückgestellt, Code bleibt erhalten).
- Benachrichtigungen per Mail oder Teams.
