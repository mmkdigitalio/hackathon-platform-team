# Agentenpaket: Image, Chart und Betrieb

## Image

`operations-agent/Dockerfile`, Build-Kontext ist das Repository-Root (die Betriebsregeln liegen in `platform/operations/agent/`):

```bash
docker build -f operations-agent/Dockerfile --target test .      # Tests im Image
docker build -f operations-agent/Dockerfile --target runtime -t operations-agent:dev .
```

Ein Image für alle Prozesse, Benutzer `10001`, Python 3.12.16, Abhängigkeiten aus `uv.lock` (`--frozen`). Den Startbefehl setzt das Chart; ohne Befehl beendet sich der Container mit Hinweis.

Das Runtime-Image enthält `git` für die Repository-Werkzeuge; Arbeitskopien liegen unter `OPS_GIT_WORKDIR` (`/tmp/ops-agent-git`, `emptyDir`). App-ID und Installations-ID sind Chart-Werte (`agent.githubAppId`, `agent.githubAppInstallationId`), der private Schlüssel nur im Secret.

Im Image liegen zum Stand des Builds: Betriebsregeln, `platform/catalog`, `platform/environments` und `platform/schemas/events` (`OPS_PLATFORM_ROOT=/app/platform`). C's API prüft damit App-Zuordnung und Ereignisschema. Eine neue App im Katalog wird deshalb erst nach einem Agenten-Build angenommen; `agent-build.yml` startet bei Änderungen unter `platform/catalog/**` und `platform/schemas/events/**`, bewusst nicht bei `platform/environments/**`, weil sein eigener Sollzustands-Commit sonst den nächsten Build auslöst. Die Teststufe (`--target test`) bildet die Repository-Struktur unter `/repo` nach und führt C's und D's Tests aus; Tests mit PostgreSQL werden dort übersprungen.

| Prozess | Befehl (Chart-Wert) | Eigentümer |
| --- | --- | --- |
| API | `api.command`, Standard `ops-api` | C |
| Worker | `worker.command`, Standard `ops-worker` | C |
| Migration | `migrate.command`, Standard `ops migrate` | C |

C hat die Einstiegspunkte unter diesen Namen geliefert; der Worker lädt D's Ausführung über `OPS_AGENT_EXECUTOR` (Standard `agent:execute`).

## Chart

`operations-agent/deploy/`, veröffentlicht als OCI-Chart `oci://<acrLoginServer>/helm/operations-agent`.

| Objekt | Zweck |
| --- | --- |
| Deployment `operations-agent-api` | API, Service `operations-agent` Port 80 → `http` |
| Deployment `operations-agent-worker` | genau ein Worker, Strategie `Recreate` |
| Job `operations-agent-migrate` | Helm-Hook `pre-install,pre-upgrade`, führt `ops migrate` aus, ohne ServiceAccount-Token |
| ServiceAccount `operations-agent`, ClusterRole und ClusterRoleBinding `operations-agent-<namespace>` | Kubernetes-Zugang der Werkzeuge |
| ConfigMap `operations-agent-config` | Modell, Grenzen, Agenteneinstellungen (keine Secrets) |

Nicht im Chart: Secrets und Ingress-Regel. Beides legt B's App-Stack an; das Secret muss vor dem Hook-Job existieren.

### Werte vom App-Stack

| Wert | Inhalt |
| --- | --- |
| `image.repository`, `image.digest` | Image nur per Digest (Schema prüft `sha256:`) |
| `secretName` | Kubernetes-Secret mit allen geheimen und Foundation-Werten; wird per `envFrom` geladen |
| `imagePullSecretName` | Pull-Secret für API, Worker und Hook-Job |
| `rolloutMarker` | `operationId` des Deploy-Laufs; erzwingt das Helm-Upgrade, ändert kein Pod-Template |
| `agent.appImagePullSecret` | Pull-Secret in App-Namespaces für Smoke-Test-Jobs |
| `model.*`, `limits.*` | Modellkonfiguration und Grenzen |

### Erwartete Schlüssel im Secret

Werden über die `secrets:`-Zuordnung in `platform/catalog/operations-agent/app.yaml` (B) aus `foundation:`-Referenzen befüllt:

| Schlüssel | Foundation-Ausgabe | Verbraucher |
| --- | --- | --- |
| `OPS_MODEL_API_KEYS` | `modelApiKeys` | D |
| `OPS_GITHUB_APP_PRIVATE_KEY` | `githubAppPrivateKey` (A), ersatzweise geschütztes Repository-Secret `OPS_GITHUB_APP_PRIVATE_KEY` über den App-Stack | D; PEM, auch mit maskierten Zeilenumbrüchen (`\n`) |
| `OPS_GITHUB_TOKEN` | `opsGithubToken` | D, nur noch Rückfall ohne App-Schlüssel |
| `OPS_BASE_DOMAIN` | `baseDomain` | D |
| `OPS_PUBLIC_SCHEME` | `publicScheme` | D (HTTP-Fallback nach E8) |
| `OPS_ACR_LOGIN_SERVER` | `acrLoginServer` | D (löst `acr:`-Smoke-Test-Images aus dem Katalog auf) |
| `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE` | `postgresHost`, `postgresAdminUser`, `postgresAdminPassword`, `postgresOperationsDatabase` | C (Namen legt C fest) |
| `ALERT_WEBHOOK_KEY`, `MAINTENANCE_API_KEY`, `GITHUB_WEBHOOK_SECRET` | gleichnamige Ausgaben | C (Namen legt C fest) |
| `DIAGNOSTICS_STORAGE_ACCOUNT`, `DIAGNOSTICS_CONTAINER`, `DIAGNOSTICS_STORAGE_KEY` | `diagnostics*` | C/D |

### Prüfung

```bash
helm lint operations-agent/deploy -f operations-agent/deploy/ci-values.yaml
helm template operations-agent operations-agent/deploy -f operations-agent/deploy/ci-values.yaml -n operations-agent-staging
```

RBAC im Staging-Cluster (Kontext ausdrücklich angeben, nie den Standardkontext):

```bash
SA=system:serviceaccount:operations-agent-staging:operations-agent
kubectl --context <staging> auth can-i get pods/log -n affine-staging --as $SA          # yes
kubectl --context <staging> auth can-i patch deployments -n affine-staging --as $SA     # yes
kubectl --context <staging> auth can-i create jobs -n affine-staging --as $SA           # yes
kubectl --context <staging> auth can-i delete deployments -n affine-staging --as $SA    # no
kubectl --context <staging> auth can-i get secrets -n affine-staging --as $SA           # no
kubectl --context <staging> auth can-i create pods/exec -n affine-staging --as $SA      # no
```

## Build und Übergabe

`.github/workflows/agent-build.yml`:

1. Pull Request und `main`: `uv sync --frozen`, Tests, `helm lint`/`helm template`.
2. Nur `main`, sobald die Repo-Secrets des State-Backends existieren (sonst wird der Schritt mit Hinweis in der Zusammenfassung übersprungen): Foundation-Ausgaben über `.github/actions/foundation-outputs` (Vertragsversion 1), Login mit dem ACR-Admin-User, Image mit Tag Git-SHA bauen und pushen, Digest ermitteln, Chart als `<major>.<minor>.<run_number>` nach `oci://<acr>/helm` pushen.
3. B's `app-update-desired-state.yml` schreibt Digest und Chart-Version in `platform/environments/staging/operations-agent.yaml`. Den Rollout übernimmt `app-deploy.yml`.

Keine Azure-Anmeldung, nur Repo-Secrets des State-Backends (über `secrets: inherit`); der Sollzustands-PR läuft mit einem Token der GitHub App, `OPS_GITHUB_TOKEN` ist nur Rückfall.

Ist `githubAppPrivateKey` in der Foundation noch nicht vorhanden oder leer, verwenden
`app-plan.yml` und `app-deploy.yml` das geschützte Repository-Secret
`OPS_GITHUB_APP_PRIVATE_KEY`. Der Wrapper `pulumi-stack.sh` schreibt es nur für
`operations-agent` per stdin und `pulumi config set --secret` in die Konfiguration
`app-runtime:githubAppPrivateKey`. Die vorhandene CI-Passphrase verschlüsselt den Wert;
das App-Programm reicht ihn als Secret an das Kubernetes-Secret weiter. Ein nichtleerer
Foundation-Schlüssel hat Vorrang. Andere Apps erhalten diesen CI-Schlüssel nicht.
`app-update-desired-state.yml` nutzt dieselbe Quellenreihenfolge für sein kurzlebiges
Installations-Token. Damit ist keine lokale Kenntnis der Pulumi-Passphrase erforderlich.

## Workspace-Pod und Toolbox

Im Modus `kubernetes` erzeugt der Worker je Operation einen Pod `ops-ws-<operation>` im Release-Namespace. Er startet das Toolbox-Image mit `sleep <ttl>` und führt Shell-Befehle über `pods/exec` aus. Der Worker-Speicher bleibt unverändert; die Befehle laufen im Workspace-Pod.

CI baut `<acr>/operations-agent-toolbox` mit Git-SHA-Tag und schreibt dessen Digest in das veröffentlichte Chart. `toolbox-info` und `/usr/local/share/ops-toolbox/README.md` im Image nennen Werkzeuge und Versionen. Die README entsteht beim Build aus den Dockerfile-Pins und installierten Paketversionen. Debian-Pakete kommen aus einem festen APT-Snapshot. Azure CLI liegt mit ihren Abhängigkeiten in `/opt/azure-cli`.

Der bestehende ServiceAccount `operations-agent` erhält zusätzlich Pod-Verwaltung und `pods/exec` ausschließlich im Release-Namespace. Die oben gezeigte Prüfung für `create pods/exec` ergibt daher nur bei `workspace.mode: off` weiterhin `no`. Der separate ServiceAccount `operations-agent-workspace` darf clusterweit nur `get`, `list`, `watch`, ohne Secrets und ohne `pods/exec`. Der Git-Hook im Image erlaubt Push-Ziele nur unter `refs/heads/agent/`; die verbindliche Prüfung bleibt im Worker.

Im Repository ist `workspace.mode` standardmäßig `off`; CI setzt beim Paketieren `kubernetes`, `workspace.image.repository` und `workspace.image.digest`. Die übrigen `workspace.*`-Werte steuern ServiceAccount, Ressourcen, ephemeren Speicher, TTL, Befehlsgrenzen und Azure-Identität. Standard sind 250m CPU/512Mi RAM als Request, 2Gi RAM als Limit, 5Gi Speicher, 300 Sekunden Befehls-Timeout, maximal 1800 Sekunden und 1 MiB Ausgabe je Befehl. Coding-Aufträge haben 1800 Sekunden Laufzeit, 150 Werkzeugaufrufe, zwei Reparaturversuche und 5 USD Budget.

Die bestehende ConfigMap wird per `envFrom` geladen. `OPS_WORKSPACE_MODE` ist immer gesetzt; die folgenden Variablen nur im Kubernetes-Modus:

| Variablen | Quelle |
| --- | --- |
| `OPS_WORKSPACE_IMAGE` | `workspace.image.repository@workspace.image.digest` |
| `OPS_WORKSPACE_NAMESPACE` | Release-Namespace |
| `OPS_WORKSPACE_SERVICE_ACCOUNT`, `OPS_WORKSPACE_IMAGE_PULL_SECRET` | `workspace.serviceAccountName`, bestehendes `imagePullSecretName` |
| `OPS_WORKSPACE_CPU_REQUEST`, `OPS_WORKSPACE_MEMORY_REQUEST`, `OPS_WORKSPACE_MEMORY_LIMIT` | `workspace.resources.requests.cpu`, `.requests.memory`, `.limits.memory` |
| `OPS_WORKSPACE_STORAGE_LIMIT`, `OPS_WORKSPACE_TTL_SECONDS` | `workspace.storageLimit`, `workspace.ttlSeconds` |
| `OPS_WORKSPACE_COMMAND_TIMEOUT`, `OPS_WORKSPACE_MAX_COMMAND_TIMEOUT`, `OPS_WORKSPACE_MAX_OUTPUT_BYTES` | `workspace.commandTimeoutSeconds`, `workspace.maxCommandTimeoutSeconds`, `workspace.maxOutputBytes` |
| `OPS_WORKSPACE_AZURE_CLIENT_ID`, `OPS_WORKSPACE_AZURE_TENANT_ID`, `OPS_WORKSPACE_AZURE_SUBSCRIPTION_ID` | `workspace.azure.clientId`, `.tenantId`, `.subscriptionId` |

`workspace.ttlSeconds` ist standardmäßig 3600. Nach Ablauf des `sleep` endet der Container; das Pod-Objekt wird dadurch nicht automatisch gelöscht. Verbliebene Pod-Objekte lassen sich mit dem letzten Befehl unten manuell entfernen.

```bash
kubectl --context <staging> get pods -n <namespace>                         # Namen ops-ws-* suchen
kubectl --context <staging> describe pod ops-ws-<operation> -n <namespace>
kubectl --context <staging> logs ops-ws-<operation> -n <namespace>           # sleep liefert normalerweise keine Ausgabe
kubectl --context <staging> exec -it ops-ws-<operation> -n <namespace> -- bash
kubectl --context <staging> auth can-i list pods --all-namespaces --as=system:serviceaccount:<namespace>:operations-agent-workspace
kubectl --context <staging> auth can-i get secrets -n <namespace> --as=system:serviceaccount:<namespace>:operations-agent-workspace       # no
kubectl --context <staging> auth can-i create pods/exec -n <namespace> --as=system:serviceaccount:<namespace>:operations-agent-workspace  # no
kubectl --context <staging> delete pod ops-ws-<operation> -n <namespace>
```

Für Azure benötigt der Workspace eine Workload Identity aus der Foundation; sie ist noch nicht provisioniert. Erst mit `workspace.azure.clientId` annotiert das Chart den Workspace-ServiceAccount. Der Worker muss für diese Pods das Label `azure.workload.identity/use: "true"` setzen. Ohne injizierte Identität beendet sich `az-login` mit Code 2 und dem Hinweis auf die fehlende AKS Workload Identity. `AZURE_CONFIG_DIR=/workspace/.home/.azure`; `AZURE_CORE_COLLECT_TELEMETRY=false` und `AZURE_CORE_SURVEY_MESSAGE=false` schalten Telemetrie und Umfrage ab.

## Start, Stopp, Wiederanlauf

| Vorgang | Vorgehen | Wirkung |
| --- | --- | --- |
| Erststart | `ops-migrate.yml` (C) einmal ausführen, dann Rollout per `app-deploy.yml` | Hook-Job migriert erneut ohne Änderung; API und Worker starten erst bei passender Schema-Version |
| Stopp | Worker auf 0 skalieren (`worker.replicas: 0` im Sollzustand) | laufende Aufträge behalten Lease bis Ablauf; `/healthz` meldet nach 10 Minuten 503 (E10) |
| Wiederanlauf | Worker wieder auf 1 | neuer Worker übernimmt Aufträge nach Lease-Ablauf; der Agent gleicht offene Aktionen ab und beobachtet bekannte Workflow-Läufe weiter |
| Agenten-Update | neuer Digest im Sollzustand, Rollout per `app-deploy.yml` | Hook-Job migriert vor neuen Pods; `Recreate` beendet den alten Worker (60 s Grace); GitHub Actions führt einen laufenden Deploy-Workflow fort, der neue Worker setzt die Beobachtung über die gespeicherte Lauf-ID fort |

## Rollback

Nur auf eine schemakompatible Version: Digest und Chart-Version des vorherigen Releases per Pull Request in `platform/environments/staging/operations-agent.yaml` zurücksetzen. Migrationen laufen nur vorwärts; ältere Prozesse brechen bei neuerer Schema-Version mit erwarteter und vorhandener Version ab, statt das Schema zu ändern. Aufträge, Aktionsprotokoll und Checkpoints bleiben in PostgreSQL erhalten.
