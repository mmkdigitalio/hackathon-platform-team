# Werkzeuge des Betriebsagenten

Alle Werkzeuge sind an genau einen Auftrag gebunden (App, Umgebung, `operationId`). Code: `operations-agent/src/agent/toolset.py` (Modellsicht) und `operations-agent/src/tools/` (Adapter).

## Ergebnisformat

Jedes Werkzeug liefert ein JSON-Objekt `ToolOutcome`:

| Feld | Bedeutung |
| --- | --- |
| `tool` | Werkzeugname |
| `status` | `ok` oder `error` |
| `target` | Ziel, z. B. `affine/staging` oder Dateipfad |
| `observed_at` | Zeitpunkt der Abfrage (UTC) |
| `error_kind` | bei Fehlern: `validation`, `permission`, `timeout`, `not_found`, `conflict`, `upstream`, `not_allowed` |
| `message` | Fehlermeldung ohne Secrets |
| `data` | Nutzdaten des Werkzeugs |
| `evidence_refs` | Evidenzreferenzen (siehe unten) |
| `operation_id`, `action_id` | Verknüpfung mit Operation und Aktion, falls zutreffend |

Logtext wird vor der Weitergabe an Modell und Ergebnis maskiert: Passwörter in Verbindungs-URLs, `password=`/`token=`/`api_key=`-Werte, Bearer-Tokens sowie die bekannten Werte von GitHub-PAT und Modell-Key.

Ein verweigerter Zugriff ist nie ein leeres Erfolgsergebnis. Eine HTTP-Antwort 503 der App ist ein Befund mit `status: ok` und `statusCode: 503`, kein Werkzeugfehler.

Fachliche Fehler gehen an das Modell zurück. Folgende Ereignisse beenden den Lauf sofort: erreichte Grenze, belegte Sperre, unklarer externer Ausgang, verlorener Besitz, von C abgelehnter Zustandsübergang.

## Evidenzreferenzen

| Präfix | Beispiel |
| --- | --- |
| `k8s://` | `k8s://affine-staging/deployment/affine@2026-10-30T10:01:02+00:00` |
| `probe:` | `probe:https://affine.<baseDomain>/info@2026-10-30T10:01:05+00:00` |
| `github://` | `github://mmkdigitalio/hackathon-platform-team/commit/<sha>` |
| `alert://` | `alert://<essentials.alertId>` |

Ein Modellbefund mit `certainty: confirmed` braucht Referenzen, die ein Werkzeug in diesem Lauf geliefert hat; sonst wird er als `hypothesis` gespeichert.

## Werkzeuge

| Werkzeug | Eingaben | Wirkung | Grenze im Code |
| --- | --- | --- | --- |
| `get_workload_state` | `namespace?` | Replicas (Soll/bereit), Image-Digests, Neustartzähler, Pod-Phasen, Bedingungen | nur Katalog-Namespaces |
| `get_events` | `namespace?` | letzte 30 Events | nur Katalog-Namespaces |
| `get_logs` | `namespace?`, `deployment?`, `tail_lines` (1–200), `since_minutes` (1–360) | Logausschnitte mit Zeitraum | höchstens 16 000 Zeichen |
| `http_check` | `app_id?`, `path?` | Status, Dauer, Zeitpunkt, Fehlerart | nur Hosts unter `baseDomain` |
| `read_repo_file` | `path` | Datei zum Git-Stand des Auftrags | 20 000 Zeichen |
| `desired_state_history` | – | letzter Commit auf die Sollzustandsdatei mit SHA, Autor, Zeitpunkt, Diff | – |
| `compare_desired_actual` | – | deterministischer Soll-/Istvergleich: `scaled_to_zero`, `image_drift`, `in_sync`, `unknown` | – |
| `restart_workload` | `reason` | Neustart über Annotation `ops.mmk/action-id` | Katalogaktion `restart`, Sperre, Reparaturversuch |
| `redeploy_desired_state` | `reason`, `workflow` | Dispatch von `app-deploy.yml`, Lauf verfolgen | Katalogaktion `redeploy`, Workflow-Liste, nur bei Abweichung, Reparaturversuch |
| `workflow_status` | `action_id` | tatsächlicher Laufstatus | nur Aktionen dieser Operation |
| `run_smoke_test` | – | Smoke-Test-Job plus Kubernetes-Zustand | Restlaufzeit |
| `record_finding` | `statement`, `certainty`, `evidence_refs` | Befund festhalten | Bestätigung nur mit bekannter Evidenz |
| `comment_on_issue` | `text` | Kommentar im ursprünglichen Issue mit `<!-- mmk-ops-agent -->` und Auftragsreferenz | nur bei Auftrag aus einem Issue |

Zusätzlich sieht das Modell die lesenden Dateiwerkzeuge des SDK (`ls`, `read_file`, `glob`, `grep`) für die Kontextdateien unter `/kontext/`. `execute`, `task`, `write_file` und `edit_file` werden ausgeblendet und beim Aufruf abgewiesen.

## Zugänge

- Kubernetes: im Pod der ServiceAccount `operations-agent`. Lokal nur mit ausdrücklich gesetztem `OPS_KUBE_CONTEXT`; der Standardkontext von `kubectl` wird nie verwendet.
- GitHub: Fine-grained PAT aus `opsGithubToken` (`OPS_GITHUB_TOKEN`). Fehlende Rechte meldet das Werkzeug mit dem Header `X-Accepted-GitHub-Permissions`, z. B. `actions=write`.
- Keine Azure-Werkzeuge (siehe [abweichung-p1-07.md](abweichung-p1-07.md)).

## Tests

`operations-agent/tests/agent/test_tools.py` deckt je Adapter positive Fälle, Zeitüberschreitung und Berechtigungsfehler ab, dazu Zielbeschränkung, Workflow-Liste, Korrelation und Abgleich. Die Gegenstellen sind simuliert (Design D, Entscheidung 10); die Staging-Abnahme läuft gegen echte Schnittstellen.
