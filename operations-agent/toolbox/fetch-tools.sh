#!/bin/bash
set -euo pipefail

case "$TARGETARCH" in
  amd64) node_arch=x64; rg_target=x86_64-unknown-linux-musl ;;
  arm64) node_arch=arm64; rg_target=aarch64-unknown-linux-gnu ;;
  *) echo "Nicht unterstützte Architektur: $TARGETARCH" >&2; exit 1 ;;
esac

mkdir -p /tmp/ops-download /out/usr/local/bin /out/opt/gh/bin
cd /tmp/ops-download
fetch() { curl --fail --silent --show-error --location --retry 3 --proto '=https' "$1" -o "$2"; }
verify_manifest() {
  # Nur die angeforderte Datei prüfen; fehlende oder doppelte Einträge brechen ab.
  awk -v name="$1" '$2 == name || $2 == "*" name { print; found++ } END { if (found != 1) exit 1 }' "$2" > selected.sha256
  sha256sum --check selected.sha256
}

yq_file="yq_linux_${TARGETARCH}"
yq_url="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}"
fetch "$yq_url/$yq_file" "$yq_file"
fetch "$yq_url/checksums" checksums
fetch "$yq_url/checksums_hashes_order" checksums_hashes_order
# yq veröffentlicht mehrere Hashes pro Zeile; SHA-256 anhand der Reihenfolge auswählen.
python3 - "$yq_file" <<'PY'
from pathlib import Path
import re
import sys

name = sys.argv[1]
column = Path("checksums_hashes_order").read_text().splitlines().index("SHA-256") + 1
rows = [parts for line in Path("checksums").read_text().splitlines() if (parts := line.split()) and parts[0] == name]
if len(rows) != 1 or not re.fullmatch(r"[a-fA-F0-9]{64}", rows[0][column]):
    sys.exit("Keine eindeutige SHA-256-Prüfsumme für yq")
Path("yq.sha256").write_text(f"{rows[0][column]}  {name}\n")
PY
sha256sum --check yq.sha256
install -m 0755 "$yq_file" /out/usr/local/bin/yq

rg_file="ripgrep-${RIPGREP_VERSION}-${rg_target}.tar.gz"
rg_url="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}"
fetch "$rg_url/$rg_file" "$rg_file"
fetch "$rg_url/$rg_file.sha256" rg.sha256
verify_manifest "$rg_file" rg.sha256
tar -xzf "$rg_file"
install -m 0755 "${rg_file%.tar.gz}/rg" /out/usr/local/bin/rg

node_file="node-v${NODE_VERSION}-linux-${node_arch}.tar.gz"
node_url="https://nodejs.org/dist/v${NODE_VERSION}"
fetch "$node_url/$node_file" "$node_file"
fetch "$node_url/SHASUMS256.txt" node.sha256
verify_manifest "$node_file" node.sha256
tar -xzf "$node_file"
install -m 0755 "${node_file%.tar.gz}/bin/node" /out/usr/local/bin/node
mkdir -p /out/usr/local/lib/node_modules
cp -a "${node_file%.tar.gz}/lib/node_modules/npm" /out/usr/local/lib/node_modules/
ln -s ../lib/node_modules/npm/bin/npm-cli.js /out/usr/local/bin/npm
ln -s ../lib/node_modules/npm/bin/npx-cli.js /out/usr/local/bin/npx

kubectl_url="https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl"
fetch "$kubectl_url" kubectl
fetch "$kubectl_url.sha256" kubectl.sha256
printf '%s  kubectl\n' "$(cat kubectl.sha256)" | sha256sum --check -
install -m 0755 kubectl /out/usr/local/bin/kubectl

helm_file="helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz"
fetch "https://get.helm.sh/$helm_file" "$helm_file"
fetch "https://get.helm.sh/$helm_file.sha256sum" helm.sha256
verify_manifest "$helm_file" helm.sha256
tar -xzf "$helm_file"
install -m 0755 "linux-${TARGETARCH}/helm" /out/usr/local/bin/helm

gh_file="gh_${GH_VERSION}_linux_${TARGETARCH}.deb"
gh_url="https://github.com/cli/cli/releases/download/v${GH_VERSION}"
fetch "$gh_url/$gh_file" "$gh_file"
fetch "$gh_url/gh_${GH_VERSION}_checksums.txt" gh.sha256
verify_manifest "$gh_file" gh.sha256
dpkg-deb --extract "$gh_file" gh-unpacked
install -m 0755 gh-unpacked/usr/bin/gh /out/opt/gh/bin/gh
