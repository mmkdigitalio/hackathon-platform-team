#!/usr/bin/env bash
# Nicht reparierbares Fehlerszenario: dokumentierter Commit mit ungültigem Konfigurationswert im Sollzustand.
#
#   fault-invalid-config.sh trigger --environment staging   Branch, Commit und Pull Request anlegen
#   fault-invalid-config.sh revert  --environment staging   Revert-Branch und Pull Request anlegen
#
# Der Fehler wird erst mit dem Merge wirksam (app-deploy.yml rollt ihn aus). Pfad und Wert stehen in
# fault-invalid-config.yaml. Geändert wird nur values in platform/environments/staging/affine.yaml.
set -euo pipefail

usage() { sed -n '2,8p' "$0" >&2; exit 2; }

command="${1:-}"; shift || true
environment=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment) environment="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$command" == "trigger" || "$command" == "revert" ]] || usage

if [[ "$environment" != "staging" ]]; then
  echo "Abbruch ohne Änderung: Fehlerszenarien sind nur für staging erlaubt (angegeben: '${environment:-leer}')." >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
file="platform/environments/$environment/affine.yaml"
marker="fault(affine/$environment): ungültiger Konfigurationswert"
cd "$root"

[[ -z "$(git status --porcelain)" ]] || { echo "Abbruch: Arbeitsverzeichnis nicht sauber" >&2; exit 1; }
[[ -f "$file" ]] || { echo "Abbruch: $file fehlt" >&2; exit 1; }

case "$command" in
  trigger)
    branch="fault/affine-$environment-invalid-config-$(date -u +%Y%m%d%H%M%S)"
    git fetch --quiet origin main
    git switch --quiet -c "$branch" origin/main
    node - "$here/fault-invalid-config.yaml" "$file" <<'EOF'
const fs = require("fs");
const { createRequire } = require("module");
const yaml = createRequire(process.cwd() + "/platform/schemas/app-catalog/package.json")("yaml");
const fault = yaml.parse(fs.readFileSync(process.argv[2], "utf8"));
if (!Array.isArray(fault.path) || fault.path.length === 0 || fault.value === null) {
  console.error("Abbruch: path und value in fault-invalid-config.yaml sind noch nicht festgelegt");
  process.exit(1);
}
const doc = yaml.parseDocument(fs.readFileSync(process.argv[3], "utf8"));
doc.setIn(["values", ...fault.path], fault.value);
fs.writeFileSync(process.argv[3], doc.toString());
console.log(`values.${fault.path.join(".")} = ${JSON.stringify(fault.value)}`);
EOF
    # Datensicherheit: nur die Sollzustandsdatei ändert sich
    [[ "$(git diff --name-only)" == "$file" ]] || { echo "Abbruch: unerwartete Änderungen" >&2; git checkout -- .; exit 1; }
    (cd platform/schemas/app-catalog && npx tsx src/cli.ts validate)
    git commit --quiet -am "$marker" -m "Fehlerszenario 'nicht reparierbar' laut platform/operations/apps/affine/README.md. Rücksetzen per Revert."
    sha="$(git rev-parse HEAD)"
    git push --quiet -u origin "$branch"
    gh pr create --base main --head "$branch" --title "$marker" \
      --body "Fehlerszenario nicht reparierbar. Auslösender Commit: $sha. Rücksetzen: \`$0 revert --environment $environment\`."
    echo "Commit $sha; nach dem Merge rollt app-deploy.yml den Fehler aus."
    ;;
  revert)
    git fetch --quiet origin main
    sha="$(git log origin/main --format=%H --grep="^${marker}" -n 1 -- "$file")"
    [[ -n "$sha" ]] || { echo "Abbruch: kein Fehler-Commit auf main gefunden" >&2; exit 1; }
    branch="fault/affine-$environment-revert-${sha:0:12}"
    git switch --quiet -c "$branch" origin/main
    git revert --no-edit "$sha"
    git push --quiet -u origin "$branch"
    gh pr create --base main --head "$branch" --title "Revert: $marker" --body "Setzt $sha zurück."
    echo "Revert-PR angelegt; nach dem Merge rollt app-deploy.yml den vorherigen Wert aus."
    ;;
esac
