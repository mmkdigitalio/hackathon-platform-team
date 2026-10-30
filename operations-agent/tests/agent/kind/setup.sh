#!/usr/bin/env bash
# Lokaler kind-Cluster für Strang D: Registry, Agenten-Chart, AFFiNE-Ersatz, Smoke-Image.
# Alle Kubeconfigs liegen unter $KIND_DIR; ~/.kube/config wird nie verwendet oder verändert.
set -euo pipefail
cd "$(dirname "$0")/../../.."                       # operations-agent/
KIND_DIR="${KIND_DIR:-$PWD/.kind}"; mkdir -p "$KIND_DIR"
CLUSTER=ops-agent; REG=ops-kind-registry; PORT=5001
export KUBECONFIG="$KIND_DIR/admin.kubeconfig"

if ! docker inspect "$REG" >/dev/null 2>&1; then
  docker run -d --restart=always -p "127.0.0.1:$PORT:5000" --network bridge --name "$REG" registry:2 >/dev/null
fi
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --config - <<KIND
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry]
    config_path = "/etc/containerd/certs.d"
KIND
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker exec "$node" mkdir -p "/etc/containerd/certs.d/localhost:$PORT"
    printf '[host."http://%s:5000"]\n' "$REG" | docker exec -i "$node" cp /dev/stdin "/etc/containerd/certs.d/localhost:$PORT/hosts.toml"
  done
  docker network connect kind "$REG" 2>/dev/null || true
fi

push() {  # push <lokales-image> <name> -> druckt localhost:PORT/name@sha256:…
  docker tag "$1" "localhost:$PORT/$2:local" && docker push -q "localhost:$PORT/$2:local" >/dev/null
  echo "localhost:$PORT/$2@$(docker inspect --format '{{index .RepoDigests 0}}' "localhost:$PORT/$2:local" | cut -d@ -f2)"
}
docker build -q -f Dockerfile --target runtime -t operations-agent:local .. >/dev/null
docker build -q -t ops-smoke:local tests/agent/kind/smoke >/dev/null
AGENT_IMAGE=$(push operations-agent:local operations-agent)
SMOKE_IMAGE=$(push ops-smoke:local ops-smoke)
echo "$SMOKE_IMAGE" > "$KIND_DIR/smoke-image"

# AFFiNE-Ersatz: nginx als Deployment "affine" mit Release-Annotationen am Namespace (wie B's App-Stack)
kubectl create namespace affine-staging --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl annotate namespace affine-staging --overwrite ops.mmk/release-git-sha=local ops.mmk/release-chart-version=0.0.0-local >/dev/null
kubectl apply -n affine-staging -f - >/dev/null <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: { name: affine, labels: { app.kubernetes.io/instance: affine } }
spec:
  replicas: 1
  selector: { matchLabels: { app: affine } }
  template:
    metadata: { labels: { app: affine } }
    spec:
      containers:
        - name: affine
          image: nginx:1.27-alpine
          ports: [{ containerPort: 80 }]
---
apiVersion: v1
kind: Service
metadata: { name: affine }
spec:
  selector: { app: affine }
  ports: [{ port: 80, targetPort: 80 }]
YAML
kubectl -n affine-staging rollout status deploy/affine --timeout=180s >/dev/null

# Agenten-Chart wie vom App-Stack: Secret und Pull-Secret vorab, Platzhalter-Einstiegspunkte bis C liefert
NS=operations-agent-staging
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret generic operations-agent-platform --dry-run=client -o yaml \
  --from-literal=OPS_GITHUB_TOKEN=placeholder --from-literal=OPS_BASE_DOMAIN=affine-staging.svc.cluster.local \
  --from-literal=OPS_MODEL_API_KEYS='{}' --from-literal=POSTGRES_PASSWORD=placeholder | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret docker-registry local-pull --docker-server="localhost:$PORT" --docker-username=x --docker-password=x \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
helm upgrade --install operations-agent deploy -n "$NS" --wait --timeout 180s \
  --set image.repository="${AGENT_IMAGE%@*}" --set image.digest="${AGENT_IMAGE#*@}" \
  --set secretName=operations-agent-platform --set imagePullSecretName=local-pull \
  --set-json 'api.command=["python","-m","http.server","8080"]' \
  --set-json 'worker.command=["python","-c","import time; time.sleep(10**9)"]' \
  --set-json 'migrate.command=["python","-c","print(\"ops migrate: Platzhalter bis Strang C liefert\")"]' >/dev/null

# Kubeconfig mit dem Token des ServiceAccounts operations-agent (für die Werkzeuge)
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
TOKEN=$(kubectl -n "$NS" create token operations-agent --duration=2h)
cat > "$KIND_DIR/sa.kubeconfig" <<KCFG
apiVersion: v1
kind: Config
clusters: [{ name: kind, cluster: { server: "$SERVER", certificate-authority-data: "$CA" } }]
users: [{ name: operations-agent, user: { token: "$TOKEN" } }]
contexts: [{ name: kind-ops-agent-sa, context: { cluster: kind, user: operations-agent } }]
current-context: kind-ops-agent-sa
KCFG
chmod 600 "$KIND_DIR"/*.kubeconfig
echo "bereit: Agent $AGENT_IMAGE, Smoke $SMOKE_IMAGE, Kubeconfigs in $KIND_DIR"
