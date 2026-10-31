# Spec Delta

## Purpose

Alle Plattform-Apps lassen sich hinter einen gemeinsamen Login mit Entra ID stellen, ohne die Apps zu ändern.

## ADDED Requirements

### Requirement: Zentrale Anmeldung
Die Foundation SHALL bei aktiviertem Schalter `entraLogin` oauth2-proxy unter `auth.<baseDomain>` mit Entra ID als OIDC-Anbieter und eine Traefik-Middleware `entra-auth` (ForwardAuth) bereitstellen. Eine Anmeldung SHALL für alle Hosts unter `<baseDomain>` gelten.

#### Scenario: Erster Aufruf ohne Sitzung
- **WHEN** eine Mitarbeiterin ohne Sitzung `https://kimai.<baseDomain>/` öffnet
- **THEN** wird sie zum Microsoft-Login geleitet und danach auf die ursprüngliche Adresse zurück
- **AND** öffnet sie danach eine andere geschützte App ohne erneute Anmeldung

### Requirement: Schutz pro App
Der Katalog SHALL `ingress.auth` mit `entra` oder `none` erlauben; bei `entra` SHALL das App-Programm die Middleware an die Ingress-Regel hängen. Pfade unter `ingress.publicPaths` SHALL ohne Anmeldung erreichbar bleiben. Bei `auth: entra` SHALL die Validierung verlangen, dass `availability.path` in `publicPaths` liegt.

#### Scenario: Prüfpfad nicht freigegeben
- **WHEN** ein Katalogeintrag `auth: entra` setzt und `/health` als Prüfpfad hat, aber nicht in `publicPaths`
- **THEN** lehnt die Validierung den Eintrag mit einer verständlichen Meldung ab

### Requirement: Identität an Apps
Geschützte Apps SHALL die Identität als `X-Forwarded-Email` und `X-Forwarded-User` erhalten; Traefik SHALL gleichnamige eingehende Header entfernen.

#### Scenario: Gefälschter Header
- **WHEN** ein Client `X-Forwarded-Email: admin@mmkdigital.io` selbst mitsendet
- **THEN** erreicht die App nur der Wert aus der Anmeldung

### Requirement: Chat mit Identität
Ist die Plattform-UI geschützt, SHALL die Chat-API den Namen und die E-Mail-Adresse der meldenden Person aus der Anmeldung übernehmen und ohne Zugangscode annehmen; ohne Anmeldung SHALL der Zugangscode weiter gelten.

#### Scenario: Angemeldete Meldung
- **WHEN** ein angemeldeter Mitarbeiter im Chat eine Meldung sendet
- **THEN** steht seine E-Mail-Adresse als Melder im Auftrag, und kein Code wird abgefragt

### Requirement: Maschinenzugriffe bleiben möglich
Verfügbarkeitstests, der Gesundheitscheck, Smoke-Tests, `/healthz` und der Ereigniseingang der Agenten-API SHALL ohne Anmeldung funktionieren.

#### Scenario: Alarm-Webhook
- **WHEN** Azure Monitor einen Alarm an `/events/azure-monitor?key=…` sendet
- **THEN** nimmt die Agenten-API ihn wie bisher an
