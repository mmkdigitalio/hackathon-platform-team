# Design: Self-Service-Onboarding

## Wie AFFiNE aufgenommen wurde (Ausgangslage)

1. Lieferrepository mit Chart, Smoke-Test-Image, Runbook und Katalogentwurf (L1–L12).
2. Sync-Workflow `app-affine-sync.yml` baut Chart und Smoke-Image in die ACR und schreibt Katalog und Sollzustand.
3. `app-deploy.yml` legt Namespace, Datenbank, Redis, Volume, Secrets, Ingress an, rollt das Chart aus und prüft per Smoke-Test.
4. Verfügbarkeitstest und Alarm entstehen beim nächsten Foundation-Rollout (A).

Aufwand und Wissen stecken in Schritt 1 und 2; Schritt 3 ist bereits generisch.

## Entscheidungen

1. **Standard-Chart im Repository statt in der ACR.** `chart.platform: web-app` verweist auf `platform/charts/web-app`; `app-deploy.yml` hat das Repository ausgecheckt, das App-Programm übergibt den lokalen Pfad an Helm. `chart.version` im Sollzustand muss der Version in `Chart.yaml` entsprechen (Validierung), damit der Sollzustand eindeutig bleibt. Ein neues Chart-Release ist ein Commit mit erhöhter Version.
2. **Werte des Standard-Charts.** Neben dem Werte-Vertrag: `container.port` (Pflicht), `container.env` (nicht geheime Variablen), `container.command`/`args` (optional), `probes.path` (Standard: Prüfpfad aus dem Katalog, vom Gerüst gesetzt), `persistence.mountPath` (nur mit `persistent-volume`). Secrets kommen ausschließlich über `secretName` (envFrom).
3. **Gerüst als CLI und Workflow.** Die Logik liegt in `platform/schemas/app-catalog/src/cli.ts new-app`; der Workflow ruft nur die CLI. So ist dasselbe Gerüst lokal und in GitHub nutzbar und getestet.
4. **Images.** Entweder ein fertiges Image mit Digest (öffentlich erreichbar) oder ein Build aus einem GitHub-Repository mit Dockerfile in die Foundation-ACR (`acr:<app-id>@sha256:…`). Der Build nutzt die ACR-Zugangsdaten aus `foundation-outputs` und für private Repositories das Token der GitHub App.
5. **Standardwerte, die einen Agenten ermöglichen:** `allowedActions: [restart, redeploy]`, Standard-Runbook mit Commit-SHA, `smoke-http` auf den Prüfpfad, `dataProcedures` mit ehrlichen Standardtexten (Datenbanksicherung über den Foundation-Server, Volumes ohne Sicherung).
6. **Pull Request statt Direkt-Commit.** Aufnahme und Release öffnen PRs; `app-plan.yml` prüft; ein Mensch merged (Auto-Merge nur, wenn das Repository ihn erlaubt).

## Risiken

- Das Standard-Chart deckt nur einfache Web-Apps ab; Abweichungen brauchen weiter ein eigenes Chart.
- Ein Smoke-Test auf den Prüfpfad belegt Erreichbarkeit, nicht die wichtigste Nutzeraktion (Produktdokument §5.3); die Anleitung fordert einen fachlichen Smoke-Test als nächsten Schritt.
