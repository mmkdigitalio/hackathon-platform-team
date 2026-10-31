# App-Programm

Ein Pulumi-Programm (TypeScript) für alle App-Stacks `organization/app-<app-id>/<env>`. Es enthält keinen app-spezifischen Code; alles kommt aus Katalog und Sollzustand. Es verwaltet nur Kubernetes-, PostgreSQL- und Random-Ressourcen und liest Foundation-Werte per `StackReference` auf `organization/foundation/<env>`.

| Ressource | Name |
| --- | --- |
| Namespace (geschützt), Annotationen `ops.mmk/release-*` mit der Release-Referenz des letzten erfolgreichen Rollouts | `<app-id>-<env>` |
| Image-Pull-Secret aus `acrUsername`/`acrPassword` | `acr-pull` |
| Aufgelöste Secret-Referenzen | `<app-id>-platform` (App- und Test-Secrets) |
| Datenbausteine | siehe [Katalog](../../schemas/app-catalog/README.md#datenbausteine-v01) |
| Helm-Release (mit Hooks, nicht atomar) | `<app-id>` |
| Ingress mit `cert-manager.io/cluster-issuer` | `<app-id>.<baseDomain>` |

Jeder Lauf übergibt die `operationId` als Chart-Wert `rolloutMarker`. Dadurch führt Helm bei jedem Lauf ein Upgrade mit Drei-Wege-Merge aus und setzt manuelle Abweichungen wie eine geänderte Replica-Zahl auf den Sollzustand zurück.

## Datenbausteine

| Baustein | Ressourcen | `app:`-Secrets |
| --- | --- | --- |
| `postgres-database` | Datenbank und Login auf Foundation-PostgreSQL, geschützt | `postgres-host`, `postgres-database`, `postgres-user`, `postgres-password`, `postgres-url` |
| `mariadb` | Deployment `<app-id>-mariadb`, Strategie `Recreate`, Service auf 3306, geschütztes PVC `<app-id>-mariadb-data` | `mariadb-host`, `mariadb-port`, `mariadb-database`, `mariadb-user`, `mariadb-password`, `mariadb-url` |
| `redis` | Deployment und Service `<app-id>-redis`, ohne Persistenz | `redis-host`, `redis-password` |
| `persistent-volume` | Geschütztes PVC `<app-id>-data` für das App-Chart | keine |

Pro App ist nur eine SQL-Datenbank erlaubt. MariaDB nutzt die konfigurierte StorageClass
und `sizes.databaseSize`, standardmäßig `5Gi`. Das App-Volume bleibt davon unabhängig.
In-Cluster-Volume ohne Backup = Staging-Risiko; für MariaDB gibt es keine automatische Sicherung.

`src/blocks.ts` führt `MARIADB_IMAGE`, `MARIADB_IMAGE_DIGEST` und `MARIADB_VERSION`.
MariaDB `11.4.13` ist per Multi-Arch-Index gepinnt, geprüft am 31.10.2026 anhand der
[offiziellen Docker-Library-Metadaten](https://github.com/docker-library/repo-info/blob/master/repos/mariadb/remote/11.4.md).
`docker buildx imagetools inspect mariadb:11.4` scheiterte lokal an DNS; ein Clusterlauf steht aus.
Die URL lautet `mysql://<user>:<password>@<host>:3306/<db>?charset=utf8mb4&serverVersion=11.4.13-MariaDB`.
DB-Name und Benutzer entsprechen der App-ID mit `_` statt `-`. Beide Passwörter sind
alphanumerisch; das Root-Passwort bleibt im internen Secret `<app-id>-mariadb`.

Der TypeScript-Leser akzeptiert `sizes.databaseSize` additiv und Ressourcen als positive
Kubernetes-Quantities. Im unveränderten Vertrag `v0.1/desired-state.schema.json` fehlen
noch `sizes.databaseSize` und die erweiterten Quantity-Muster für CPU und Speicher.

## Projekt je App

Pulumi leitet den Projektnamen aus `Pulumi.yaml` ab. [`scripts/pulumi-stack.sh`](scripts/pulumi-stack.sh) erzeugt deshalb je App `.work/app-<app-id>/Pulumi.yaml` mit `main: ../../` und wählt den Stack `organization/app-<app-id>/<env>`. Das Programm liest App-ID und Umgebung aus Projekt- und Stack-Namen.

```bash
npm ci && (cd ../../schemas/app-catalog && npm ci)
export PULUMI_CONFIG_PASSPHRASE=…
export PULUMI_BACKEND_ACCOUNT=… PULUMI_BACKEND_KEY=…        # oder PULUMI_BACKEND_URL=file://…
scripts/pulumi-stack.sh affine staging preview --diff
```

Konfiguration (`APP_RUNTIME_CONFIG_<key>=<wert>` an das Skript oder `pulumi config set app-runtime:<key>`):

| Schlüssel | Standard | Zweck |
| --- | --- | --- |
| `gitSha`, `operationId` | `local`, `local-<zeit>` | Release-Referenz, Rollout-Markierung (`APP_RUNTIME_GIT_SHA`, `APP_RUNTIME_OPERATION_ID`) |
| `foundationStack` | `organization/foundation/<env>` | Stack-Referenz |
| `expectedContractVersion` | `1` | erwartete `contractVersion` der Foundation-Ausgaben |
| `storageClass` | `managed-csi` | StorageClass für `persistent-volume` und `mariadb` |
| `ingressClassName`, `clusterIssuer`, `publicScheme` | Foundation-Ausgaben `ingressClassName`, `clusterIssuerName`, `publicScheme` | nur lokal übersteuern; `publicScheme: http` ist der HTTP-Fallback (E8) ohne Zertifikat |
| `postgresSslMode` | `require` | lokal `disable` |
| `postgresAdminHost`, `postgresAdminPort` | – | nur lokal: Adresse für den PostgreSQL-Provider, wenn Pods den Server unter `postgresHost` erreichen, Pulumi aber nicht |
| `treeDir` | – | nur lokal: Baum mit `catalog/` und `environments/` statt `platform/`, z. B. `platform/infra/app-runtime/dev/test-tree` |

## Tests

```bash
npm test        # Pulumi-Mocks mit Platzhalter-Ausgaben: Ressourcentypen, Namen, Secrets, Werte, Vertragsversion
npm run build   # tsc nach bin/, von Pulumi ausgeführt
```

## Lokal gegen Cluster und PostgreSQL

Ohne Azure: kind-Cluster mit lokaler Registry (dient als „ACR“) und ingress-nginx, PostgreSQL im Container, Platzhalter-Foundation unter [`dev/foundation-placeholder`](dev/foundation-placeholder/Pulumi.yaml) und die Test-App `runtime-test` aus [`dev/test-tree`](dev/test-tree) mit [`dev/test-chart`](dev/test-chart) (alle drei Datenbausteine, Pre-Install-Hook, `Recreate`). Voraussetzungen: Docker, kind, Helm, Pulumi-CLI.

```bash
# Registry, Cluster (Registry-Spiegel und Ports 8080/8443 laut kind-Doku "Local Registry" und "Ingress"), ingress-nginx, PostgreSQL
docker run -d --restart=always -p 127.0.0.1:5001:5000 --name kind-registry registry:2
kind create cluster --name app-runtime --config kind.yaml        # containerdConfigPatches config_path, ingress-ready, extraPortMappings
#   je Knoten: /etc/containerd/certs.d/localhost:5001/hosts.toml -> [host."http://kind-registry:5000"]
docker network connect kind kind-registry
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
docker run -d --name app-runtime-pg -p 127.0.0.1:5432:5432 -e POSTGRES_USER=platformadmin -e POSTGRES_PASSWORD=local postgres:16

# Chart und smoke-http in die lokale Registry; Digest von smoke-http in dev/test-tree/catalog/runtime-test/app.yaml eintragen
helm package dev/test-chart -d "$TMPDIR" && helm push "$TMPDIR/runtime-test-0.1.0.tgz" oci://localhost:5001/charts --plain-http
docker build -t localhost:5001/platform/smoke-http:local smoke-http && docker push localhost:5001/platform/smoke-http:local

# Umgebung
export PULUMI_BACKEND_URL=file://$HOME/.pulumi-local PULUMI_CONFIG_PASSPHRASE=local
export APP_RUNTIME_CONFIG_storageClass=standard APP_RUNTIME_CONFIG_publicScheme=http APP_RUNTIME_CONFIG_postgresSslMode=disable
export APP_RUNTIME_CONFIG_treeDir=platform/infra/app-runtime/dev/test-tree
export KUBECONFIG=$(mktemp) && kind get kubeconfig --name app-runtime > "$KUBECONFIG"

# Platzhalter-Foundation; baseDomain aus der Knoten-IP, damit Pods den Host über sslip.io erreichen
node_ip=$(docker inspect -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' app-runtime-control-plane)
(cd dev/foundation-placeholder && pulumi stack init organization/foundation/staging --secrets-provider passphrase \
  && pulumi config set --secret kubeconfig "$(cat "$KUBECONFIG")" && pulumi config set --secret postgresAdminPassword local \
  && pulumi config set baseDomain "${node_ip//./-}.sslip.io" && pulumi up --yes)

# Test-App
APP_RUNTIME_OPERATION_ID=local-1 scripts/pulumi-stack.sh runtime-test staging up --yes
curl -H "Host: runtime-test.${node_ip//./-}.sslip.io" http://127.0.0.1:8080/
```

Geprüft wurde damit (30.10.2026): Preview nur mit Kubernetes-, PostgreSQL- und Random-Ressourcen; Pre-Install- und Pre-Upgrade-Hook mit vorhandenem Secret; stabile Secrets, unveränderte Pods, PVC und Datenbank bei erneutem Lauf; Replica-Zahl nach `kubectl scale --replicas=0` durch einen Lauf ohne Sollzustandsänderung zurückgesetzt; scheiternder Plan nach Entfernen von `persistent-volume`; Lauf ohne Stack-Konfigurationsdatei (Salz aus dem State); `scripts/smoke-job.sh` mit Erfolg und mit Fehlschlag bei 0 Replicas.

Ebenso mit D's echtem Agenten-Chart (Image und Chart aus `operations-agent/` in die lokale Registry, Katalogeintrag `platform/catalog/operations-agent/app.yaml` in einem Test-Baum): Ohne C's Einstiegspunkte scheitert der Migrations-Hook, es entstehen keine API- und Worker-Pods und der Lauf endet als fehlgeschlagen ohne Release-Annotationen. Mit Platzhaltern für `migrate.command`, `api.command` und `worker.command` in `values` laufen API und Worker als getrennte Deployments, der Host entspricht `operationsApiHost`, das Secret enthält die Schlüssel aus `platform/operations/agent/paket-betrieb.md` und `smoke-http` prüft `/healthz` erfolgreich. Ein Preview des anderen App-Stacks zeigt danach keine Änderungen.

Aufräumen: `kind delete cluster --name app-runtime && docker rm -f kind-registry app-runtime-pg`.
