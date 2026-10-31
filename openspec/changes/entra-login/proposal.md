# Einheitlicher Login mit Entra ID für alle Apps

## Why

Alle Staging-Hosts unter `*.57-153-7-126.sslip.io` sind öffentlich erreichbar: die Plattform-UI mit Betriebsdaten, der Support-Chat (heute mit einem geteilten Zugangscode) und jede neu aufgenommene App. Mitarbeitende haben bereits ein Microsoft-Konto. Ein Login am Ingress schützt jede App ohne Änderung an der App, ersetzt den Zugangscode durch eine echte Identität und lässt sich für neue Apps standardmäßig einschalten.

## What Changes

- **Zentrale Anmeldung am Ingress:** oauth2-proxy unter `auth.<baseDomain>` mit Entra ID (OIDC) und eine Traefik-Middleware `entra-auth` (ForwardAuth). Ein Login gilt für alle Hosts unter `<baseDomain>` (Cookie auf der Basisdomain).
- **Pro App wählbar:** neues Katalogfeld `ingress.auth` mit `entra` (Standard für neue Apps über das Gerüst) oder `none`. Das App-Programm hängt bei `entra` die Middleware an die Ingress-Regel.
- **Ausnahmen für Maschinen:** Pfade, die ohne Anmeldung erreichbar bleiben müssen, stehen pro App unter `ingress.publicPaths` (z. B. `/healthz`, `/events/`); Verfügbarkeitstests, Gesundheitscheck und Webhooks funktionieren weiter.
- **Identität für Apps:** Die Middleware reicht `X-Forwarded-Email`, `X-Forwarded-User` und (optional) Gruppen an die App weiter.
- **Support-Chat mit Identität:** Ist die Plattform-UI geschützt, nimmt die Chat-API die Identität aus dem Login statt aus einem Zugangscode; der Name im Chat ist der echte Name. Der Zugangscode bleibt als Rückfall für Apps ohne Login.
- **Zugriff einschränken:** optional auf eine Entra-Gruppe (z. B. „MMK Mitarbeitende“).

## Capabilities

### New Capabilities

- `platform-login`: zentrale Entra-Anmeldung, Middleware, Ausnahmen, Identitäts-Header.

### Modified Capabilities

- `app-catalog`: Felder `ingress.auth` und `ingress.publicPaths`.
- `app-self-service`: Gerüst setzt `ingress.auth: entra`.
- `employee-chat`: Identität aus dem Login.
- `shared-platform-runtime` (Foundation): oauth2-proxy, Middleware, neue Konfigurationssecrets.

## Impact

`platform/infra/foundation/` (Ingress-Modul), `platform/infra/app-runtime/`, `platform/schemas/app-catalog/`, `operations-agent/src/api/` (Chat-Identität), Katalogeinträge der bestehenden Apps, Doku in `platform/operations/foundation/` und `platform/operations/apps/`. Voraussetzung außerhalb des Repositorys: eine Entra-App-Registrierung (Abweichung von E3, siehe Design).

## Out of Scope

- Rollen und Berechtigungen innerhalb der Apps (Produktdokument §5.1; Phase 3).
- Login-Integration in Apps mit eigenem Benutzerkonzept (AFFiNE behält seinen Login; Doppelanmeldung wird in Kauf genommen oder `auth: none` gewählt).
- Produktionsdomain und Mandantentrennung.
