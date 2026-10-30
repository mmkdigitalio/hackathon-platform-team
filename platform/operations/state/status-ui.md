# Plattformstatus

Die lesende Statusübersicht ist Teil des vorhandenen Operations-Agent-Images.
Auf Azure läuft sie als eigenständige App `platform-status`, damit ein fehlender
Agentenzugang oder ein ausgefallener Worker ihre Bereitstellung nicht verhindert.
`GET /` liefert die UI, `GET /status` die dazugehörige JSON-Projektion.
Der eigenständige Dienst bietet keinen Ereigniseingang, keine Betriebsaktionen und
keine Laufdetails. `/healthz` prüft dort nur den UI-Prozess. Die vorhandene Agenten-API
enthält weiterhin dieselbe UI; ihre bisherigen Endpunkte und Schutzmaßnahmen bleiben erhalten.

## Daten und Grenzen

- Anwendungen kommen aus `platform/catalog/` und dem Sollzustand unter `platform/environments/<environment>/` im gebauten Image. Die angezeigte Sollversion ist keine Abfrage der laufenden Container-Version.
- HTTP-Prüfungen nutzen `<app-id>.<OPS_BASE_DOMAIN>` und den `availability.path` aus dem Katalog. Nur HTTP 200 gilt als erreichbar. TLS wird geprüft, Redirects werden nicht verfolgt; Antwortkörper werden nicht geladen.
- Datenbankstatus, Worker-Heartbeat, aktive Aufträge, abgeschlossene und fehlgeschlagene/eskalierte Aufträge der letzten 24 Stunden sowie die sechs zuletzt aktualisierten Aufträge kommen aus der Operations-Datenbank. Auftragsabfragen sind auf die gewählte Umgebung beschränkt. Der Heartbeat entspricht wie `/healthz` dem jüngsten Worker-Heartbeat in dieser Datenbank.
- Die Datenbankabfragen laufen in einer lesenden Transaktion mit einem Statement-Timeout von 2,5 Sekunden. HTTP-Prüfungen laufen parallel mit 4 Sekunden Timeout.
- Die öffentliche Projektion enthält App-ID, Auftragszustand und Aktualisierungszeit. Sie enthält keine Auftrags- oder Vorfalltexte, IDs, Logs, Modellantworten, Secret-Werte oder rohe Fehlermeldungen. Der Nachweis-Endpunkt `/runs/{job_id}` bleibt in der Agenten-API geschützt und ist im eigenständigen Statusdienst nicht vorhanden.
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

`agent-build.yml` baut das gemeinsame Image. `status-ui-package.yml` prüft und veröffentlicht das eigenständige Helm-Chart unter `platform/infra/status-ui/`. Das Chart startet nur den Statusdienst aus dem geprüften Image. Es startet keinen Worker, führt keine Migration aus, benötigt keinen GitHub- oder Modellzugang und mountet kein Kubernetes-ServiceAccount-Token. Die Datenbank wird ausschließlich über die lesende Statusprojektion abgefragt.

`platform/catalog/platform-status/app.yaml` und `platform/environments/staging/platform-status.yaml` definieren die App und ihre festen Release-Referenzen. `app-deploy.yml` rollt sie über den bestehenden Pulumi-App-Pfad mit Operations-Sperre, Plan und Smoke-Test aus. Adresse: `https://platform-status.<baseDomain>/`.

Ein vollständiger Agenten-Rollout braucht weiterhin die Foundation-Ausgabe `githubAppPrivateKey`, das geschützte CI-Secret `OPS_GITHUB_APP_PRIVATE_KEY` oder den dokumentierten PAT-Rückfall. Die Status-UI ersetzt diesen Zugang nicht und zeigt den Agenten bei fehlender Erreichbarkeit als gestört an.

Bei ausgefallenem gemeinsamem Ingress ist auch die Status-UI nicht erreichbar; sie ersetzt kein unabhängiges externes Monitoring. Nach neuen Builds wird der gewünschte Image-Digest im Sollzustand explizit aktualisiert.

## Staging-Nachweis vom 30.10.2026

- Adresse: https://platform-status.57-153-7-126.sslip.io/
- Pulumi-Stack: `organization/app-platform-status/staging`, Namespace `platform-status-staging`.
- Release-Commit: `fbe2a019e6edc508119f0c8107357aeaa227622f`, Chart `0.1.1`, Image `sha256:52b48976e5433616416de5d4414544b8a82f9b47ded281f7b4dcac85963d3aad`.
- [IaC-Rollout 41107859138](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41107859138) erfolgreich, ohne Break-glass. Laufender Stand entspricht dem Sollzustand; Smoke-Test auf `/status` erfolgreich.
- Öffentliche HTTPS-Prüfung mit gültigem Zertifikat: `/`, `/healthz`, `/status` und JavaScript-Asset jeweils HTTP 200.
- Live-Daten zum Prüfzeitpunkt: AFFiNE erreichbar, Operations-Datenbank lesbar; Operations-Agent nicht erreichbar und kein Worker-Heartbeat. Das ist der angezeigte Plattformzustand, kein Fehler des UI-Rollouts. Der vollständige Agenten-Rollout ist separat durch die fehlende Foundation-Ausgabe `githubAppPrivateKey` blockiert.
