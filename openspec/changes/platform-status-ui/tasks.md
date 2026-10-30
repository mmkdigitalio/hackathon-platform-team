# Tasks

Der Bestand wurde ohne Spec gebaut; die Abschnitte 1 und 2 sind nachgetragen und nur mit Nachweis abgehakt. Nachweis „live“ bedeutet: am 30.10.2026 gegen `https://platform-status.57-153-7-126.sslip.io/` geprüft (`/status` lieferte JSON mit HTTP 200; Gesamtzustand `degraded`, weil Operations-Agent und Worker nicht laufen) und der Deploy-Lauf [41107859138](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41107859138) (Workflow `app-deploy`, Commit `fbe2a01`) ist erfolgreich. Zuständigkeit siehe [Proposal](proposal.md#impact).

## 1. Bestand (nachgetragen)

- [x] 1.1 Statusprojektion `status.py` und statische Seite unter `operations-agent/src/api/` (Commit `d95d545`); Nachweis: Tests in `operations-agent/tests/state/test_status.py` (TLS-Fehler, Redirect, fehlende Konfiguration, Katalogfehler, Datenbankprojektion ohne private Felder, Datenbankausfall, Auslieferung und `no-store`), live `/status` und JavaScript-Asset mit 200.
- [x] 1.2 Eigenständiger Einstieg `platform/infra/status-ui/files/serve.py`, Helm-Chart und `.github/workflows/status-ui-package.yml` (Commit `68d0e6a`); Nachweis: Chart `0.1.1` im Runbook als veröffentlicht und ausgerollt vermerkt, `/healthz` live mit 200.
- [x] 1.3 Katalogeintrag `platform/catalog/platform-status/app.yaml` und Sollzustand `platform/environments/staging/platform-status.yaml` (Commit `fbe2a01`); Nachweis: erfolgreicher Deploy-Lauf ohne Break-glass, live erreichbar mit gültigem Zertifikat.
- [x] 1.4 Runbook `platform/operations/state/status-ui.md` mit Daten, Grenzen, Konfiguration, lokaler Vorschau und Staging-Nachweis; Nachweis: Datei vorhanden, Inhalt stimmt mit Code und Live-Stand überein (Abweichungen siehe Abschnitt 3).

## 2. Spec nachziehen

- [x] 2.1 Capability `platform-status` mit stabilen Requirements und ausdrücklich vorläufigem Inhalt schreiben; Abschluss durch Abgleich jeder Anforderung mit Code oder Live-Verhalten prüfen.
- [x] 2.2 `openspec validate platform-status-ui` ausführen; Nachweis: Change gilt als valide (nur Warnungen zu langen Requirement-Texten, wie bei den Specs der anderen Changes üblich).
- [ ] 2.3 Tests den Requirements zuordnen und Lücken schließen: HTTP-405 für schreibende Methoden, 404 für `/runs/*` und `/events/*` am eigenständigen Dienst, Chart-Rendering (`automountServiceAccountToken`, Image-Digest); Abschluss durch grüne Tests prüfen.

## 3. Offene Abweichungen zwischen Anspruch und Bestand

Diese Punkte brauchen eine Entscheidung; sie sind kein Teil der inhaltlichen Überarbeitung.

- [ ] 3.1 Eigene lesende Datenbankrolle für den Statusdienst statt Administrator-Zugang (`foundation:postgresAdminPassword`) vorsehen oder die Abweichung bewusst akzeptieren (Staging); Abschluss durch Entscheidung und, bei Umsetzung, durch Verbindung mit einer Rolle ohne Schreibrechte prüfen. Berührt A (Ausgaben) und C (Datenbankobjekte).
- [ ] 3.2 Zugriffsentscheidung treffen: öffentlich ohne Anmeldung belassen oder einschränken; Abschluss durch dokumentierte Entscheidung im Runbook prüfen.
- [ ] 3.3 `/docs`, `/redoc` und `/openapi.json` am eigenständigen Dienst abschalten oder die Fußzeilenverlinkung samt Runbook korrigieren; Abschluss durch HTTP 404 beziehungsweise angepasste Doku prüfen. Entscheiden, ob die Agenten-API ihre Dokumentation weiter öffentlich ausliefert.
- [ ] 3.4 Image neu bauen und Digest im Sollzustand aktualisieren, damit der Katalogeintrag `platform-status` im ausgelieferten Image enthalten ist; entscheiden, ob die Seite sich selbst prüfen oder ausnehmen soll; Abschluss durch `/status` mit erwartetem Dienstumfang prüfen.
- [ ] 3.5 Alarmkette für `platform-status` klären: Verfügbarkeitstest und Alarm an `ops-intake` (A, Agent ohne erlaubte Aktionen) beibehalten oder auf `ops-humans` umstellen; Abschluss durch Entscheidung und Eintrag in A's Spec oder Ausnahmeliste prüfen.
- [ ] 3.6 `refreshAfterSeconds` aus dem JSON im Browser verwenden oder aus der Projektion entfernen; Abschluss durch Test prüfen.
- [ ] 3.7 Runbook an die Spec angleichen (Endpunktliste, Zugriffsmodell, Datenbankrolle) und auf `openspec/changes/platform-status-ui/` verweisen; Abschluss durch Durchsicht gegen Code prüfen.

## 4. Inhaltliche Überarbeitung (vorläufig, noch nicht begonnen)

- [ ] 4.1 Gewünschte Inhalte, Kennzahlen, Texte und Zielgruppe der Seite festlegen; Abschluss durch abgestimmte Liste prüfen.
- [ ] 4.2 Oberfläche und, wo nötig, die `/status`-Projektion umsetzen; Abschluss durch lokale Vorschau (`scripts/preview-status.py`) und Tests prüfen.
- [ ] 4.3 Requirement „Vorläufige Inhaltsdarstellung“ durch feste Anforderungen für die dann entschiedenen Inhalte ersetzen, neue Felder gegen „Keine Geheimnisse und minimale Projektion“ prüfen; Abschluss durch Spec-Validierung prüfen.
- [ ] 4.4 Neues Image bauen, Digest im Sollzustand aktualisieren, per `app-deploy.yml` ausrollen und den Staging-Nachweis im Runbook erneuern; Abschluss durch erfolgreichen Deploy-Lauf und Live-Abruf von `/` und `/status` prüfen.
