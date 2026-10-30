#!/usr/bin/env bash
# Startet den Smoke-Test als Kubernetes-Job im App-Namespace und schreibt das Verifikationsergebnis.
#
#   smoke-job.sh <stack-outputs.json> <app-facts.json> <ergebnisdatei>
#
# Umgebung: KUBECONFIG, OPERATION_ID, ACTION_ID (optional), RUN_URL, DESIRED_GIT_SHA.
# Exit 0 nur bei erfolgreichem Smoke-Test; das Ergebnis wird in jedem Fall geschrieben.
set -euo pipefail

outputs="${1:?Stack-Ausgaben fehlen}"
facts="${2:?App-Angaben fehlen}"
result="${3:?Ergebnisdatei fehlt}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
catalog="$here/../../../schemas/app-catalog"
work="$(mktemp -d)"

jq_out() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"; }
app_id="$(jq_out "$outputs" appId)"
environment="$(jq_out "$outputs" environment)"
namespace="$(jq_out "$outputs" namespace)"
smoke_image="$(jq_out "$outputs" smokeImage)"

suffix="$(printf '%s' "${ACTION_ID:-$OPERATION_ID}" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-' | tail -c 40)"
job="smoke-${suffix#-}"
job="${job%-}"

python3 - "$outputs" "$facts" "$job" > "$work/job.json" <<'EOF'
import json, os, sys
outputs, facts, job = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3]
env = [{"name": "TARGET_URL", "value": outputs["url"]}]
env += [{"name": k, "value": v} for k, v in sorted(facts["smokeEnv"].items())]
# Nur die Test-Secrets aus <app-id>-platform, nicht die übrigen App-Secrets (wie D's Verifikationswerkzeug)
env += [
    {"name": k, "valueFrom": {"secretKeyRef": {"name": outputs["secretName"], "key": k}}}
    for k in sorted(facts["smokeSecretKeys"])
]
container = {"name": "smoke", "image": outputs["smokeImage"], "env": env}
print(json.dumps({
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
        "name": job,
        "namespace": outputs["namespace"],
        "labels": {"platform.mmk/smoke-test": "true", "platform.mmk/app-id": outputs["appId"]},
        "annotations": {
            "platform.mmk/operation-id": os.environ["OPERATION_ID"],
            "ops.mmk/action-id": os.environ.get("ACTION_ID", ""),
        },
    },
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 900,
        "ttlSecondsAfterFinished": 86400,
        "template": {
            "metadata": {"labels": {"platform.mmk/smoke-test": "true"}},
            "spec": {
                "restartPolicy": "Never",
                "imagePullSecrets": [{"name": outputs["imagePullSecret"]}],
                "containers": [container],
            },
        },
    },
}))
EOF

kubectl -n "$namespace" delete job "$job" --ignore-not-found >/dev/null
kubectl apply -f "$work/job.json" >/dev/null
echo "Smoke-Job $namespace/$job gestartet ($smoke_image)"

deadline=$((SECONDS + 960))
while (( SECONDS < deadline )); do
  succeeded="$(kubectl -n "$namespace" get job "$job" -o jsonpath='{.status.succeeded}')"
  failed="$(kubectl -n "$namespace" get job "$job" -o jsonpath='{.status.failed}')"
  [[ "${succeeded:-0}" -ge 1 || "${failed:-0}" -ge 1 ]] && break
  sleep 5
done

pod="$(kubectl -n "$namespace" get pods -l "job-name=$job" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
exit_code=125
if [[ -n "$pod" ]]; then
  kubectl -n "$namespace" logs "$pod" > "$work/smoke.out" 2>/dev/null || true
  code="$(kubectl -n "$namespace" get pod "$pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}')"
  [[ -n "$code" ]] && exit_code="$code"
fi
touch "$work/smoke.out"

# Tatsächlich laufender Stand: Release-Referenz am Namespace, Image-Digest aus den Pods
annotation() { kubectl get namespace "$namespace" -o "jsonpath={.metadata.annotations.ops\.mmk/release-$1}"; }
running_sha="$(annotation git-sha)"
running_chart="$(annotation chart-version)"
desired_image="$(jq_out "$facts" image)"
desired_digest="${desired_image##*@}"
# Alle laufenden Pods des Namespace; zugeordnet wird über das Image-Repository (Charts labeln Pods unterschiedlich)
kubectl -n "$namespace" get pods -o json > "$work/pods.json"
running_digest="$(python3 - "$work/pods.json" "${desired_image%@*}" <<'EOF'
import json, sys
pods, repo = json.load(open(sys.argv[1]))["items"], sys.argv[2]
# Registry-Teil weglassen: acr:<repo> bzw. <registry>/<repo>
repo = repo[4:] if repo.startswith("acr:") else repo.split("/", 1)[-1]
digests = set()
for pod in pods:
    if pod["status"].get("phase") != "Running":
        continue
    for cs in pod["status"].get("containerStatuses", []):
        if repo and repo in cs.get("image", "") + cs.get("imageID", ""):
            digests.add(cs.get("imageID", "").rsplit("@", 1)[-1])
print(sorted(digests)[0] if len(digests) == 1 else ("gemischt:" + ",".join(sorted(digests)) if digests else "unbekannt"))
EOF
)"

set +e
(cd "$catalog" && npx tsx src/cli.ts verification-result \
  --smoke-output "$work/smoke.out" --exit-code "$exit_code" \
  --app "$app_id" --env "$environment" --check-ref "${smoke_image##*@}" \
  --running-git-sha "${running_sha:-unbekannt}" --running-image-digest "$running_digest" \
  --running-chart-version "${running_chart:-unbekannt}" \
  --desired-git-sha "$DESIRED_GIT_SHA" --desired-image-digest "$desired_digest" \
  --desired-chart-version "$(jq_out "$facts" chartVersion)" \
  --operation-id "$OPERATION_ID" --workflow-run "$RUN_URL") > "$result"
status=$?
set -e
echo "Smoke-Test Exit-Code $exit_code, Verifikation $( [[ $status -eq 0 ]] && echo erfolgreich || echo fehlgeschlagen )"
exit "$status"
