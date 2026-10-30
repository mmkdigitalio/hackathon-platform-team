# Plattformstatus

Die lesende Statusübersicht läuft im vorhandenen Operations-Agent-API-Prozess.
`GET /` liefert die UI, `GET /status` die dazugehörige JSON-Projektion.
Ereigniseingang, `/healthz`, `/runs/{job_id}` und `/docs` bleiben unter ihren bisherigen Pfaden verfügbar.
Die Seite enthält keine Betriebsaktionen.

## Daten und Grenzen

- Anwendungen kommen aus `platform/catalog/` und dem Sollzustand unter `platform/environments/<environment>/` im gebauten Image. Die angezeigte Sollversion ist keine Abfrage der laufenden Container-Version.
- HTTP-Prüfungen nutzen `<app-id>.<OPS_BASE_DOMAIN>` und den `availability.path` aus dem Katalog. Nur HTTP 200 gilt als erreichbar. TLS wird geprüft, Redirects werden nicht verfolgt; Antwortkörper werden nicht geladen.
- Datenbankstatus, Worker-Heartbeat, aktive Aufträge, abgeschlossene und fehlgeschlagene/eskalierte Aufträge der letzten 24 Stunden sowie die sechs zuletzt aktualisierten Aufträge kommen aus der Operations-Datenbank. Auftragsabfragen sind auf die gewählte Umgebung beschränkt. Der Heartbeat entspricht wie `/healthz` dem jüngsten Worker-Heartbeat in dieser Datenbank.
- Die Datenbankabfragen laufen in einer lesenden Transaktion mit einem Statement-Timeout von 2,5 Sekunden. HTTP-Prüfungen laufen parallel mit 4 Sekunden Timeout.
- Die öffentliche Projektion enthält App-ID, Auftragszustand und Aktualisierungszeit. Sie enthält keine Auftrags- oder Vorfalltexte, IDs, Logs, Modellantworten, Secret-Werte oder rohe Fehlermeldungen. Der bereits geschützte Nachweis-Endpunkt `/runs/{job_id}` bleibt geschützt.
- Der Browser aktualisiert alle 30 Sekunden. Der API-Prozess bündelt Abfragen über einen Cache von 10 Sekunden. Nach fehlgeschlagenem Abruf oder mehr als 90 Sekunden ohne frische Daten zeigt die Seite den Gesamtzustand als unbekannt und markiert den letzten Stand als veraltet.
- Unbekannte Werte werden als Strich dargestellt. Ein gesunder Zustand besagt nur, dass die geprüften Komponenten antworten. Es werden keine historischen Uptime-Werte oder funktionalen Prüfungen erfunden.

## Konfiguration

| Variable | Bedeutung |
| --- | --- |
| `OPS_BASE_DOMAIN` | Foundation-Ausgabe, z. B. `57-153-7-126.sslip.io`; kein fest eingebauter Host |
| `OPS_PUBLIC_SCHEME` | `https`, alternativ dokumentierter `http`-Fallback; nutzt ersatzweise `OPS_HTTP_SCHEME` |
| `OPS_STATUS_ENVIRONMENT` | `staging` oder `production`, Standard `staging`; das Helm-Chart übernimmt `platform.environment` vom App-Stack |
| `OPS_PLATFORM_ROOT` | Katalog und Sollzustände, bereits Teil des Agenten-Images |
| `OPS_DATABASE_URL` / `POSTGRES_*` | Vorhandene Datenbankkonfiguration der API |

## Lokal starten

Aus dem Repository-Wurzelverzeichnis:

```bash
cd operations-agent
uv sync --frozen
uv run python scripts/preview-status.py --base-domain 57-153-7-126.sslip.io
```

Vorschau unter `http://127.0.0.1:8088`. Ohne Datenbankkonfiguration werden echte öffentliche HTTP-Prüfungen ausgeführt, Datenbank und Worker bleiben unbekannt. Mit `OPS_DATABASE_URL` oder `POSTGRES_HOST` liest die Vorschau zusätzlich die konfigurierte Datenbank. Sie nimmt keine Ereignisse an und startet keinen Worker. `--environment production` wechselt die Umgebung, `--port` den lokalen Port.

Das Design übernimmt Farben, Typografie und Abstände aus `new-website/src/design-system/tokens/` sowie dessen lokal gehostete Schriften Bricolage Grotesque, Hanken Grotesk und JetBrains Mono. Die UI benötigt keinen Node-Build und keine zusätzlichen Python-Abhängigkeiten. CSS, JavaScript und Fonts liegen im Python-Paket unter `api/static/` und damit im bestehenden Container-Image.

## Deployment

Das bestehende `agent-build.yml` baut bei Änderungen unter `operations-agent/` Image und Chart. Der bestehende App-Pfad rollt den Operations-Agenten aus. Nach Deployment ist die Übersicht auf der Wurzel-URL des Operations-Agenten erreichbar, z. B. `https://operations-agent.<baseDomain>/`. Kein zusätzlicher Dienst und kein eigener Ingress sind nötig. Bei kaputtem Ingress ist auch die dort gehostete Übersicht nicht erreichbar; sie ersetzt kein unabhängiges externes Monitoring.

Das Hinzufügen der UI veröffentlicht noch kein neues Image. Lokale Änderungen müssen über den bestehenden Git-/CI-Pfad ausgeliefert werden.
