#!/usr/bin/env bash
set -euo pipefail
kind delete cluster --name ops-agent
docker rm -f ops-kind-registry >/dev/null 2>&1 || true
rm -rf "$(dirname "$0")/../../../.kind"
