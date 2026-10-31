# Tasks

## 0. Voraussetzungen

- [ ] 0.1 Entra-App-Registrierung mit Redirect-URI `https://auth.<baseDomain>/oauth2/callback` anlegen lassen; Client-ID, Tenant-ID, Client-Secret im Passwort-Manager; optional Gruppe festlegen.

## 1. Foundation

- [ ] 1.1 oauth2-proxy (2 Replicas) und Middleware `entra-auth` hinter Schalter `entraLogin`; Secrets per `foundation.sh secrets`; Mock-Tests.
- [ ] 1.2 Verfügbarkeitstest auf `auth.<baseDomain>/ping`; Doku in `platform/operations/foundation/` (Einrichtung, Rotation des Secrets, geänderte baseDomain).

## 2. App-Pfad

- [ ] 2.1 Katalogfelder `ingress.auth`, `ingress.publicPaths` mit Validierung; App-Programm erzeugt Ingress mit Middleware und Ingress für öffentliche Pfade; Tests.
- [ ] 2.2 Gerüst `new-app` setzt `auth: entra` und den Prüfpfad als öffentlichen Pfad; Formularfeld in `app-onboard.yml`.
- [ ] 2.3 Bestehende Apps einstufen: Plattform-UI `entra`, Agenten-API `entra` mit `publicPaths` `/healthz`, `/events/`; AFFiNE nach Entscheidung.

## 3. Chat

- [ ] 3.1 Chat-API übernimmt Identität aus vertrauenswürdigen Headern; UI ohne Code-Eingabe bei Anmeldung; Tests.

## 4. Nachweis

- [ ] 4.1 In Staging: Anmeldung, Single Sign-on über zwei Apps, abgewiesener gefälschter Header, Alarm-Webhook und Gesundheitscheck weiter grün.
