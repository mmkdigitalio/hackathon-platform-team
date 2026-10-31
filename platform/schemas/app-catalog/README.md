# App-Katalog v0.1

Vertrag zwischen App-Lieferungen und dem App-Pfad. Strang B besitzt Schema, Validierung und Smoke-Test-Vertrag. A erzeugt aus `app.yaml` Verfügbarkeitstests, C validiert App/Umgebung, D lädt Release, erlaubte Aktionen und Runbook und führt Smoke-Tests aus.

| Datei | Inhalt |
| --- | --- |
| [`v0.1/app.schema.json`](v0.1/app.schema.json) | Katalogeintrag `platform/catalog/<app-id>/app.yaml` |
| [`v0.1/desired-state.schema.json`](v0.1/desired-state.schema.json) | Sollzustand `platform/environments/<env>/<app-id>.yaml` |
| [`v0.1/data-blocks.json`](v0.1/data-blocks.json) | Datenbausteine und ihre `app:`-Secrets; gelesen von Validierung und App-Programm |
| [`v0.1/smoke-test-output.schema.json`](v0.1/smoke-test-output.schema.json) | Ausgabe eines Smoke-Test-Images |
| [`v0.1/verification-result.schema.json`](v0.1/verification-result.schema.json) | Verifikationsergebnis (Deploy-Workflow und D's Verifikationswerkzeug) |
| [`v0.1/deployment-result.schema.json`](v0.1/deployment-result.schema.json) | Ergebnis eines `app-deploy.yml`-Laufs |
| [`examples/`](examples/) | Gültige Bäume (AFFiNE-artig, `operations-agent`, `whoami`, `kimai`), ungültige Fälle mit erwarteter Meldung, Beispielergebnisse |

## Zwei Dateien je App

`app.yaml` beschreibt, was eine App ist und braucht, umgebungsunabhängig. Der Sollzustand beschreibt, welche Version mit welchen Werten in einer Umgebung läuft. App-ID und Umgebung ergeben sich aus dem Pfad. Namespace `<app-id>-<env>`, Host `<app-id>.<baseDomain>` und Stack `organization/app-<app-id>/<env>` werden abgeleitet und sind keine Felder.

| Feld in `app.yaml` | Bedeutung |
| --- | --- |
| `schemaVersion` | `"0.1"` |
| `appId` | gleich dem Verzeichnisnamen, Konvention aus `platform/schemas/conventions/` |
| `source.repository`, `source.upstream` | Herkunft (`owner/name`) |
| `chart` | genau eine Quelle: `acr.repository` (OCI in der Foundation-ACR), `oci.url` (`oci://…`), `repository.{url,name}` (Helm-Repository) oder `platform.name` (lokales Chart) |
| `ingress.service`, `ingress.port` | Ziel der Ingress-Regel |
| `dataBlocks` | Teilmenge von `postgres-database`, `mariadb`, `redis`, `persistent-volume`; nur eine SQL-Datenbank pro App |
| `generatedSecrets` | Namen zufälliger, stabiler Werte, referenzierbar als `app:<name>` |
| `secrets` | `ENV_NAME: foundation:<ausgabe>` oder `app:<name>`; landet im Secret `<app-id>-platform` |
| `availability.path` | Pfad, der ohne Anmeldung 200 liefert; A erzeugt daraus `alert-<app-id>-<env>-availability` |
| `smokeTest.image` | Image mit Digest; `env` (nicht geheim) und `secrets` (Referenzen; landen ebenfalls in `<app-id>-platform`, Schlüssel dürfen nicht mit anderer Referenz in `secrets` vorkommen) |
| `runbook` | `repository`, `path`, `commit` (40 Hex-Zeichen) |
| `allowedActions` | Teilmenge von `restart`, `redeploy` |
| `dataProcedures` | `migration`, `backup`, `restore`; nicht benötigt als `not-applicable` |

| Feld im Sollzustand | Bedeutung |
| --- | --- |
| `image` | `<registry>/<repo>@sha256:<64 hex>` oder `acr:<repo>@sha256:…`; Tags allein werden abgelehnt |
| `chart.version` | feste SemVer-Version |
| `sizes.replicas`, `sizes.resources`, `sizes.volumeSize` | Größen; `volumeSize` genau dann, wenn `persistent-volume` deklariert ist |
| `upstreamRelease` | informativ, z. B. AFFiNE-Release |
| `values` | Chart-Werte; `values.platform` ist reserviert |

Git-SHA des Commits, Image-Digest und Chart-Version bilden die Release-Referenz.

### Datenbausteine v0.1

| Baustein | Umsetzung im App-Stack | `app:`-Secrets |
| --- | --- | --- |
| `postgres-database` | Datenbank und Login `<app_id>` auf dem Foundation-Server, TLS, geschützt | `postgres-host`, `postgres-database`, `postgres-user`, `postgres-password`, `postgres-url` |
| `mariadb` | Deployment `<app-id>-mariadb` mit `Recreate`, Service 3306 und geschütztem PVC `<app-id>-mariadb-data`, Standard 5Gi | `mariadb-host`, `mariadb-port`, `mariadb-database`, `mariadb-user`, `mariadb-password`, `mariadb-url` |
| `redis` | Deployment `<app-id>-redis` mit fixiertem Image, ohne Persistenz | `redis-host`, `redis-password` |
| `persistent-volume` | PVC `<app-id>-data`, StorageClass `managed-csi`, ReadWriteOnce, geschützt | keine |

Additive Bausteine sind innerhalb v0.1 erlaubt; Umsetzung, Registrierung und Tests gehören zusammen.
MariaDB liegt auf einem In-Cluster-Volume ohne Backup, ein Staging-Risiko.
`sizes.databaseSize` ist optional, Standard `5Gi`, und unabhängig von `sizes.volumeSize`.
Der TypeScript-Leser akzeptiert es bereits; die Ergänzung in `v0.1/desired-state.schema.json`
steht noch aus. Ebenso fehlen dort die erweiterten Kubernetes-Quantity-Muster für Ressourcen.

### Secret-Referenzen

`foundation:<name>` muss eine Ausgabe des Foundation-Ausgabeschemas sein; `foundation:kubeconfig` ist für Apps gesperrt. `app:<name>` muss von einem deklarierten Baustein geliefert oder in `generatedSecrets` erzeugt werden. Klartextwerte werden abgelehnt und in Fehlermeldungen nicht wiederholt.

## Werte-Vertrag für Charts

Das App-Programm setzt zusätzlich zu `values` aus dem Sollzustand diese Werte (Konvention aus D's Agenten-Chart):

```yaml
image:
  repository: <registry>/<repo>        # weitere image-Werte aus dem Sollzustand bleiben erhalten
  digest: sha256:…
secretName: affine-platform            # alle aufgelösten secrets und smokeTest.secrets
imagePullSecretName: acr-pull
rolloutMarker: <operationId>           # je Lauf neu
platform:                              # zusätzliche Angaben, optional nutzbar
  appId: affine
  environment: staging
  namespace: affine-staging
  host: affine.<baseDomain>
  url: https://affine.<baseDomain>
  persistentVolumeClaim: affine-data   # leer ohne persistent-volume
  replicas: 1                          # sizes.replicas
  resources: { requests: …, limits: … }
  release: { gitSha, imageDigest, chartVersion }
```

Im Sollzustand sind `values.image.repository`, `values.image.digest`, `values.secretName`, `values.imagePullSecretName`, `values.rolloutMarker` und `values.platform` reserviert.

Pflichten des Charts:

- Image als `{{ .Values.image.repository }}@{{ .Values.image.digest }}`, Pull-Secret `imagePullSecretName` für alle Pods einschließlich Hooks, Konfiguration und Secrets per `envFrom` aus `secretName`.
- Das Chart legt keine Secrets, Ingress-Regeln, PVCs oder Datenbanken an; das macht der App-Stack.
- Deployments tragen `app.kubernetes.io/instance: <app-id>`; daran erkennt die Rollout-Prüfung die App. Den laufenden Image-Digest ermittelt die Verifikation über das Image-Repository der Pods.
- `rolloutMarker` und `platform.release.gitSha` gehören nicht in Pod-Templates. Sie ändern sich bei jedem Lauf und erzwingen nur das Helm-Upgrade, das manuelle Abweichungen (etwa die Replica-Zahl) zurücksetzt.
- Hooks (`pre-install,pre-upgrade`) sind erlaubt; das App-Programm rollt als echtes Helm-Release aus.
- Ein `values.schema.json` darf zusätzliche Schlüssel wie `platform` nicht verbieten.

## Lokale Plattform-Charts

```yaml
chart:
  platform:
    name: web-app
```

`platform.name` ist ein Chart-Verzeichnisname aus Kleinbuchstaben, Ziffern und
Bindestrichen, ohne Pfadbestandteile. Die Validierung verlangt das Verzeichnis
`platform/charts/<name>` mit lesbarer `Chart.yaml`. `chart.version` im Sollzustand
muss exakt deren `version` entsprechen. Das App-Programm übergibt den absoluten
lokalen Pfad ohne Repository-Zugangsdaten an Helm. Der vollständige Checkout
von `app-deploy.yml` und `app-plan.yml` enthält die Charts.

Das [Standard-Chart web-app](../../charts/web-app/README.md) erzeugt ein Deployment
und einen Service auf Port 80. Unter `values` stehen `container.port` (Standard 8080),
`container.env` (String-Werte), optional `container.command` und `container.args`,
`probes.path` (Standard `/`) sowie bei PVC `persistence.mountPath` (absoluter Pflichtpfad).
Der Prüfpfad soll `availability.path` entsprechen. Mit PVC gilt `Recreate`, sonst
`RollingUpdate`. Secrets kommen ausschließlich über das Plattform-Secret.
`securityContext.runAsNonRoot` ist standardmäßig false. `securityContext.relaxed`
ist ebenfalls false; true lässt das Verbot von Privilegieneskalation und das
Entfernen aller Capabilities weg. Einzelheiten und alle Defaults stehen im Chart-README.

Beispiel: `examples/valid/catalog/whoami/app.yaml` mit passendem Sollzustand unter
`examples/valid/environments/staging/whoami.yaml`. Image-Digest und Runbook-Commit
sind dort ausdrücklich Platzhalter. Das gemeinsame Runbook liegt unter
`platform/operations/apps/standard-runbook.md`.

## Gerüst mit new-app

`--data-blocks mariadb` setzt `DATABASE_URL`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`
und `DB_PASSWORD` auf die entsprechenden `app:mariadb-*`-Referenzen.
`postgres-database,mariadb` zusammen wird abgelehnt.

`--generated-secret ENV_NAME` ist wiederholbar, etwa `--generated-secret APP_SECRET
--generated-secret ADMINPASS`. Daraus werden `generatedSecrets: [app-secret, adminpass]`
und Referenzen `APP_SECRET: app:app-secret`, `ADMINPASS: app:adminpass`.
Erlaubt sind Namen gemäß `^[A-Z][A-Z0-9_]*$` mit höchstens 63 Zeichen.
Duplikate, Überschneidungen mit `--env`, Baustein-Schlüsseln und deren Standardvariablen
werden abgelehnt; bei SQL gilt das auch für `DB_*`.

`--root-start` setzt `values.securityContext.relaxed: true`, weil Apache/PHP-Images
als root starten und den Benutzer selbst wechseln. `runAsNonRoot` bleibt im Chart false.
Das lässt die Vorgaben für Privilegieneskalation und Capabilities weg.

`--memory-limit 1Gi --cpu-request 100m` überschreibt die Defaults `512Mi` und `50m`.
Die Speicheranfrage bleibt bis einschließlich `512Mi` Limit bei `128Mi`;
darüber gilt `min(Limit/2, 512Mi)`. Positive Kubernetes-Quantities wie `512Mi`, `1Gi`,
`100m`, `0.5`, `2` und `1G` werden geprüft. CPU ist nur über die CLI konfigurierbar.

Das Kimai-Beispiel unter `examples/valid/` nutzt MariaDB, ein separates App-Volume,
generierte `APP_SECRET`/`ADMINPASS`, Root-Start, Port 8001 und 1Gi Speicherlimit.
Image-Digest, Runbook-Commit, Admin-Mail und Domain vor Aufnahme ersetzen;
der Beispiel-Digest gehört zum gewünschten Image `kimai/kimai2:apache` und ist ein Platzhalter.
Der Login-Pfad `/en/login` ist noch nicht im Cluster geprüft.

## Smoke-Test-Vertrag

- Container-Image, per Digest referenziert, läuft als Kubernetes-Job im App-Namespace mit `TARGET_URL=https://<app-id>.<baseDomain>` (HTTP-Fallback nach E8), den `env`-Werten und genau den Schlüsseln aus `smokeTest.secrets` per `secretKeyRef` auf `<app-id>-platform`.
- Exit-Code 0 bei Erfolg, ungleich 0 bei Fehlschlag.
- Ergebnis: **eine JSON-Zeile als letzte Zeile der Ausgabe** nach [`smoke-test-output.schema.json`](v0.1/smoke-test-output.schema.json) mit `success`, `startedAt`, `finishedAt`, `evidence`. Diagnosezeilen davor sind erlaubt; `kubectl logs` vermischt stdout und stderr. Secrets gehören weder in die Ausgabe noch in `evidence`.
- Erreichbarkeit allein ist kein Nachweis: Der Test prüft die zentrale Nutzeraktion.
- Der Ausführende ergänzt `appId`, `environment`, das tatsächlich laufende `release` (Namespace-Annotationen `ops.mmk/release-git-sha`, `-image-digest`, `-chart-version` und Pod-Image-Digest), `desiredRelease` mit `deviations` und `checkRef` (Digest des Smoke-Test-Images).
- Widerspruch zwischen Exit-Code und `success`, ungültiges JSON oder fehlende Felder gelten als Fehlschlag mit `failureReason`.

Beispiel eines fehlgeschlagenen Ergebnisses: [`examples/results/verification-result-failed.json`](examples/results/verification-result-failed.json). Für Apps ohne eigenen Test gibt es das generische Image `smoke-http` (`platform/infra/app-runtime/smoke-http/`), gesteuert über `CHECK_PATH`, `EXPECT_STATUS`, `TIMEOUT_SECONDS`, `ATTEMPTS` (Standard 3) und `RETRY_DELAY_SECONDS` (Standard 5); die Wiederholung überbrückt die kurze Verzögerung des Ingress direkt nach einem Rollout.

### Abgleich mit C und D (Task 1.3)

Zwei Ausführende erzeugen Verifikationsergebnisse: `app-deploy.yml` (B) für Zusammenfassung und Aktionsprotokoll, D's Verifikationswerkzeug als `VerificationEvidence` (C's Vertrag in `operations-agent/src/contracts/v0_1.py`, Felder in snake_case) für die Abschlussprüfung. Beide starten denselben Job: `TARGET_URL`, `smokeTest.env`, Test-Secrets per `secretKeyRef` auf `<app-id>-platform`, `acr:`-Auflösung, Ergebnis aus der letzten JSON-Zeile, Erfolg nur bei Exit-Code 0 und `success: true`.

| B `verification-result` v0.1 | C/D `VerificationEvidence` | Bemerkung |
| --- | --- | --- |
| `appId`, `environment` | `app_id`, `environment` | gleich |
| `release.{gitSha,imageDigest,chartVersion}` | `release.{git_sha,image_digest,chart_version}` | Iststand aus `ops.mmk/release-*` und Pod-Digest |
| `checkRef` | `check_ref` | Digest des Smoke-Test-Images |
| `startedAt`, `finishedAt` | `started_at`, `finished_at` | D: `finished_at` leer bei Zeitüberschreitung |
| `success`, `exitCode` | `success`, `exit_code` | gleiche Regel |
| `evidence[]` | `result.evidence[]`, `evidence_refs` | D führt zusätzlich Job- und Kubernetes-Referenzen |
| `desiredRelease`, `deviations`, `releaseMatchesDesired` | – | bei D prüft C's Abschlussprüfung Soll gegen Ist |
| `failureReason` | – | D: `timed_out` |

Prüfgrundlage für die gemeinsame Durchsicht ist [`examples/results/verification-result-failed.json`](examples/results/verification-result-failed.json): Exit-Code 1, `success: false`, Teilprüfungen `document` und `attachment` fehlgeschlagen. Von D bestätigt (`platform/operations/agent/uebergaben.md`, Commit `4db663e`); Bestätigung durch C offen.

## Validierung

```bash
npm ci
npx tsx src/cli.ts validate                    # platform/catalog und platform/environments
npx tsx src/cli.ts validate --tree examples/valid
npm test                                       # gültige und ungültige Beispiele, Ergebnisse
```

Die Validierung liest A's Verträge und bricht ab, wenn sie fehlen:

| Vertrag | Standardpfad | Erwartete Form |
| --- | --- | --- |
| Konventionen | `platform/schemas/conventions/conventions.json` | `{ "appId": { "pattern": "…", "endPattern": "…", "reserved": [...] }, "environments": ["staging", …] }` |
| Foundation-Ausgaben | `platform/schemas/foundation-outputs/foundation-outputs.schema.json` | JSON-Schema mit einer Eigenschaft je Ausgabe unter `properties` |

Geprüft werden außerdem `appId.endPattern` (kein Bindestrich am Ende) und `appId.reserved`. Abweichende Pfade über `--conventions`, `--foundation-outputs` oder `APP_CATALOG_CONVENTIONS`, `APP_CATALOG_FOUNDATION_OUTPUTS`. Die Tests verwenden A's gelieferte Dateien.

Weitere Befehle: `show`, `set-desired`, `affected`, `verification-result`, `deployment-result` (`npx tsx src/cli.ts` ohne Argumente zeigt die Hilfe).
