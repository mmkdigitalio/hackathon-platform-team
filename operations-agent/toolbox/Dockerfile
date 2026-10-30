# syntax=docker/dockerfile:1.7
# Build-Kontext: operations-agent/toolbox; Debian-Pakete aus einem festen Snapshot.
FROM python:3.12.16-slim-bookworm AS base
ARG DEBIAN_SNAPSHOT=20261029T000000Z
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
    && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ bookworm main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ bookworm-updates main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s/ bookworm-security main\n' \
       "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       git bash coreutils findutils grep sed gawk diffutils patch curl ca-certificates \
       jq unzip openssh-client libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

FROM base AS download
ARG TARGETARCH
ARG YQ_VERSION=4.45.1
ARG RIPGREP_VERSION=14.1.1
ARG NODE_VERSION=22.14.0
ARG KUBECTL_VERSION=1.35.0
ARG HELM_VERSION=3.18.4
ARG GH_VERSION=2.69.0
ARG AZURE_CLI_VERSION=2.80.0
COPY fetch-tools.sh /tmp/fetch-tools.sh
RUN bash /tmp/fetch-tools.sh
# Eigene venv: Azure-Abhängigkeiten bleiben vom Python der Sandbox getrennt.
RUN python3 -m venv /opt/azure-cli \
    && /opt/azure-cli/bin/pip install --no-cache-dir --no-compile "azure-cli==$AZURE_CLI_VERSION" \
    && find /opt/azure-cli -type d -name __pycache__ -prune -exec rm -rf '{}' +
# Liste aus den Build-Pins und installierten Versionen; keine zweite Versionsquelle.
RUN mkdir -p /out/usr/local/share/ops-toolbox \
    && { \
      printf '# Operations-Toolbox\n\n'; \
      python3 --version; uv --version; \
      printf 'yq %s\nripgrep %s\nNode.js %s\nkubectl %s\nHelm %s\ngh %s\nAzure CLI %s, eigene venv /opt/azure-cli\n' \
        "$YQ_VERSION" "$RIPGREP_VERSION" "$NODE_VERSION" "$KUBECTL_VERSION" \
        "$HELM_VERSION" "$GH_VERSION" "$AZURE_CLI_VERSION"; \
      printf 'npm '; /out/usr/local/bin/node /out/usr/local/lib/node_modules/npm/bin/npm-cli.js --version; \
      printf '\nDebian-Snapshot %s, signierte APT-Metadaten:\n' "$DEBIAN_SNAPSHOT"; \
      dpkg-query -W -f='${Package} ${Version}\n' git bash coreutils findutils grep sed gawk \
        diffutils patch curl ca-certificates jq unzip openssh-client libstdc++6; \
      printf '\nVersionen zur Laufzeit: toolbox-info\n'; \
    } > /out/usr/local/share/ops-toolbox/README.md

FROM base AS runtime
LABEL org.opencontainers.image.source="https://github.com/mmkdigitalio/hackathon-platform-team" \
      org.opencontainers.image.title="operations-agent-toolbox" \
      org.opencontainers.image.version="0.1.0"
COPY --from=download /out/ /
COPY --from=download /opt/azure-cli /opt/azure-cli
COPY --chmod=0755 gh git-credential-ops az-login toolbox-info /usr/local/bin/
COPY gitconfig /etc/gitconfig
COPY --chmod=0755 pre-push /etc/ops-git-hooks/pre-push
RUN ln -s /opt/azure-cli/bin/az /usr/local/bin/az \
    && groupadd --gid 10001 ops \
    && useradd --uid 10001 --gid 10001 --home-dir /workspace/.home --no-create-home --shell /bin/bash ops \
    && install -d -o 10001 -g 10001 /workspace /workspace/.home
# Dokumentierte Azure-CLI-Konfiguration per Umgebungsvariablen: keine Telemetrie/Umfrage.
ENV HOME=/workspace/.home \
    AZURE_CONFIG_DIR=/workspace/.home/.azure \
    AZURE_CORE_COLLECT_TELEMETRY=false \
    AZURE_CORE_SURVEY_MESSAGE=false \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
USER 10001:10001
CMD ["sleep", "3600"]
