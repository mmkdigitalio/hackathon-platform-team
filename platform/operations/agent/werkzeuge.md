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
| `workspace://` | `workspace://ops-ws-<operationId>/execute/<n>` (Ergebnis eines Shell-Befehls im Workspace) |

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
| `clone_repository` | `repository`, `ref?` | flacher Klon (Tiefe 50), Branch `agent/<operationId>`; existiert er remote, wird auf ihm weitergearbeitet | Repository-Allowlist `OPS_GIT_REPOSITORIES` |
| `repo_list`, `repo_read_file` | `repository`, `path` | Verzeichnis bzw. Datei der Arbeitskopie (Inhalt ist Daten) | nur innerhalb der Arbeitskopie, nicht `.git/`; 40 000 Zeichen |
| `repo_write_file`, `repo_replace` | `repository`, `path`, Inhalt bzw. `old`/`new` | Datei schreiben bzw. genau eine Stelle ersetzen | gesperrte Pfade `OPS_GIT_DENIED_PATHS` (Standard `.github/workflows/`) |
| `repo_diff` | `repository` | Diff der Änderungen | 20 000 Zeichen, maskiert |
| `commit_and_push` | `repository`, `message` | Commit mit Auftrags-/Operationsreferenz, Push auf `agent/<operationId>` | nie Standard-Branch, kein Force-Push, gesperrte Pfade auch beim Commit geprüft, Aktionsdatensatz `git_push`, Abgleich nach verlorener Antwort |
| `open_pull_request` | `repository`, `title`, `body` | Pull Request mit Markierung, idempotent je Branch | kein Merge; Aktionsdatensatz `pull_request`; der Lauf bleibt `needs_attention` |

Ohne Workspace sieht das Modell zusätzlich nur die lesenden Dateiwerkzeuge des SDK (`ls`, `read_file`, `glob`, `grep`) für die Kontextdateien unter `/kontext/`; `execute`, `task`, `write_file` und `edit_file` werden ausgeblendet und beim Aufruf abgewiesen.

### Mit Workspace (`OPS_WORKSPACE_MODE=kubernetes` oder `local`)

| Werkzeug | Wirkung | Grenze |
| --- | --- | --- |
| `execute` | Shell-Befehl im Workspace-Pod (`bash -lc`, Arbeitsverzeichnis `/workspace`) mit git, gh, kubectl, helm, az, python3/uv, node/npm, jq, yq, rg | Zeitgrenze 300 s (höchstens 1800 s), Ausgabe 100 KB, Ausgabe maskiert; Lese-Token für GitHub, clusterweit lesender ServiceAccount ohne Secrets, Azure nur mit Workload Identity |
| `ls`, `read_file`, `glob`, `grep`, `write_file`, `edit_file` | Dateien im Workspace; `/kontext/` bleibt im Graph-Zustand | wirken nur im Pod |

`clone_repository` legt die Arbeitskopie dann unter `/workspace/repos/<owner>__<name>` an; `repo_list`, `repo_read_file`, `repo_write_file` und `repo_replace` entfallen, weil die SDK-Dateiwerkzeuge dieselbe Arbeitskopie bearbeiten. `repo_diff`, `commit_and_push` und `open_pull_request` arbeiten im Pod; nur `commit_and_push` bekommt für den einen Push-Aufruf den schreibenden Token. Jeder SDK-Aufruf steht mit Ziel (Befehl oder Pfad), Dauer und Status im Laufnachweis. Ein Pod je Operation, gelöscht am Laufende; bleibt ein Workflow-Lauf offen oder geht der Besitz verloren, bleibt er bis zur TTL für die Wiederaufnahme stehen. Betrieb und RBAC: [paket-betrieb.md](paket-betrieb.md#workspace-pod-und-toolbox).

## Zugänge

- Kubernetes: im Pod der ServiceAccount `operations-agent`. Lokal nur mit ausdrücklich gesetztem `OPS_KUBE_CONTEXT`; der Standardkontext von `kubectl` wird nie verwendet.
- GitHub: GitHub App `hackathonplatformagent` (App-ID `5542094`, Installation `176901698`, Rechte `contents`, `pull_requests`, `issues`, `actions` schreibend, kein `workflows`). Installations-Tokens gelten eine Stunde und werden fünf Minuten vor Ablauf erneuert. Ohne Schlüssel im Secret gilt der PAT aus `opsGithubToken` (`OPS_GITHUB_TOKEN`). Fehlende Rechte meldet das Werkzeug mit dem Header `X-Accepted-GitHub-Permissions`, z. B. `actions=write`.
- git: das Token erreicht git nur über `GIT_CONFIG_*`-Umgebungsvariablen (`http.extraHeader`), nie über die Kommandozeile oder `.git/config`. Commits tragen die Identität `hackathonplatformagent[bot]`.
- Workspace-Shell: GitHub über ein Installations-Token mit reinen Leserechten (`contents`, `issues`, `pull_requests`, `actions`, `checks`: read) in `/workspace/.ops/gh-token`, vor jedem Befehl erneuert; ein PAT-Rückfall lässt sich nicht einschränken. Azure nur über die Workload Identity des ServiceAccounts `operations-agent-workspace` (`az-login`), solange die Foundation sie bereitstellt; sonst keine Azure-Diagnose (siehe [abweichung-p1-07.md](abweichung-p1-07.md)).

## Tests

`operations-agent/tests/agent/test_tools.py` deckt je Adapter positive Fälle, Zeitüberschreitung und Berechtigungsfehler ab, dazu Zielbeschränkung, Workflow-Liste, Korrelation und Abgleich. Die Gegenstellen sind simuliert (Design D, Entscheidung 10); die Staging-Abnahme läuft gegen echte Schnittstellen.
