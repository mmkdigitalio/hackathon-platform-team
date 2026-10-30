# Agentenpaket: Image, Chart und Betrieb

## Image

`operations-agent/Dockerfile`, Build-Kontext ist das Repository-Root (die Betriebsregeln liegen in `platform/operations/agent/`):

```bash
docker build -f operations-agent/Dockerfile --target test .      # Tests im Image
docker build -f operations-agent/Dockerfile --target runtime -t operations-agent:dev .
```

Ein Image für alle Prozesse, Benutzer `10001`, Python 3.12.16, Abhängigkeiten aus `uv.lock` (`--frozen`). Den Startbefehl setzt das Chart; ohne Befehl beendet sich der Container mit Hinweis.

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
| `OPS_GITHUB_TOKEN` | `opsGithubToken` | D |
| `OPS_BASE_DOMAIN` | `baseDomain` | D |
| `OPS_PUBLIC_SCHEME` | `publicScheme` | D (HTTP-Fallback nach E8; fehlt noch im Katalog, siehe uebergaben.md) |
| `OPS_ACR_LOGIN_SERVER` | `acrLoginServer` | D (Auflösung von `acr:`-Smoke-Images) |
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

Keine Azure-Anmeldung, nur Repo-Secrets des State-Backends und `OPS_GITHUB_TOKEN` (über `secrets: inherit`).

## Start, Stopp, Wiederanlauf

| Vorgang | Vorgehen | Wirkung |
| --- | --- | --- |
| Erststart | `ops-migrate.yml` (C) einmal ausführen, dann Rollout per `app-deploy.yml` | Hook-Job migriert erneut ohne Änderung; API und Worker starten erst bei passender Schema-Version |
| Stopp | Worker auf 0 skalieren (`worker.replicas: 0` im Sollzustand) | laufende Aufträge behalten Lease bis Ablauf; `/healthz` meldet nach 10 Minuten 503 (E10) |
| Wiederanlauf | Worker wieder auf 1 | neuer Worker übernimmt Aufträge nach Lease-Ablauf; der Agent gleicht offene Aktionen ab und beobachtet bekannte Workflow-Läufe weiter |
| Agenten-Update | neuer Digest im Sollzustand, Rollout per `app-deploy.yml` | Hook-Job migriert vor neuen Pods; `Recreate` beendet den alten Worker (60 s Grace); GitHub Actions führt einen laufenden Deploy-Workflow fort, der neue Worker setzt die Beobachtung über die gespeicherte Lauf-ID fort |

## Rollback

Nur auf eine schemakompatible Version: Digest und Chart-Version des vorherigen Releases per Pull Request in `platform/environments/staging/operations-agent.yaml` zurücksetzen. Migrationen laufen nur vorwärts; ältere Prozesse brechen bei neuerer Schema-Version mit erwarteter und vorhandener Version ab, statt das Schema zu ändern. Aufträge, Aktionsprotokoll und Checkpoints bleiben in PostgreSQL erhalten.
