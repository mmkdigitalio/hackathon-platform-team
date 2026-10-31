# Self-Service-Onboarding für neue Apps

## Why

AFFiNE auf die Plattform zu bringen hat zwölf Lieferpunkte (L1–L12), ein eigenes Chart, ein eigenes Smoke-Image, einen eigenen Sync-Workflow und Handarbeit von drei Strängen gebraucht. Für eine typische kleine Eigenentwicklung oder ein fertiges Container-Image ist das zu viel: Wer heute eine App aufnehmen will, muss Helm, den Werte-Vertrag, das Katalogschema, Digests, Runbook-Commits und die Workflows kennen. Die wirtschaftliche Hypothese (Produktdokument §1) hängt aber gerade daran, dass eine neue App billig aufzunehmen ist.

## What Changes

- **Standard-Chart `web-app`** unter `platform/charts/web-app/`: ein Deployment, ein Service, Proben auf den Prüfpfad, Umgebungsvariablen aus dem Plattform-Secret, optional ein Volume (dann Strategie `Recreate`). Eine App mit Container-Image braucht kein eigenes Chart mehr. Katalog: `chart: { platform: web-app }`; das App-Programm nimmt das Chart direkt aus dem Repository.
- **Standard-Runbook** `platform/operations/apps/standard-runbook.md` für Apps ohne eigenes Runbook (Symptome, Diagnose, `restart`/`redeploy`, Grenzen).
- **Standard-Smoke-Test**: `smoke-http` gegen den Prüfpfad wird automatisch eingetragen.
- **Gerüst-Befehl** `new-app` in der Katalog-CLI: erzeugt Katalogeintrag und Sollzustand aus wenigen Angaben (App-ID, Image oder Quell-Repository, Port, Prüfpfad, Datenbausteine) und validiert sie.
- **Workflow „App aufnehmen“** (`app-onboard.yml`) mit Formular in GitHub: baut bei Bedarf das Image aus einem Repository mit Dockerfile, legt es in die ACR, erzeugt beide Dateien und öffnet einen Pull Request mit Checkliste. Nach dem Merge rollt `app-deploy.yml` aus.
- **Workflow „App-Release“** (`app-release.yml`): baut eine neue Version aus dem Quell-Repository und schlägt den Sollzustand per PR vor (wiederverwendet `app-update-desired-state.yml`).
- **Anleitung für Einsteiger** `platform/operations/apps/ONBOARDING.md`.

## Capabilities

### New Capabilities

- `app-self-service`: Standard-Chart, Gerüst, Aufnahme- und Release-Workflow, Standard-Runbook und -Smoke-Test.

### Modified Capabilities

- `app-catalog` (app-delivery-and-onboarding): neue Chart-Quelle `platform`.
- `app-delivery` (app-delivery-and-onboarding): App-Programm löst `chart.platform` aus dem Repository auf.

## Impact

`platform/charts/`, `platform/infra/app-runtime/`, `platform/schemas/app-catalog/` (Schema, CLI, Beispiele), `.github/workflows/app-onboard.yml`, `.github/workflows/app-release.yml`, `platform/operations/apps/`. Keine neue Azure-Ressource; Verfügbarkeitstest und Alarm in Azure Monitor entstehen weiter beim nächsten Foundation-Rollout, bis dahin prüft der geplante Gesundheitscheck (`health-check.yml`) jede Katalog-App alle 30 Minuten.

## Out of Scope

- Apps mit mehreren Containern, Hintergrund-Jobs oder eigenen Ingress-Regeln (dafür bleibt das eigene Chart).
- Automatische Aufnahme beliebiger Software (Produktdokument §8).
- Produktionsumgebung.
