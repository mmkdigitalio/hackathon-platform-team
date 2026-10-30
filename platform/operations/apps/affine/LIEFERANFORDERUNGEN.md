# Lieferanforderungen an das AFFiNE-Paket

Für: Lieferung aus `mmkdigitalio/hackathon-platform-affine`. Strang B integriert die Lieferung über den gemeinsamen App-Pfad und legt `platform/environments/staging/affine.yaml` an. Chart-Inhalt, Smoke-Test-Logik und Runbook-Inhalt verantwortet die Lieferung.

## Checkliste

| Nr. | Lieferung | Anforderung |
| --- | --- | --- |
| L1 | Katalogeintrag | `platform/catalog/affine/app.yaml` nach [Schema v0.1](../../../schemas/app-catalog/README.md), per PR in dieses Repository. Vorlage: [`examples/valid/catalog/affine/app.yaml`](../../../schemas/app-catalog/examples/valid/catalog/affine/app.yaml). `npx tsx src/cli.ts validate` in `platform/schemas/app-catalog/` muss grün sein. |
| L2 | Chart | Feste Version, als OCI-Artefakt veröffentlicht (z. B. `oci://ghcr.io/mmkdigitalio/charts/affine`, öffentlich lesbar, oder in die Foundation-ACR). Unverändertes Upstream-AFFiNE-Image, kein Fork. |
| L3 | Chart-Werte-Vertrag | Liest `image.repository`, `image.digest`, `imagePullSecretName`, `secretName` (per `envFrom`), `platform.persistentVolumeClaim`, `platform.replicas`, `platform.resources`, siehe [Werte-Vertrag](../../../schemas/app-catalog/README.md#werte-vertrag-für-charts). Legt selbst keine Secrets, Ingress-Regeln, PVCs oder Datenbanken an. |
| L4 | Deployment | Haupt-Deployment heißt `affine`, Strategie **`Recreate`** (ReadWriteOnce-Volume; sonst hängt ein fehlerhaftes Rolling Update statt eines Ausfalls). Deployment mit Label `app.kubernetes.io/instance: affine`. Service `affine` auf Port 3010 (oder abweichend in `ingress` eintragen). |
| L5 | Konfiguration | Werte, die sich je Umgebung unterscheiden, unter `values` im Sollzustand; falls vorhanden, `values.schema.json` im Chart. `rolloutMarker` und `platform.release.gitSha` nicht in Pod-Templates verwenden; ein `values.schema.json` darf den Schlüssel `platform` nicht verbieten. |
| L6 | Verfügbarkeitspfad | `availability.path` liefert ohne Anmeldung HTTP 200, solange AFFiNE nutzbar ist (Vorschlag `/info`). |
| L7 | Smoke-Test-Image | Per Digest referenziert, Vertrag laut [Smoke-Test-Vertrag](../../../schemas/app-catalog/README.md#smoke-test-vertrag): `TARGET_URL`, Exit-Code, eine JSON-Zeile als letzte Ausgabezeile. Prüft Anmeldung, Dokument und Anhang. Benötigte Zugangsdaten über `smokeTest.secrets`, z. B. ein generiertes Passwort (`generatedSecrets`). |
| L8 | Persistenznachweis | Der Smoke-Test legt beim ersten Lauf ein festes Testdokument mit Anhang an und findet es in späteren Läufen wieder (für Abnahmeszenario 9); `evidence` nennt Dokument und Anhang. |
| L9 | Runbook | Im Lieferrepository, referenziert mit `repository`, `path` und vollständigem Commit-SHA. Enthält Symptome, Diagnose, die erlaubten Aktionen `restart`, `redeploy` und Grenzen. |
| L10 | Datenverfahren | `dataProcedures.migration`, `backup`, `restore` im Katalogeintrag: Upstream-Migrationen beim Start, Sicherung der Datenbank über den Foundation-Server, Anhänge auf dem PVC ohne Sicherung (Staging-Risiko), Wiederherstellung nur manuell. |
| L11 | Fehlerwert | Pfad und Wert für das nicht reparierbare Fehlerszenario in [`fault-invalid-config.yaml`](fault-invalid-config.yaml): schemagültig, AFFiNE startet damit nicht, berührt weder Daten noch Secrets. |
| L12 | Smoke-Image-Herkunft | Angabe, ob das Smoke-Test-Image aus der ACR (`acr:…`) oder öffentlich gezogen wird. |

## Bereitgestellt durch den App-Pfad

| Baustein | Werte im Secret `affine-platform` (Schlüssel frei wählbar in `secrets`) |
| --- | --- |
| `postgres-database` | `app:postgres-url` (`postgresql://…?sslmode=require`), `app:postgres-host`, `-database`, `-user`, `-password` |
| `redis` | `app:redis-host`, `app:redis-password` (Port 6379, ohne Persistenz) |
| `persistent-volume` | PVC `affine-data`, Größe aus `sizes.volumeSize` |

Host `https://affine.<baseDomain>` mit Let's-Encrypt-Zertifikat über die Ingress-Regel des App-Stacks.

## Übergabe

| Schritt | Status |
| --- | --- |
| Anforderungen übergeben an den AFFiNE-Kollegen | erledigt |
| Übergabe bestätigt | erledigt, Lieferung in `mmkdigitalio/hackathon-platform-affine` (`platform-delivery/README.md`) |
| Entwurf `platform/catalog/affine/app.yaml` validiert | erledigt |
| L11 Fehlerwert festgelegt | erledigt: `affine.nodeOptions = --max-old-space-size=16` |
