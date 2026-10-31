# Mitarbeiter-Chat

Der Chat auf der App-Detailseite nimmt Meldungen mit Name und Text entgegen. Eine Unterhaltung ist ein Portal-Vorfall; weitere Nachrichten hängen am offenen Auftrag. Nach Abschluss beginnt ein neuer Auftrag mit dem bisherigen Verlauf. Der Agent schreibt Antworten und Ergebnisse in denselben Chat. Wünsche im Text ändern keine Betriebsregeln oder erlaubten Aktionen.

## Zugangscodes verwalten

Vor dem Start von API und Worker mit dem neuen Image `ops migrate` ausführen. API und Worker prüfen beim Start Schema-Version 2 sowie das Checkpoint-Schema.

Im Staging-Cluster den Worker-Pod auswählen und dessen konfigurierte Datenbankverbindung nutzen:

```bash
kubectl -n operations-agent-staging get pods
kubectl -n operations-agent-staging exec <worker-pod> -- ops portal-code create --label 'Team Support' --expires-days 30
kubectl -n operations-agent-staging exec <worker-pod> -- ops portal-code list
kubectl -n operations-agent-staging exec <worker-pod> -- ops portal-code revoke <code-id>
```

`create` gibt ID und Code einmalig als JSON aus. Den Code vertraulich an Mitarbeitende weitergeben. Nur dessen SHA-256-Hash bleibt gespeichert; `list` enthält ID, Bezeichnung, Anlage, Ablauf, Widerruf und letzte Nutzung. Ohne `--expires-days` läuft ein Code nicht automatisch ab. Widerruf wirkt sofort für weitere Anfragen und erhält bestehende Verläufe.

Alternativ lokal mit Zugang zur Staging-Datenbank, etwa über VPN:

```bash
cd operations-agent
export POSTGRES_HOST='<staging-host>' POSTGRES_USER='<benutzer>' POSTGRES_DATABASE=operations
export POSTGRES_SSLMODE=require
read -s POSTGRES_PASSWORD
export POSTGRES_PASSWORD
uv run --frozen ops portal-code create --label 'Team Support' --expires-days 30
uv run --frozen ops portal-code list
uv run --frozen ops portal-code revoke <code-id>
```

Eine gesetzte `OPS_DATABASE_URL` hat Vorrang vor `POSTGRES_*`. Der Browser sendet den Code ausschließlich als `X-Portal-Code`. `GET /portal/session` prüft ihn und liefert die Bezeichnung.

## Herkunft und Grenzen

CORS gilt nur für `/portal/*`. Standard sind `https://platform-status.<baseDomain>` und `https://operations-agent.<baseDomain>` aus `OPS_BASE_DOMAIN`. `OPS_PUBLIC_SCHEME` bestimmt das Schema, ersatzweise `OPS_HTTP_SCHEME`, sonst `https`. `OPS_PORTAL_ALLOWED_ORIGINS` ersetzt die Liste durch kommagetrennte vollständige Ursprünge. Keine Wildcards verwenden. Erlaubt sind GET, POST, OPTIONS und die Header Content-Type und X-Portal-Code, ohne Credentials.

Pro Code werden höchstens 20 neue Meldungen innerhalb von zehn Minuten angenommen. Ein Retry mit derselben `clientMessageId` zählt nicht erneut. IDs bestehen aus 1 bis 100 Zeichen aus A-Z, a-z, 0-9, Punkt, Unterstrich oder Bindestrich und müssen portalweit eindeutig sein. Der Browser sollte zufällige UUIDs verwenden. Meldungen haben höchstens 4000 Zeichen, Namen 80. App und Umgebung müssen im Katalog stehen; ohne Umgebung gilt die einzige konfigurierte Umgebung der App, sonst staging.

## Fehlersuche

- 401: Header und Code prüfen. Abgelaufene und widerrufene Codes liefern dieselbe Antwort wie unbekannte Codes. Bei Bedarf neuen Code erzeugen.
- 429: Zehn-Minuten-Fenster abwarten. Das Limit gilt gemeinsam für alle Nutzer desselben Codes.
- 503: Datenbankverbindung, Pool, Migrationen und Pod-Logs prüfen. Die API bestätigt keine Speicherung. Mit derselben Client-ID erneut senden.
- Browser-CORS-Fehler: Seitenursprung einschließlich Schema und Port mit der erlaubten Liste vergleichen. Ein fremder Ursprung erhält keine Freigabe. Andere API-Pfade erhalten keine Portal-CORS-Header.

`GET /portal/conversations/<id>` benötigt ebenfalls einen gültigen Code. Es liefert Nachrichten und Auftragszustände, keine internen Befunde, Lease-Daten oder Ereignis-Payloads. Jeder gültige Code kann mit bekannter Unterhaltungs-ID deren Verlauf lesen.
