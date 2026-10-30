# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md). [event-intake](specs/event-intake/spec.md) und [durable-operations](specs/durable-operations/spec.md) definieren die Anforderungen. API, Worker und CLI liegen im selben Python-Paket unter `operations-agent/src/`, laufen aber als getrennte Prozesse. Die [gemeinsame Übersicht](../../PARALLEL-IMPLEMENTATION.md) beschreibt Lieferverträge, Zuständigkeiten und die Hackathon-Entscheidungen E1 bis E10.

## Goals / Non-Goals

Goals:

- Auftragsannahme und Besitz deterministisch, lokal mit PostgreSQL und ohne Modell testbar machen.
- Agent, Pipeline und Wiederanlauf auf dasselbe Aktionsprotokoll und dieselbe Sperre verpflichten.
- D eine klare Ausführungsschnittstelle geben, ohne Diagnose und SDK-Code in die Auftragsverwaltung zu ziehen.
- Agentenausfall ohne Modell und ohne Azure-Messwerte erkennbar machen (E10).

Non-Goals:

- Kein zusätzlicher Broker, kein Modell im Requestpfad und keine zweite Queue innerhalb von D.
- Keine Exactly-once-Garantie allein aus Datenbank oder Checkpoints ableiten.
- Keine quellenübergreifende Vorfallkorrelation in Phase 1.
- Keine Auswertung von Azure-Messwerten (E6).
- A's Azure-Ressourcen, B's Deploy-Workflows und D's Adapter, Image und Chart bleiben außerhalb dieses Changes.

## Decisions

### 1. Persistenter Eingang mit PostgreSQL-Transaktionen

Die API unter `operations-agent/src/api/` bietet folgende Endpunkte:

| Endpunkt | Schutz | Zweck |
| --- | --- | --- |
| `POST /events/azure-monitor?key=<alertWebhookKey>` | Query-Key | Common Alert Schema von `ops-intake` |
| `POST /events/github` | `X-Hub-Signature-256` mit `githubWebhookSecret` | Repo-Webhook für `issues` und `issue_comment` |
| `POST /events/maintenance` | `X-Api-Key` mit `maintenanceApiKey` | Wartungsaufträge, etwa aus `health-check.yml` |
| `GET /runs/{auftragsId}` | `X-Api-Key` mit `maintenanceApiKey` | Laufnachweise als JSON |
| `GET /healthz` | keiner | Datenbank und Worker-Heartbeat |

Keys werden mit konstanter Laufzeit verglichen. Request-Logs maskieren den Query-Parameter `key` und die Header `X-Api-Key` und `X-Hub-Signature-256`. Die Werte kommen aus den Foundation-Ausgaben über das Kubernetes-Secret, das der Agenten-App-Stack anlegt.

Jeder Endpunkt normalisiert auf den Ereignisvertrag unter `platform/schemas/events/` und schreibt Ereignis, Zustellungsidentität und Auftrags-/Vorfallverknüpfung in einer Transaktion. Ein eindeutiger Index auf (`source`, `deliveryId`) verhindert doppelte Annahme. Erst der Commit erlaubt die erfolgreiche HTTP-Antwort.

`deliveryId` je Quelle:

- Azure Monitor: `<essentials.alertId>:<essentials.monitorCondition>`. Azure liefert keine eigene Zustellungs-ID; dieselbe Kombination bei erneuter Zustellung ist dasselbe Ereignis.
- GitHub: Header `X-GitHub-Delivery`.
- Wartung: vom Aufrufer gesetztes Feld `deliveryId`, im Gesundheitscheck aus Workflow-Lauf und Prüfung gebildet.

**Azure Monitor.** `appId` und `environment` kommen zuerst aus den Custom Properties `app-id` und `environment`, sonst aus dem Regelnamen `alert-<app-id>-<env>-<check>`. `externalRef` ist `essentials.alertId`. `monitorCondition` wird zu `state` `fired` oder `resolved`. Resolved aktualisiert nur den Alarmzustand.

**GitHub.** Ein Repo-Admin legt den Webhook auf `mmkdigitalio/hackathon-platform-team` manuell an: URL `https://<operationsApiHost>/events/github`, Inhalt JSON, Ereignisse Issues und Issue comments, Secret gleich `githubWebhookSecret`. Die Anleitung liegt in `platform/operations/state/`. Der Eingang prüft die Signatur über den unveränderten Body, dann die Repository-Allowlist (Phase 1: nur dieses Repository), dann Ereignistyp und Aktion (`issues.opened`, `issue_comment.created`). App und Umgebung liest er aus den Abschnitten `### App` und `### Umgebung`, die die Issue-Form unter `.github/ISSUE_TEMPLATE/` mit Dropdowns erzeugt. Die Dropdown-Werte werden aus `platform/catalog/` gepflegt. Kommentare mit `<!-- mmk-ops-agent -->` im Text werden ignoriert. Der Autor zählt nicht, weil der Agent per PAT unter einem Teammitglied schreibt (E7).

**Wartung.** `.github/workflows/health-check.yml` liest Host und `maintenanceApiKey` über `.github/actions/foundation-outputs`, führt die deterministischen Prüfungen direkt aus und sendet nur bei Fehlschlag einen Wartungsauftrag.

**Vorfallzuordnung.** Fired und Resolved mit derselben `alertId` gehören zu einem Vorfall, ebenso alle Kommentare eines Issues. Weitere Zusammenführung gibt es in Phase 1 nicht: Ein Alarm und ein Support-Issue zur selben App ergeben zwei Vorfälle. Das hält die Regel prüfbar und vermeidet falsches Zusammenlegen verschiedener Probleme.

### 2. Auftrags-Lease und Ausführung trennen

Der Worker unter `operations-agent/src/worker/` übernimmt wartende Aufträge atomar (`SELECT ... FOR UPDATE SKIP LOCKED`) mit Besitzer, Ablaufzeit und Besitzversion. Er erneuert den Besitz anhand der Datenbankzeit. Jeder Schreibzugriff prüft die Besitzversion. Ein übernehmender Worker prüft zuerst offene Aktionen.

In Staging läuft genau ein Worker-Replica. Das Lease-Verfahren bleibt trotzdem nötig, weil ein neuer Pod nach Absturz den alten Besitz übernimmt. Konkurrenz zweier Worker wird nur lokal getestet.

C's Worker übernimmt Scheduling, Besitz, Wiederaufnahme und Übergabe. D implementiert eine aufrufbare Agentenausführung. Der Vertrag liegt unter `operations-agent/src/contracts/` und übergibt Auftrag, Vorfall, Ausführung, App/Umgebung, Kontextreferenzen, gültigen Besitz, Budget und Wiederaufnahmeinformationen. D meldet Befunde, Aktionsreferenzen, Verbrauchsdaten, Verifikationsnachweise und einen Ergebnisvorschlag. In lokalen C-Tests genügt eine vertragskonforme Testausführung.

### 3. Explizite Zustandsregeln

Der reguläre Ablauf ist `received -> queued -> investigating -> acting -> verifying -> resolved`. Untersuchung ohne Maßnahme kann nach `verifying` wechseln. Ein erneuter begrenzter Versuch führt aus `verifying` zurück nach `investigating`. Aktive Zustände können bei begründetem Abbruch nach `failed` oder `needs_attention` wechseln. Wiederanlauf behält den gespeicherten Zustand und gleicht Aktionen ab, statt pauschal auf `queued` zurückzusetzen.

`resolved` verlangt zwei Nachweise: einen positiven fachlichen Test, der nach der letzten Maßnahme begonnen hat (ohne Maßnahme: nach Beginn der Untersuchung), und den danach erfassten Kubernetes-Zustand des Ziels mit dem erwarteten Release. Die Auftragsverwaltung prüft App, Umgebung, Release und Zeitbezug. Azure-Messwerte liest der Agent nicht (E6). Technischer Alarmzustand und Vorfallabschluss sind getrennt; D's Ergebnistext allein genügt nicht. Die Übergangstabelle wird mit negativen Fällen getestet.

### 4. Aktionsprotokoll unabhängig von SDK-Checkpoints

Eine Operation (`operationId`) gruppiert einen ändernden Ablauf je App/Umgebung. Einzelne Aktionen (`actionId`) besitzen Ziel, Art, Status, Korrelation und externe Referenzen. Vor einem externen Aufruf wird die Aktion gespeichert, nach der Antwort ergänzt. Unbekannte Ergebnisse bleiben ausdrücklich unklar.

D liefert je Adapter den Abgleich gegen das externe System. Die Auftragsverwaltung blockiert potenziell doppelte Änderungen bis zur Klärung. Ein bekannter Workflow wird weiterverfolgt. Bei Mehrdeutigkeit endet der Auftrag in `needs_attention`.

### 5. Eine Sperre für Agent und Pipeline, ohne API-Abhängigkeit

C liefert unter `operations-agent/src/persistence/` eine Python-Bibliothek und die CLI `ops`. Agent und GitHub-Runner greifen direkt auf die Datenbank `operations` zu. Runner melden sich mit `postgresAdminUser` und `postgresAdminPassword` aus den Foundation-Ausgaben an (TLS erzwungen, keine Azure-Identität). So funktionieren Erstdeployment und Agenten-Update auch ohne laufende API.

Je App/Umgebung gibt es höchstens einen Besitzer mit Ablaufzeit und Besitzversion. Erwerb, Erneuerung, Besitzprüfung und Freigabe prüfen den Besitzer transaktional. Bei Besitzverlust stoppt der Ausführer weitere Schreibschritte; gestartete externe Vorgänge werden abgeglichen. Ein ungeklärter externer Vorgang verhindert die Neuvergabe, auch nach Ablauf.

**Konfliktregel.** `ops lock acquire` wartet im Workflow bis zu 15 Minuten (Abfrage in festen Abständen) und endet dann mit Fehler. Der Agent ruft den Erwerb ohne Warten auf; bei belegter Sperre beendet die Auftragsverwaltung den Auftrag mit `needs_attention` und dem Grund „gesperrt durch Operation <operationId>“.

**Break-glass (E9).** Ist PostgreSQL nicht erreichbar, bricht `ops lock acquire` ab. Nur mit `--force` setzt die CLI den Workflow ohne Sperre fort und schreibt einen Vermerk mit App, Umgebung, Git-SHA, Workflow-Lauf und Zeitpunkt in `$GITHUB_STEP_SUMMARY`. Erreicht die CLI PostgreSQL, ignoriert sie `--force`. Nach Wiederherstellung trägt eine Person den Vorgang mit `ops lock backfill` als Operation mit Vermerk `break-glass` ins Aktionsprotokoll nach; das Verfahren steht in `platform/operations/state/`. Der Agent nutzt `--force` nie.

**Dispatch.** Workflow-Dispatch durch den Agenten wird als Aktion mit Korrelation gespeichert. Der B-Workflow erwirbt die Ziel-Sperre, D verfolgt den Lauf lesend und hält dabei keine Ziel-Sperre. Die `operationId` ist ein Korrelationsmerkmal, kein Besitznachweis.

**Foundation.** Der Foundation-Stack nimmt keine Sperre und hängt nicht von PostgreSQL-Erreichbarkeit ab.

Ein reiner HTTP-Sperrdienst würde einen laufenden Dienst voraussetzen. GitHub-Concurrency koordiniert keine direkten Kubernetes-Werkzeuge und ersetzt die Sperre nicht.

### 6. Migrationen, Schema-Version und Heartbeat

C besitzt alle Tabellen der Datenbank `operations` und die Migrations-CLI `ops migrate`. Die Tabellen der SDK-Persistenz bindet C nach D's Prüfung in dieselbe Migrationsfolge ein. D's Chart führt `ops migrate` als Helm-Hook-Job mit `pre-install,pre-upgrade` aus. API und Worker vergleichen beim Start nur die Schema-Version und beenden sich bei Abweichung mit erwarteter und vorhandener Version. Damit migriert nie mehr als ein Prozess.

Der Worker schreibt mindestens jede Minute einen Heartbeat (Worker-ID, Datenbankzeit) in eine eigene Tabelle, aus einem eigenen Task unabhängig vom laufenden Auftrag. `GET /healthz` liest den jüngsten Heartbeat: 503 bei Datenbankfehler, fehlendem oder mehr als 10 Minuten altem Heartbeat, sonst 200. A's Verfügbarkeitstest ruft den Endpunkt alle fünf Minuten auf und alarmiert `ops-humans`.

### 7. Laufnachweise

Ein Auftrag verknüpft Ereignisse, Vorfall, Operationen, Aktionen, externe Referenzen, Werkzeugaufrufe, Modellkonfiguration, Verbrauchsdaten und Verifikationsnachweise. Eine gemeinsame Abfragefunktion liefert daraus ein JSON. Abrufweg ist `GET /runs/{auftragsId}` mit `X-Api-Key` oder `ops runs show <id>`; beide nutzen dieselbe Funktion, die CLI funktioniert auch ohne laufende API. Secrets werden nie in Nachweise geschrieben; ein Test prüft die Ausgabe zusätzlich gegen die bekannten Secret-Werte.

## Risks / Trade-offs

- Externe Aktion und Datenbank-Commit sind nicht atomar. Vorab-Protokoll, stabile Korrelation, Adapterabgleich und sichtbarer Klärungsfall verhindern blindes Wiederholen.
- Lease-Verlust stoppt keine bereits angenommene externe Aktion. Ein neuer Besitzer gleicht sie zuerst ab.
- Der Query-Key von Azure Monitor kann in Proxy- oder Ingress-Logs landen. Die API maskiert ihn; Ingress-Zugriffslogs sind für Staging als Risiko akzeptiert.
- Ohne quellenübergreifende Korrelation kann derselbe Fehler als Alarm und als Issue zwei Aufträge erzeugen. Die Sperre verhindert parallele Änderungen; der zweite Auftrag endet bei Konflikt in `needs_attention`.
- Break-glass-Deployments laufen ungeschützt, bis der Nachtrag erfolgt. Der Vermerk in der Workflow-Zusammenfassung macht sie auffindbar.
- Direkter Datenbankzugriff aus Actions mit Admin-Passwort ist für Staging akzeptiert (A: öffentlicher Endpunkt, TLS, Passwort).
- Gemeinsame Python-Dateien würden Konflikte erzeugen. C besitzt Verträge, API, Persistenz und Worker; D besitzt Agent, Tools und Paketmanifest.

## Migration Plan

1. Ereignis-, Aufruf-, Aktions- und Sperrverträge mit Beispielen veröffentlichen; lokal PostgreSQL und `ops migrate` bereitstellen.
2. API, Duplikaterkennung, Worker-Leases, Heartbeat und Zustandsregeln mit vertragskonformer Testausführung prüfen.
3. D's SDK-Ausführung anbinden; SDK-Tabellen in die Migrationsfolge aufnehmen.
4. Migrations-CLI und Sperr-CLI an B und D übergeben; gegen A's Datenbank von einem GitHub-hosted Runner testen.
5. Webhook einrichten lassen und echte Ereignisse anschließen. Danach Wiederanlauf, Mehrfachzustellung und konkurrierende Änderung mit B und D nachweisen.
6. Schemaänderungen nur kompatibel ausrollen; der Hook-Job läuft vor neuen Prozessen. Ein Code-Rollback setzt Schema-Kompatibilität voraus und löscht weder Aufträge noch Aktionsnachweise.
