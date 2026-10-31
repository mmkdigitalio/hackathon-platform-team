# Standard-Chart web-app

Für eine HTTP-App mit einem Container. Das Chart erzeugt ausschließlich ein Deployment
und einen Service auf Port 80. Secrets, Ingress und PVC stellt das App-Programm bereit.
Deployment und Service heißen `platform.appId`, ersatzweise `Release.Name`.
Das Image wird als `image.repository@image.digest` verwendet.
Leere Image-Defaults erlauben Linting; vor einem Rollout setzt die Plattform beide Werte.

## Werte

| Schlüssel | Standard | Bedeutung |
| --- | --- | --- |
| `image.repository` | `""` | Reserviert, Image-Repository |
| `image.digest` | `""` | Reserviert, SHA-256-Digest |
| `secretName` | `""` | Reserviert, optionales envFrom-Secret |
| `imagePullSecretName` | `""` | Reserviert, optionales Pull-Secret |
| `rolloutMarker` | `""` | Reserviert, erzwingt nur das Helm-Upgrade |
| `platform.appId` | `""` | Reserviert, Name und Instance-Label |
| `platform.environment` | `""` | Reserviert, Umgebung |
| `platform.namespace` | `""` | Reserviert, Namespace |
| `platform.host` | `""` | Reserviert, öffentlicher Host |
| `platform.url` | `""` | Reserviert, öffentliche URL |
| `platform.persistentVolumeClaim` | `""` | Reserviert, vorhandenes PVC |
| `platform.replicas` | `1` | Reserviert, aus sizes.replicas |
| `platform.resources` | `{}` | Reserviert, aus sizes.resources |
| `platform.release.gitSha` | `""` | Reserviert, Release-Commit |
| `platform.release.imageDigest` | `""` | Reserviert, Release-Digest |
| `platform.release.chartVersion` | `""` | Reserviert, Chart-Version |
| `container.port` | `8080` | Pflichtfeld im zusammengeführten Wertesatz, Port 1 bis 65535 |
| `container.env` | `{}` | Nicht geheime Variablen, Werte als Strings |
| `container.command` | `[]` | Optionaler Einstiegspunkt |
| `container.args` | `[]` | Optionale Argumente |
| `probes.path` | `/` | HTTP-Pfad für alle drei Proben |
| `persistence.mountPath` | `""` | Absoluter Pfad, Pflicht bei vorhandenem PVC |
| `securityContext.runAsNonRoot` | `false` | Bei geeigneten Images auf true setzen |
| `securityContext.relaxed` | `false` | Sicherheitsvorgaben für Eskalation und Capabilities auslassen |

Im Sollzustand nur die nicht reservierten Werte unter `values` eintragen.
`rolloutMarker` und die Release-Referenz verändern kein Pod-Template.
Es gibt keine Checksum-Annotationen. Nach einer Secret-Rotation ist ein Neustart nötig.

## Proben und Persistenz

Readiness prüft alle 5 Sekunden, Liveness alle 10 Sekunden, jeweils mit 2 Sekunden Timeout
und 3 Fehlversuchen. Die Startup-Probe erlaubt 60 Versuche alle 5 Sekunden, also etwa
5 Minuten Startzeit. Der Prüfpfad muss ohne Anmeldung HTTP 200 liefern.
Ein PVC wird als Volume `data` am Mount-Pfad eingebunden, die Strategie ist dann `Recreate`.
Ohne PVC gilt `RollingUpdate`.

## Container-Sicherheit

Standardmäßig gelten `allowPrivilegeEscalation: false` und `capabilities.drop: [ALL]`.
`runAsNonRoot` ist separat konfigurierbar und standardmäßig false, da beliebige Images
Root benötigen können. `securityContext.relaxed: true` lässt die beiden Vorgaben für
Privilegieneskalation und Capabilities weg; dann gelten die Kubernetes-Defaults.
Das gewährt dem Container mehr Rechte und braucht eine begründete Image-Anforderung.
`runAsNonRoot` bleibt auch bei `relaxed: true` wirksam.
