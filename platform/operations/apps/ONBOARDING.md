# Eine App auf die Plattform bringen

Diese Anleitung bringt deine App in etwa einer Stunde auf die MMK-Plattform, auch ohne Kubernetes-, Helm- oder Azure-Wissen. Am Ende läuft sie unter `https://<app-id>.57-153-7-126.sslip.io`, steht in der [Plattform-UI](https://platform-status.57-153-7-126.sslip.io), wird überwacht, und Kolleginnen und Kollegen können Probleme im Chat melden, die der Betriebsagent bearbeitet.

## Was die Plattform für dich übernimmt

| Du lieferst | Die Plattform erledigt |
| --- | --- |
| Ein Container-Image oder ein GitHub-Repository mit `Dockerfile` | Bauen, in die Registry legen, ausrollen |
| Den Port, auf dem die App lauscht | HTTPS-Adresse mit Zertifikat |
| Einen Prüfpfad, der `200` liefert, wenn die App funktioniert | Überwachung alle 30 Minuten, Prüfung nach jedem Deployment |
| Die Angabe, ob du PostgreSQL, Redis oder ein Dateiverzeichnis brauchst | Datenbank, Redis, Volume und die Zugangsdaten als Umgebungsvariablen |
| – | Betriebsagent mit Runbook, Neustart, Wiederherstellung, Chat-Meldungen |

## Voraussetzungen

1. **GitHub-Zugang** zur Organisation `mmkdigitalio` mit Schreibrecht auf `mmkdigitalio/hackathon-platform-team` (frag die Plattform-Betreuung).
2. **Deine App läuft als Container.** Prüfe lokal:
   ```bash
   docker build -t meine-app .
   docker run --rm -p 8080:8080 meine-app
   curl -i http://localhost:8080/health
   ```
   Die Antwort muss mit `HTTP/1.1 200` beginnen. Port und Pfad merkst du dir für das Formular.
3. **Konfiguration über Umgebungsvariablen.** Passwörter und Schlüssel gehören nicht ins Image und nicht ins Formular. Datenbank- und Redis-Zugang stellt die Plattform bereit (siehe unten).
4. **Dateien, die bleiben sollen** (Uploads, SQLite), liegen in einem festen Verzeichnis, z. B. `/data`.
5. **Private Repositories:** Die GitHub App der Plattform (`hackathonplatformagent`) muss Zugriff auf dein Repository haben. Ein Org-Admin fügt es unter *Settings → GitHub Apps → hackathonplatformagent → Repository access* hinzu. Öffentliche Repositories brauchen nichts.

### Variablen, die deine App bekommt

| Baustein im Formular | Umgebungsvariablen |
| --- | --- |
| PostgreSQL | `DATABASE_URL` (`postgresql://…?sslmode=require`), `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` |
| Redis | `REDIS_HOST`, `REDIS_PASSWORD` (Port 6379, ohne Persistenz) |
| Volume | ein beschreibbares Verzeichnis am angegebenen Pfad (Standard `/data`) |

Eigene, nicht geheime Werte (z. B. `LOG_LEVEL=info`) trägst du im Formular ein.

## Schritt 1: Formular „App aufnehmen“ ausfüllen

1. Öffne <https://github.com/mmkdigitalio/hackathon-platform-team/actions/workflows/app-onboard.yml>.
2. Klicke **Run workflow** und fülle aus:

| Feld | Beispiel | Hinweis |
| --- | --- | --- |
| `appId` | `zeiterfassung` | Beginnt mit einem Kleinbuchstaben, dann Kleinbuchstaben, Ziffern oder Bindestrich, 2–31 Zeichen, endet nicht mit Bindestrich. Wird Teil der Adresse. |
| `description` | `Zeiterfassung für Projekte` | Ein Satz, erscheint in der Plattform-UI. |
| `sourceRepository` | `mmkdigitalio/zeiterfassung` | Repository mit `Dockerfile`. Leer lassen, wenn du ein fertiges Image angibst. |
| `sourceRef` | `main` | Branch oder Tag, der gebaut wird. |
| `dockerfilePath` / `buildContext` | `Dockerfile` / `.` | Nur ändern, wenn das Dockerfile nicht im Wurzelverzeichnis liegt. |
| `image` | `ghcr.io/traefik/whoami@sha256:…` | Nur ohne `sourceRepository`: fertiges öffentliches Image **mit** `@sha256:`-Digest. |
| `port` | `8080` | Port aus den Voraussetzungen. |
| `healthPath` | `/health` | Prüfpfad, der ohne Anmeldung `200` liefert (Standard `/`). |
| `postgres`, `redis`, `volume` | Haken setzen | Nur, was die App braucht. |
| `volumeSize` / `mountPath` | `5Gi` / `/data` | Nur mit Volume. |
| `env` | `LOG_LEVEL=info` | Eine Zeile je Variable, nichts Geheimes. |

3. Klicke **Run workflow**. Nach 2–10 Minuten (Build) ist der Lauf grün, und die Zusammenfassung verlinkt einen **Pull Request „App aufnehmen: <appId>“**.

Schlägt der Lauf fehl, steht der Grund in der Zusammenfassung (typische Fehler unten).

## Schritt 2: Pull Request prüfen und mergen

1. Öffne den Pull Request. Er enthält zwei Dateien:
   - `platform/catalog/<appId>/app.yaml`: was deine App ist (Chart, Prüfpfad, Datenbausteine, erlaubte Aktionen des Agenten).
   - `platform/environments/staging/<appId>.yaml`: welche Version läuft (Image-Digest, Größe, Einstellungen).
2. Warte, bis der Check **app-plan / Plan-Ergebnis** grün ist. Er zeigt, was angelegt wird.
3. **Merge** (Squash). Damit startet automatisch das Deployment.

## Schritt 3: Deployment beobachten

1. Öffne <https://github.com/mmkdigitalio/hackathon-platform-team/actions/workflows/app-deploy.yml>; der oberste Lauf ist deiner.
2. Grün heißt: ausgerollt, Pods bereit, Smoke-Test auf deinen Prüfpfad bestanden. Die Zusammenfassung nennt Adresse, Version und Nachweise.

## Schritt 4: Prüfen

- `https://<appId>.57-153-7-126.sslip.io` öffnet deine App.
- In der [Plattform-UI](https://platform-status.57-153-7-126.sslip.io/applications) steht sie mit Status „Erreichbar“.
- Auf ihrer Detailseite gibt es den **Support-Chat**. Mit dem Zugangscode der Plattform-Betreuung kannst du dort eine Testmeldung schicken, z. B. „Bitte prüfe, ob die App erreichbar ist“. Der Betriebsagent antwortet im Chat.

## Neue Version veröffentlichen

1. Änderungen in dein Repository pushen.
2. <https://github.com/mmkdigitalio/hackathon-platform-team/actions/workflows/app-release.yml> → **Run workflow** mit `appId` und `sourceRef` (Branch oder Tag).
3. Es entsteht ein Pull Request, der nur die Image-Version ändert. Nach grünem `app-plan` mergen; das Deployment läuft automatisch.

Die Release-Pipeline baut immer das `Dockerfile` im Wurzelverzeichnis des Repositorys. Liegt es woanders, frag die Plattform-Betreuung.

Zurück auf eine frühere Version: denselben Pull Request mit `git revert` rückgängig machen (Vorsicht bei Datenbankänderungen, siehe [README.md](README.md#rollback)).

## Was danach automatisch passiert

- **Überwachung:** Der Gesundheitscheck prüft alle 30 Minuten deinen Prüfpfad. Fällt er aus, bekommt der Betriebsagent einen Auftrag. Verfügbarkeitstests und Alarme in Azure Monitor kommen mit dem nächsten Foundation-Rollout dazu.
- **Betriebsagent:** Er folgt dem [Standard-Runbook](standard-runbook.md): Zustand prüfen, bei hängender App neu starten, bei Abweichung vom Sollzustand neu ausrollen, danach verifizieren. Was er nicht lösen kann, meldet er als „Braucht Aufmerksamkeit“; Konfigurationsfehler schlägt er als Pull Request vor.
- **Daten:** Die Datenbank wird über den PostgreSQL-Server der Plattform 7 Tage gesichert. Volumes haben in Staging **keine** Sicherung.

## Typische Fehler

| Meldung | Ursache und Lösung |
| --- | --- |
| „App-ID verletzt die Konvention“ | Nur `a-z`, `0-9`, `-`, beginnt mit einem Buchstaben, endet nicht mit `-`; `mmk-platform` und `foundation` sind reserviert. |
| „Öffentliches Image braucht … @sha256-Digest“ | Bei `image` immer `…@sha256:…` angeben (`docker buildx imagetools inspect <image>` zeigt ihn). |
| Build schlägt fehl | Lokal `docker build` prüfen; bei privatem Repository Zugriff der GitHub App prüfen. |
| `app-deploy` rot im Schritt Smoke-Test | Der Prüfpfad liefert nicht `200` oder die App lauscht auf einem anderen Port. Lokal mit `docker run` und `curl` prüfen, dann über „App veröffentlichen“ neu ausrollen bzw. den Sollzustand korrigieren. |
| Pod startet nicht (`CrashLoopBackOff`) | Meist fehlt eine Umgebungsvariable oder die Datenbankverbindung. Im Chat nachfragen: der Agent liest Logs und Events. |
| „Zieldateien existieren bereits“ | Die App-ID ist schon vergeben. |

## Nächste Schritte für den Dauerbetrieb

1. **Fachlicher Smoke-Test:** Der Standard prüft nur Erreichbarkeit. Besser ist ein Test der wichtigsten Nutzeraktion (z. B. anmelden und einen Datensatz anlegen); Vertrag und Beispiel in [schemas/app-catalog/README.md](../../schemas/app-catalog/README.md#smoke-test-vertrag).
2. **Eigenes Runbook:** Bekannte Fehlerbilder deiner App in deinem Repository beschreiben und im Katalog unter `runbook` verlinken; der Agent nutzt es dann statt des Standard-Runbooks.
3. **Eigenes Chart** nur, wenn die App mehrere Container, Hintergrund-Jobs oder besondere Ingress-Regeln braucht ([Lieferanforderungen am Beispiel AFFiNE](affine/LIEFERANFORDERUNGEN.md)).

## Hilfe

Fragen zur Plattform im Chat der Plattform-UI oder bei der Plattform-Betreuung (Strang-Verantwortliche in [openspec/PARALLEL-IMPLEMENTATION.md](../../../openspec/PARALLEL-IMPLEMENTATION.md)).
