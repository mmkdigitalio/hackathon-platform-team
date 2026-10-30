#!/usr/bin/env bash
# Führt ein Pulumi-Kommando für den Stack organization/app-<app-id>/<env> aus.
#
#   scripts/pulumi-stack.sh <app-id> <env> preview|up|refresh|stack ... [weitere pulumi-Argumente]
#
# Backend: PULUMI_BACKEND_URL (lokal z. B. file://~/.pulumi-local) oder
#          PULUMI_BACKEND_ACCOUNT + PULUMI_BACKEND_KEY (+ PULUMI_STATE_CONTAINER) für das Azure-Blob-Backend.
# Passphrase: PULUMI_CONFIG_PASSPHRASE.
# Optional: APP_RUNTIME_GIT_SHA, APP_RUNTIME_OPERATION_ID und weitere APP_RUNTIME_CONFIG_<key>=<wert>.
# APP_RUNTIME_GITHUB_APP_PRIVATE_KEY: geschützter CI-Rückfall, nur beim operations-agent.
set -euo pipefail

app_id="${1:?app-id fehlt}"
environment="${2:?Umgebung fehlt}"
shift 2

[[ "$app_id" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "ungültige App-ID: $app_id" >&2; exit 2; }
[[ "$environment" =~ ^[a-z]+$ ]] || { echo "ungültige Umgebung: $environment" >&2; exit 2; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${PULUMI_BACKEND_URL:-}" ]]; then
  : "${PULUMI_BACKEND_ACCOUNT:?PULUMI_BACKEND_ACCOUNT oder PULUMI_BACKEND_URL fehlt}"
  : "${PULUMI_BACKEND_KEY:?PULUMI_BACKEND_KEY fehlt}"
  export AZURE_STORAGE_ACCOUNT="$PULUMI_BACKEND_ACCOUNT"
  export AZURE_STORAGE_KEY="$PULUMI_BACKEND_KEY"
  export PULUMI_BACKEND_URL="azblob://${PULUMI_STATE_CONTAINER:-pulumi-state}"
fi
: "${PULUMI_CONFIG_PASSPHRASE:?PULUMI_CONFIG_PASSPHRASE fehlt}"
export PULUMI_SKIP_UPDATE_CHECK=true

if [[ ! -f "$here/bin/infra/app-runtime/src/index.js" || "${APP_RUNTIME_SKIP_BUILD:-}" != "1" ]]; then
  (cd "$here" && npm run --silent build)
fi

# Ein Programm, ein Projekt je App: Pulumi.yaml wird erzeugt und verweist auf das gemeinsame Programm.
# Die Stack-Konfiguration ist flüchtig; das Passphrase-Salz liest Pulumi aus dem State.
work="$here/.work/app-$app_id"
mkdir -p "$work"
cat > "$work/Pulumi.yaml" <<EOF
name: app-$app_id
description: App-Stack für $app_id (erzeugt von scripts/pulumi-stack.sh)
runtime:
  name: nodejs
  options:
    typescript: false
main: ../../
EOF

stack="organization/app-$app_id/$environment"
pulumi login --non-interactive >/dev/null
pulumi stack select "$stack" --create --secrets-provider passphrase --non-interactive --cwd "$work" >/dev/null

set_config() { pulumi config set --cwd "$work" --stack "$stack" "app-runtime:$1" "$2" >/dev/null; }
[[ -n "${APP_RUNTIME_GIT_SHA:-}" ]] && set_config gitSha "$APP_RUNTIME_GIT_SHA"
[[ -n "${APP_RUNTIME_OPERATION_ID:-}" ]] && set_config operationId "$APP_RUNTIME_OPERATION_ID"
# Nur der Agent erhält den CI-Rückfall. PEM bleibt aus Argumenten und Logs heraus;
# Pulumi verschlüsselt ihn mit der vorhandenen Stack-Passphrase.
if [[ "$app_id" == "operations-agent" && -n "${APP_RUNTIME_GITHUB_APP_PRIVATE_KEY:-}" ]]; then
  printf '%s' "$APP_RUNTIME_GITHUB_APP_PRIVATE_KEY" | pulumi config set --secret \
    --cwd "$work" --stack "$stack" app-runtime:githubAppPrivateKey >/dev/null
fi
while IFS='=' read -r name value; do
  [[ "$name" == APP_RUNTIME_CONFIG_* ]] && set_config "${name#APP_RUNTIME_CONFIG_}" "$value"
done < <(env)

exec pulumi "$@" --cwd "$work" --stack "$stack" --non-interactive
