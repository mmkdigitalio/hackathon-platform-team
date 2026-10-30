# Den Betriebsagenten testen

Drei Stufen. Stufe 1 bis 3 laufen heute lokal, Stufe 4 braucht Staging und die Lieferungen von A, B und C.

Alle Befehle aus `operations-agent/`. Für Stufen mit echtem Modell vorher den Azure-Key setzen (der Wert erscheint nirgends):

```bash
export OPS_MODEL_ENDPOINT=https://mmk-hackathon-openai.openai.azure.com/ OPS_MODEL_NAME=gpt-6.1-terra
export AZURE_OPENAI_API_KEY="$(az cognitiveservices account keys list --subscription 'MMK DIGITAL - Hackathon 2026' \
  -g hackathon-openai -n mmk-hackathon-openai --query key1 -o tsv)"
```

## Stufe 1: Unit- und Adaptertests (Sekunden, ohne Netz)

```bash
uv sync --frozen && uv run pytest -q
```

Echter Deep-Agents-Graph mit vorgegebenen Modellantworten, simuliertem Cluster und simuliertem GitHub. Deckt Grenzen, Sperre, Abgleich, eingeschleuste Anweisungen und die Review-Regressionen ab. Optional gegen ein lokales PostgreSQL: `OPS_TEST_POSTGRES_DSN=postgresql://…/postgres uv run pytest tests/agent/test_persistence.py`.

## Stufe 2: Prototyp mit echtem Modell (eine Minute, ca. 0,01 USD)

```bash
uv run ops-agent-prototype --url https://example.com
```

Erwartet: Exit-Code 0, ein `http_probe`-Aufruf, Status 200 in `toolResults`.

## Stufe 3: Lokaler Cluster mit echtem Modell (ca. 5 Minuten, ca. 0,10 USD je Szenario)

Voraussetzungen: Docker Desktop läuft, `kind` und `helm` sind installiert. Die Skripte schreiben eigene Kubeconfigs nach `operations-agent/.kind/`; `~/.kube/config` und der dort aktive Kontext bleiben unberührt.

```bash
tests/agent/kind/setup.sh                                   # Registry, Cluster, Agenten-Chart, AFFiNE-Ersatz (nginx)
KUBECONFIG=.kind/admin.kubeconfig tests/agent/kind/rbac-check.sh
PYTHONPATH=. uv run python tests/agent/kind/e2e.py repair        # erwartet resolved
PYTHONPATH=. uv run python tests/agent/kind/e2e.py unrepairable  # erwartet needs_attention
tests/agent/kind/teardown.sh                                # Cluster und Registry entfernen
```

Was dabei echt ist und was nicht:

| Echt | Simuliert |
| --- | --- |
| Azure-Modell, Deep-Agents-Ausführung | GitHub (Katalog, Sollzustand, Commit, Dispatch) |
| Kubernetes-API mit dem Token des ServiceAccounts `operations-agent` und der Chart-RBAC | `app-deploy.yml`: der simulierte Lauf skaliert das Deployment per Admin-Zugang zurück |
| Smoke-Test als Kubernetes-Job mit `TARGET_URL`, Exit-Code und JSON | C's Dienste (Aktionen, Sperre, Zustand) als Speicherobjekte |
| Helm-Install mit Hook-Job, zwei Deployments aus einem Digest | AFFiNE (nginx als Ersatz), C's Einstiegspunkte (Platzhalterbefehle) |

Ergebnis am 30.10.2026:

| Szenario | Ergebnis |
| --- | --- |
| RBAC (`rbac-check.sh`) | 20 von 20 Prüfungen wie erwartet (erlaubt: Lesen, `patch deployments`, Jobs anlegen/löschen; verweigert u. a. Secrets, `pods/exec`, Deployments löschen) |
| `repair`: Deployment auf 0 | `resolved`: Soll-/Istvergleich `scaled_to_zero`, ein Dispatch, Smoke-Job HTTP 200, 1/1 bereit, Soll-Digest; 7 Modellaufrufe, ca. 0,12 USD |
| `unrepairable`: nginx mit fehlender Konfigurationsdatei | `needs_attention`: kein Redeploy, kein Neustart, Smoke-Job „Connection refused“, Verweis auf Sollzustands-Commit und die echte `[emerg]`-Logzeile; 5 Modellaufrufe, ca. 0,10 USD |

## Stufe 4: Staging-Abnahme (Szenarien 4 und 5)

Erst möglich, wenn diese Lieferungen da sind:

| Von | Lieferung |
| --- | --- |
| A | Foundation ausgerollt: AKS, ACR mit Admin-User, PostgreSQL, `baseDomain`, `modelApiKeys` (mit `azure-openai`), `opsGithubToken`, Composite Action `foundation-outputs`, Verfügbarkeitstest und Alarm für AFFiNE |
| B | Katalog und Sollzustand für `affine` und `operations-agent`, `app-deploy.yml` mit `operationId`/`actionId` im `run-name`, `app-update-desired-state.yml`, App-Stack des Agenten (Secret, Pull-Secret, Ingress), Fehlerskripte |
| C | Vertrag v0.1 übernommen, API, Worker, `ops migrate`, Sperrclient, Aktionsprotokoll |
| AFFiNE-Kollege | Chart, Smoke-Test-Image, Runbook |

Ablauf, sobald alles steht:

1. Agentenpaket über `agent-build.yml` auf `main` bauen; B rollt es per `app-deploy.yml` aus. Prüfen: `kubectl --context <staging> -n operations-agent-staging get deploy,pods`, `/healthz` liefert 200.
2. RBAC im echten Cluster: `KUBECONFIG=<staging-kubeconfig> tests/agent/kind/rbac-check.sh`.
3. **Szenario 4:** B's Skript skaliert `affine` in `affine-staging` auf 0. Erwartet: Verfügbarkeitsalarm, Auftrag bei C, ein `app-deploy.yml`-Lauf mit der `actionId` im Titel, positiver Smoke-Test, Auftrag `resolved`. Nachweis: `ops runs show <auftragsId>`.
4. **Szenario 5:** B's Skript committet einen ungültigen AFFiNE-Konfigurationswert. Erwartet: kein Redeploy, `needs_attention` mit SHA, Autor und Zeitpunkt des Commits sowie betroffenen Logzeilen.
5. Szenarien 6 bis 8 mit C: doppelte Zustellung, Worker-Neustart während eines Workflows, Sperrkonflikt mit laufendem Deployment.
