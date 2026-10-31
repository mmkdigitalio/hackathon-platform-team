# Plattformstatus

Die lesende Statusübersicht ist Teil des vorhandenen Operations-Agent-Images.
Auf Azure läuft sie als eigenständige App `platform-status`, damit ein fehlender
Agentenzugang oder ein ausgefallener Worker ihre Bereitstellung nicht verhindert.
Die Admin-Aufgaben sind über eigene URLs getrennt:

| URL | Aufgabe |
| --- | --- |
| `/` | Tagescheck und priorisierter Handlungsbedarf |
| `/applications` | Suche, Zustandsfilter und Anwendungsbestand |
| `/applications/<app-id>` | Zustand, dokumentierter Rollout, Monitoring und App-Verlauf |
| `/activity` | Verlauf nach Anwendung, Kategorie, Ergebnis und Zeitraum filtern |
| `/status` | Gemeinsame lesende JSON-Projektion |

User Stories und Journeys: `openspec/changes/platform-status-ui/user-journeys.md`.
Filter werden als URL-Parameter gespeichert und überstehen Reload und Browser-Zurück.
Die Detailseite hat Sprunglinks zu Zustand/Rollout, Überwachung und Aktivitäten.
Der eigenständige Dienst bietet keinen Ereigniseingang, keine Betriebsaktionen und
keine Laufdetails. `/healthz` prüft dort nur den UI-Prozess. Die vorhandene Agenten-API
enthält weiterhin dieselbe UI; ihre bisherigen Endpunkte und Schutzmaßnahmen bleiben erhalten.

## Daten und Grenzen

- Anwendungen kommen aus dem Live-Katalog oder ersatzweise aus `platform/catalog/` und `platform/environments/<environment>/` im gebauten Image. Die angezeigte Sollversion ist keine Abfrage der laufenden Container-Version.
- HTTP-Prüfungen nutzen `<app-id>.<OPS_BASE_DOMAIN>` und den `availability.path` aus dem Katalog. Nur HTTP 200 gilt als erreichbar. TLS wird geprüft, Redirects werden nicht verfolgt; Antwortkörper werden nicht geladen.
- Datenbankstatus, Worker-Heartbeat, aktive Aufträge, abgeschlossene und fehlgeschlagene/eskalierte Aufträge der letzten 24 Stunden sowie die sechs zuletzt aktualisierten Aufträge kommen aus der Operations-Datenbank. Auftragsabfragen sind auf die gewählte Umgebung beschränkt. Der Heartbeat entspricht wie `/healthz` dem jüngsten Worker-Heartbeat in dieser Datenbank.
- Die Datenbankabfragen laufen in einer lesenden Transaktion mit einem Statement-Timeout von 2,5 Sekunden. HTTP-Prüfungen laufen parallel mit 4 Sekunden Timeout.
- Die öffentliche Projektion enthält App-ID, Auftragszustand, feste Quellen-/Tätigkeitsbeschreibungen, Zeitpunkte, Verifikationskennzeichen und erlaubte Release-Metadaten. Sie enthält keine Auftrags- oder Vorfalltexte, IDs, Logs, Modellantworten, Secret-Werte oder rohe Fehlermeldungen. Der Nachweis-Endpunkt `/runs/{job_id}` bleibt in der Agenten-API geschützt und ist im eigenständigen Statusdienst nicht vorhanden.
- Der Browser aktualisiert alle 30 Sekunden. Der API-Prozess bündelt Abfragen über einen Cache von 10 Sekunden. Nach fehlgeschlagenem Abruf oder mehr als 90 Sekunden ohne frische Daten zeigt die Seite den Gesamtzustand als unbekannt und markiert den letzten Stand als veraltet.
- Unbekannte Werte werden als Strich dargestellt. Ein gesunder Zustand besagt nur, dass die geprüften Komponenten antworten. Es werden keine historischen Uptime-Werte oder funktionalen Prüfungen erfunden.

## Konfiguration

Nach jedem erfolgreichen App-Deployment veröffentlicht `app-deploy.yml` den vollständigen Katalog und die Sollzustände der Umgebung als ConfigMap `platform-catalog` in vorhandene Agenten- und Status-Namespaces. API, Worker und Statusdienst mounten sie unter `/app/platform-live` über `OPS_CATALOG_SNAPSHOT_DIR`; neue Apps erscheinen nach ihrem ersten erfolgreichen Deployment in UI und Chat, sobald Kubernetes das Volume aktualisiert und der Status-Cache von 10 Sekunden abläuft. `/status` nennt unter `catalogSource` Quelle und Git-SHA. Fehlt ein gültiger Snapshot, gilt der Katalog aus dem Image. Manuell erneut veröffentlichen: `gh workflow run app-deploy.yml -f appId=<any> -f environment=staging`, wobei `<any>` eine vorhandene App-ID ersetzt und diese App erneut ausgerollt wird.

| Variable | Bedeutung |
| --- | --- |
| `OPS_BASE_DOMAIN` | Foundation-Ausgabe, z. B. `57-153-7-126.sslip.io`; kein fest eingebauter Host |
| `OPS_PUBLIC_SCHEME` | `https`, alternativ dokumentierter `http`-Fallback; nutzt ersatzweise `OPS_HTTP_SCHEME` |
| `OPS_STATUS_ENVIRONMENT` | `staging` oder `production`, Standard `staging`; das Helm-Chart übernimmt `platform.environment` vom App-Stack |
| `OPS_PLATFORM_ROOT` | Katalog und Sollzustände, bereits Teil des Agenten-Images |
| `OPS_CATALOG_SNAPSHOT_DIR` | Optionaler Live-Katalog aus der ConfigMap, im Cluster `/app/platform-live` |
| `OPS_DATABASE_URL` / `POSTGRES_*` | Vorhandene Datenbankkonfiguration der API |

## Lokal starten

Aus dem Repository-Wurzelverzeichnis:

```bash
cd operations-agent
uv sync --frozen
uv run python scripts/preview-status.py --base-domain 57-153-7-126.sslip.io
```

Vorschau unter `http://127.0.0.1:8088`. Ohne Datenbankkonfiguration werden echte öffentliche HTTP-Prüfungen ausgeführt, Datenbank und Worker bleiben unbekannt. Mit `OPS_DATABASE_URL` oder `POSTGRES_HOST` liest die Vorschau zusätzlich die konfigurierte Datenbank. Sie nimmt keine Ereignisse an und startet keinen Worker. `--environment production` wechselt die Umgebung, `--port` den lokalen Port.

Der zusätzliche Verlauf besteht aus den jüngsten 100 Erkennungen, Aktionen und Abschlüssen innerhalb von sieben Tagen. Die Filter 24 Stunden/sieben Tage beziehen sich auf diesen verfügbaren Ausschnitt. „Verifiziert abgeschlossen“ erfordert `resolved` und eine erfolgreiche jüngste Verifikation desselben Auftrags. Ein erfolgreicher System-Deploy ist keine automatische Problembehebung.

Monitoring-Konfiguration aus der Foundation-IaC, Live-HTTP-Prüfungen und empfangene Azure-Signale werden separat angezeigt. Die UI fragt keine Azure-Monitor-Aktivierung ab. Ein fehlendes Signal ist kein Beleg für fehlende Überwachung. Der letzte dokumentierte Rollout kommt aus `operations` und dem erlaubten Ausschnitt von `actions.detail.result`; er ist kein Live-Kubernetes-Inventar. Freie Meldungen, Rohpayloads, interne IDs und Targets bleiben verborgen. GitHub-Links sind auf Actions-Läufe des Plattform-Repositories begrenzt.

Der eigenständige Statusdienst bietet `/docs`, `/redoc` und `/openapi.json` nicht mehr an. Die Dokumentations-Endpunkte der Agenten-API bleiben unverändert.

Das Design übernimmt Farben, Typografie und Abstände aus `new-website/src/design-system/tokens/` sowie dessen lokal gehostete Schriften Bricolage Grotesque, Hanken Grotesk und JetBrains Mono. Die UI benötigt keinen Node-Build und keine zusätzlichen Python-Abhängigkeiten. CSS- und JavaScript-URLs enthalten einen Inhalts-Hash, damit bestehende Browser nach einem Deployment keine alten Assets mit neuer HTML-Struktur kombinieren. CSS, JavaScript und Fonts liegen im Python-Paket unter `api/static/` und damit im bestehenden Container-Image.

## Deployment

`agent-build.yml` baut das gemeinsame Image. `status-ui-package.yml` prüft und veröffentlicht das eigenständige Helm-Chart unter `platform/infra/status-ui/`. Das Chart startet nur den Statusdienst aus dem geprüften Image. Es startet keinen Worker, führt keine Migration aus, benötigt keinen GitHub- oder Modellzugang und mountet kein Kubernetes-ServiceAccount-Token. Die Datenbank wird ausschließlich über die lesende Statusprojektion abgefragt.

`platform/catalog/platform-status/app.yaml` und `platform/environments/staging/platform-status.yaml` definieren die App und ihre festen Release-Referenzen. `app-deploy.yml` rollt sie über den bestehenden Pulumi-App-Pfad mit Operations-Sperre, Plan und Smoke-Test aus. Adresse: `https://platform-status.<baseDomain>/`.

Ein vollständiger Agenten-Rollout braucht weiterhin die Foundation-Ausgabe `githubAppPrivateKey`, das geschützte CI-Secret `OPS_GITHUB_APP_PRIVATE_KEY` oder den dokumentierten PAT-Rückfall. Die Status-UI ersetzt diesen Zugang nicht und zeigt den Agenten bei fehlender Erreichbarkeit als gestört an.

Bei ausgefallenem gemeinsamem Ingress ist auch die Status-UI nicht erreichbar; sie ersetzt kein unabhängiges externes Monitoring. Nach neuen Builds wird der gewünschte Image-Digest im Sollzustand explizit aktualisiert.

## Staging-Nachweis vom 30.10.2026

- Adresse: https://platform-status.57-153-7-126.sslip.io/
- Pulumi-Stack: `organization/app-platform-status/staging`, Namespace `platform-status-staging`.
- Release-Commit: `08abf33731ad2a8bc8cc9416c2fb7287024e871b`, Chart `0.1.2`, Image `sha256:0f0fea7efe8e921c7eff347e85490d7eab27720a28b2ce57f1fa68e1bd924b1a`.
- [IaC-Rollout 41125342717](https://github.com/mmkdigitalio/hackathon-platform-team/actions/runs/41125342717) erfolgreich, ohne Break-glass. Laufender Stand entspricht dem Sollzustand; Smoke-Test auf `/status` erfolgreich.
- Öffentliche HTTPS-Prüfung mit gültigem Zertifikat: `/`, `/applications`, `/applications/affine`, `/activity`, `/healthz` und `/status` jeweils HTTP 200. Die Dokumentations-Endpunkte des eigenständigen Dienstes liefern HTTP 404.
- Live-Daten zum Prüfzeitpunkt 21:46 MEZ: drei Apps erreichbar, Datenbank lesbar, Worker-Heartbeat vorhanden, keine aktiven oder eskalierten Aufträge. Elf strukturierte Aktivitäten und verifizierte Rollout-Nachweise für alle drei Apps verfügbar. Das ist eine Momentaufnahme, keine Uptime-Zusage.
- Browserprüfung im bereits geöffneten Tab nach normalem Neuladen erfolgreich. App-Suche, Detailanalyse, Monitoring, Browser-Zurück mit erhaltenem Suchfilter und appbezogener Systemaktivitätenfilter geprüft. Die Asset-Versionierung behebt dabei den im ersten Rollout festgestellten Konflikt mit alten Browser-Caches.
- CI des ausgelieferten Images: 304 Tests bestanden, einer übersprungen; Image-Prüfstufe: 192 bestanden, 113 übersprungen. Lokale Status-/Admin-Suite: 20 bestanden. OpenSpec-Validierung mit `--strict` erfolgreich.
