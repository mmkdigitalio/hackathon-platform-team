# Ereigniseingang

## Endpunkte

| Endpunkt | Schutz | Antwort |
| --- | --- | --- |
| `POST /events/azure-monitor?key=<alertWebhookKey>` | Query-Key | 202 angenommen, 200 Duplikat, 400 kein Common Alert Schema, 401 Key fehlt/falsch, 503 nicht gespeichert |
| `POST /events/github` | `X-Hub-Signature-256` (HMAC-SHA256 über den unveränderten Body mit `githubWebhookSecret`) | 202 angenommen oder ignoriert, 200 Duplikat, 401 Signatur, 403 Repository nicht in der Allowlist, 503 |
| `POST /events/maintenance` | `X-Api-Key: <maintenanceApiKey>` | wie oben |
| `GET /runs/{auftragsId}` | `X-Api-Key: <maintenanceApiKey>` | 200 JSON, 401, 404 |
| `GET /healthz` | keiner | 200 oder 503, siehe [worker.md](worker.md) |

Antwort einer Annahme:

```json
{"status": "accepted", "eventId": "…", "incidentId": "…", "jobId": "…", "mappingError": null}
```

`status` ist `accepted`, `duplicate` (gleiche `source` + `deliveryId`, verweist auf denselben Vorgang) oder `ignored` (mit `reason`). Eine 2xx-Antwort kommt erst nach dem Commit. Bei Datenbankfehlern antwortet der Eingang mit 503 und ohne `jobId`; Azure Monitor und GitHub stellen dann erneut zu.

Keys werden in konstanter Laufzeit verglichen. Die API protokolliert Requests selbst und schreibt `?key=***`; das Zugriffslog von uvicorn ist abgeschaltet. Ingress-Zugriffslogs können den Query-Key enthalten, das ist für Staging akzeptiert.

## Ereignisvertrag v0.1

Schema: [`platform/schemas/events/event.schema.json`](../../schemas/events/event.schema.json). Beispiele unter `examples/valid/`, `examples/invalid/` (mit erwartetem Fehlerfeld) und die unveränderten Quelldaten unter `examples/payloads/`.

```bash
ops events validate platform/schemas/events/examples/valid/*.json
ops events validate --expect-invalid platform/schemas/events/examples/invalid/*.json   # prüft auch das erwartete Fehlerfeld
```

| Quelle | `deliveryId` | `externalRef` / Vorfall | `state` |
| --- | --- | --- | --- |
| Azure Monitor | `<essentials.alertId>:<monitorCondition>` | `essentials.alertId` | `fired`, `resolved` |
| GitHub | Header `X-GitHub-Delivery` | `<owner>/<repo>#<issue>` | `opened`, `commented` |
| Wartung | Feld `deliveryId` des Aufrufers | Feld `externalRef` des Aufrufers | `requested` |

Vorfallzuordnung nur innerhalb einer Quelle: gleiche Alarm-ID → gleicher Vorfall, alle Kommentare eines Issues → Vorfall des Issues, gleiche `externalRef` eines Wartungsauftrags → gleicher Vorfall. Ein Alarm und ein Issue zur selben App ergeben zwei Vorfälle.

Je Vorfall gibt es höchstens einen aktiven Auftrag. Weitere Ereignisse hängen sich an ihn. Ist der letzte Auftrag abgeschlossen, legt ein neues Fired-, Issue- oder Kommentar-Ereignis einen neuen an. Ein Resolved-Alarm setzt nur `incidents.alert_state` und schließt nie einen Auftrag.

## App-Zuordnung

- Azure Monitor: Custom Properties `app-id` und `environment`, sonst Regelname `alert-<app-id>-<env>-<check>`, mit `<env>` ∈ `staging`, `production`.
- GitHub: gerenderte Abschnitte `### App` und `### Umgebung` der [Issue-Form](../../../.github/ISSUE_TEMPLATE/support.yml).
- Wartung: Felder `appId` und `environment`.

Danach Katalogprüfung: `platform/catalog/<app-id>/app.yaml` und `platform/environments/<env>/<app-id>.yaml` müssen existieren. Sonst wird das Ereignis mit `mappingError` gespeichert, der Auftrag direkt in `needs_attention` mit Grund „Zuordnungsfehler: …“ angelegt, und es gibt keine Zieländerung.

## Schleifenschutz

Kommentare und Issues, deren Text `<!-- mmk-ops-agent -->` enthält, werden ignoriert (Antwort `ignored`). Der Autor zählt nicht: Der Agent schreibt per PAT unter einem Teammitglied (E7), dessen eigene Kommentare ohne Markierung normal verarbeitet werden. Kommentare zu Pull Requests werden ignoriert.

## GitHub-Webhook einrichten (Repo-Admin, einmalig)

1. Werte aus der Foundation lesen: `operationsApiHost` und `githubWebhookSecret` (lokal mit Pulumi, siehe `platform/operations/foundation/`).
2. GitHub → `mmkdigitalio/hackathon-platform-team` → Settings → Webhooks → Add webhook:
   - Payload URL: `https://<operationsApiHost>/events/github` (bei E8-Fallback `http://`)
   - Content type: `application/json`
   - Secret: Wert von `githubWebhookSecret`
   - SSL verification: aktiv (bei HTTP-Fallback entfällt sie)
   - Events: „Let me select individual events“ → nur **Issues** und **Issue comments**
   - Active: an
3. GitHub sendet ein `ping`; unter „Recent Deliveries“ muss es 202 mit `"status": "ignored"` zeigen.
4. Ein Test-Issue über die Form „Support-Anfrage“ anlegen; die Zustellung muss 202 mit `"status": "accepted"` und `jobId` zeigen.

Wird das Secret rotiert, zuerst das Kubernetes-Secret des Agenten (App-Stack) und dann den Webhook aktualisieren; dazwischen abgewiesene Zustellungen lassen sich unter „Recent Deliveries“ erneut senden („Redeliver“, gleiche `X-GitHub-Delivery`, wird dedupliziert).

## Beispielzustellungen

Voraussetzung: API läuft, Variablen gesetzt.

```bash
API=https://$OPERATIONS_API_HOST            # lokal: http://127.0.0.1:8080
EX=platform/schemas/events/examples/payloads

# Azure Monitor (Fired, dann Resolved derselben Alarm-ID)
curl -s -X POST "$API/events/azure-monitor?key=$OPS_ALERT_WEBHOOK_KEY" \
  -H 'Content-Type: application/json' --data-binary @$EX/azure-monitor-fired.json
curl -s -X POST "$API/events/azure-monitor?key=$OPS_ALERT_WEBHOOK_KEY" \
  -H 'Content-Type: application/json' --data-binary @$EX/azure-monitor-resolved.json

# Ohne Key: 401, kein Auftrag
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/events/azure-monitor" \
  -H 'Content-Type: application/json' --data-binary @$EX/azure-monitor-fired.json

# GitHub-Issue mit Signatur
sig=$(openssl dgst -sha256 -hmac "$OPS_GITHUB_WEBHOOK_SECRET" < $EX/github-issue-opened.json | sed 's/^.* //')
curl -s -X POST "$API/events/github" -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: issues' -H "X-GitHub-Delivery: $(uuidgen)" \
  -H "X-Hub-Signature-256: sha256=$sig" --data-binary @$EX/github-issue-opened.json

# Agentenkommentar mit Markierung: ignored
sig=$(openssl dgst -sha256 -hmac "$OPS_GITHUB_WEBHOOK_SECRET" < $EX/github-comment-agent-marked.json | sed 's/^.* //')
curl -s -X POST "$API/events/github" -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: issue_comment' -H "X-GitHub-Delivery: $(uuidgen)" \
  -H "X-Hub-Signature-256: sha256=$sig" --data-binary @$EX/github-comment-agent-marked.json

# Wartungsauftrag
curl -s -X POST "$API/events/maintenance" -H "X-Api-Key: $OPS_MAINTENANCE_API_KEY" \
  -H 'Content-Type: application/json' --data-binary @$EX/maintenance-health-check-failed.json
```

`--data-binary` ist wichtig: Die Signatur gilt für die Bytes der Datei.

## Gesundheitscheck

[`health-check.yml`](../../../.github/workflows/health-check.yml) läuft alle 30 Minuten und manuell. Er liest `baseDomain`, `operationsApiHost` und `maintenanceApiKey` über `.github/actions/foundation-outputs` und ruft `ops health-check` auf. Das prüft `https://<app-id>.<baseDomain><availability.path>` für jede Katalog-App außer `operations-agent` (den überwacht A's Test auf `/healthz`). Die Zusammenfassung nennt App, Umgebung, Prüfung und Zeitpunkt. Nur bei Fehlschlag geht ein Wartungsauftrag mit `externalRef` `health-check:<app>:<env>:availability` raus; wiederholte Fehlschläge hängen sich an den offenen Auftrag.

## Fehlerdiagnose

| Symptom | Ursache und Prüfung |
| --- | --- |
| Azure Monitor: 401 | Key in der Action Group ≠ `OPS_ALERT_WEBHOOK_KEY` im Pod. URL der Action Group mit Foundation-Ausgabe vergleichen. |
| GitHub: 401 | Webhook-Secret ≠ `OPS_GITHUB_WEBHOOK_SECRET`; oder ein Proxy verändert den Body. |
| GitHub: 403 | Repository nicht in `OPS_GITHUB_REPO_ALLOWLIST`. |
| GitHub: 202 `ignored` | Ereignistyp nicht `issues.opened`/`issue_comment.created`, Agentenmarkierung oder PR-Kommentar; `reason` lesen. |
| 400 | Kein Common Alert Schema (Action Group: „Enable the common alert schema“) bzw. Pflichtfeld fehlt. |
| 503 bei Annahme | PostgreSQL nicht erreichbar oder Schema fehlt; Pod-Log `Ereignis nicht gespeichert`, dann `/healthz`. Quelle stellt erneut zu. |
| `mappingError` gesetzt | App/Umgebung fehlen oder stehen nicht im Katalog; Custom Properties der Alarmregel bzw. Issue-Abschnitte prüfen. Auftrag steht in `needs_attention`. |

SQL-Schnellblick:

```sql
SELECT received_at, source, state, app_id, mapping_error, job_id FROM events ORDER BY received_at DESC LIMIT 20;
SELECT id, state, state_reason, app_id, attempts, lease_owner, lease_expires_at FROM jobs ORDER BY created_at DESC LIMIT 20;
```
