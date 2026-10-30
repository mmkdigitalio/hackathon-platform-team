#!/usr/bin/env bash
# Richtet einen GitHub-Runner für den App-Pfad ein: Node-Abhängigkeiten und Pulumi-CLI.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
(cd "$root/platform/schemas/app-catalog" && npm ci --no-audit --no-fund)

if [[ "${1:-}" == "--catalog-only" ]]; then exit 0; fi

(cd "$root/platform/infra/app-runtime" && npm ci --no-audit --no-fund && npm run --silent build)

version="${PULUMI_VERSION:?PULUMI_VERSION fehlt}"
if ! command -v pulumi >/dev/null || [[ "$(pulumi version)" != "v$version" ]]; then
  curl -fsSL https://get.pulumi.com | sh -s -- --version "$version" >/dev/null
  echo "$HOME/.pulumi/bin" >> "${GITHUB_PATH:-/dev/null}"
  export PATH="$HOME/.pulumi/bin:$PATH"
fi
pulumi version
