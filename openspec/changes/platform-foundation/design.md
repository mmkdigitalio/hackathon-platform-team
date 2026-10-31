# Design

## Context

Motivation und Umfang stehen im [Proposal](proposal.md), Verhalten in [platform-bootstrap](specs/platform-bootstrap/spec.md) und [shared-platform-runtime](specs/shared-platform-runtime/spec.md). Die gemeinsamen Hackathon-Entscheidungen E1 bis E10 stehen in der [gemeinsamen Übersicht](../../PARALLEL-IMPLEMENTATION.md).

Die prägende Einschränkung: Das Team hat Contributor auf der Subscription, aber niemand kann Rollen zuweisen oder Entra-App-Registrierungen anlegen. Contributor darf Ressourcen anlegen und Schlüssel abrufen (`listKeys`, `listCredentials`, `listClusterAdminCredential`), aber nicht `Microsoft.Authorization/roleAssignments/write`. Auf GitHub können Teammitglieder Repositories anlegen und Repo-Secrets setzen, aber keine Organisations-Secrets. Für den Agenten gibt es die GitHub App `hackathonplatformagent`, installiert auf beiden Repositories (siehe Entscheidung 3; geändert 2026-10-30 (Erik); war: keine GitHub App). Mit Owner-Rechten lassen sich Rollen zuweisen; das nutzt nur die optionale Leseidentität des Agent-Workspace (Entscheidung 8; geändert 31.10.2026).

## Goals / Non-Goals

Goals:

- Eine lauffähige Staging-Plattform, die vollständig mit Contributor-Rechten entsteht.
- B, C und D erhalten alle Werte aus einem einzigen versionierten Ausgabevertrag.
- Wiederherstellung hängt weder vom Agenten noch von seiner Datenbank ab.

Non-Goals:

- Kein Least-Privilege, keine Secret-Rotation, keine privaten Netze.
- Keine Azure-Diagnose durch den Agenten in der Grundkonfiguration; eine schreibgeschützte Leseidentität für den Workspace-Pod gibt es nur als Opt-in (Entscheidung 8; geändert 31.10.2026; war: keine Azure-Diagnose durch den Agenten).
- Keine Bereitstellung aus GitHub Actions für Azure-Ressourcen.

## Decisions

### 1. Foundation lokal, Apps aus CI

Der Foundation-Stack braucht Azure Resource Manager und läuft deshalb lokal unter `az login` einer Contributor-Person. GitHub Actions bekommen keine Azure-Identität, weil eine Service-Principal- oder Managed-Identity-Anmeldung eine Rollenzuweisung bräuchte. App-Stacks enthalten deshalb nur Kubernetes-Objekte und Datenbankobjekte über das PostgreSQL-Protokoll; dafür genügen Storage-Key, Passphrase und die Foundation-Ausgaben.

Verworfen: OIDC mit Managed Identity (braucht Owner für die Rollenzuweisung), Service Principal mit Secret (braucht App-Registrierung und Rollenzuweisung), Benutzer-Token in CI (läuft ab, MFA).

### 2. Alle Azure-Ressourcen im Foundation-Stack

Aus Entscheidung 1 folgt die Besitzregel: Was Azure Resource Manager braucht, gehört zur Foundation. App-spezifische Verfügbarkeitstests und Alarmregeln erzeugt der Foundation-Stack aus `platform/catalog/*/app.yaml`. Eine neue App braucht deshalb einen Foundation-Lauf für ihr Monitoring, aber keinen für ihr Deployment.

### 3. Schlüssel statt Rollen

| Bedarf | Lösung | Statt |
| --- | --- | --- |
| Image-Pull in AKS | Image-Pull-Secret mit ACR-Admin-User | `AcrPull` für die Kubelet-Identität |
| Image-Push aus CI | ACR-Admin-User | `AcrPush` per OIDC |
| Cluster-Zugriff aus CI | Admin-Kubeconfig in den Ausgaben, lokale Accounts aktiv | Entra-Integration mit Azure RBAC |
| Diagnose-Storage | Storage-Key | `Storage Blob Data Contributor` |
| GitHub-Zugriff | GitHub App `hackathonplatformagent` mit privatem Schlüssel aus den Foundation-Ausgaben; ersatzweise das geschützte Repo-Secret `OPS_GITHUB_APP_PRIVATE_KEY`, das B's Workflows nutzen, solange die Ausgabe leer ist (geändert 2026-10-30 (Erik); war: Fine-grained PAT; Rückfall ergänzt 31.10.2026) | Fine-grained PAT eines Teammitglieds |

Der AKS-Cluster nutzt eine system-assigned Identity und das Standardnetz im Node-Resource-Group, damit beim Anlegen keine Rollenzuweisung nötig ist. Der Ingress-Controller bekommt seine öffentliche IP über den LoadBalancer-Service im Cluster statt über eine vorab angelegte IP in einer anderen Resource Group.

### 4. sslip.io statt eigener Domain

Es gibt keine nutzbare Domain. Der Foundation-Stack liest nach dem Ausrollen des Ingress-Controllers dessen Service-IP und gibt `baseDomain = <ip-mit-bindestrichen>.sslip.io` aus. Damit funktionieren Host-basiertes Routing und Let's Encrypt ohne DNS-Zone. AFFiNE braucht einen eigenen Host am Root-Pfad; Pfad-Routing unter einer gemeinsamen Adresse wurde deshalb verworfen.

### 5. Ein gemeinsamer PostgreSQL-Server

Ein Flexible Server (Burstable) trägt die Datenbank `operations` und darf App-Datenbanken aufnehmen. Ein eigener Server pro App kostet Zeit und Geld; In-Cluster-Postgres für den Agenten widerspricht der Anforderung, Agentenzustand außerhalb des Clusters zu halten.

### 6. Agentenausfall über `/healthz`

Statt eines Log-Search-Alarms auf Container-Logs prüft ein Application-Insights-Standardtest `/healthz`. C lässt den Endpunkt 503 liefern, wenn der Worker-Heartbeat in PostgreSQL älter als zehn Minuten ist. So hängt die Ausfallmeldung nicht an Container Insights, dessen Einrichtung ohne Rollenzuweisung unsicher ist.

### 7. Keine Sperre in der Foundation

Der Foundation-Stack erwirbt keine Operations-Sperre. Sonst könnte er eine ausgefallene Agentendatenbank nicht reparieren, weil die Sperre in genau dieser Datenbank liegt. Gleichzeitige Foundation-Läufe verhindert das Locking des Pulumi-Backends.

### 8. Opt-in: Leseidentität für den Agent-Workspace (D14)

Ausnahme von Entscheidung 3 und den Hackathon-Entscheidungen E3 und E6: Der Betriebsagent soll in seinem Workspace-Pod mit `az` Azure-Ressourcen, Metriken und Logs lesen (P1-07). Hinter dem Schalter `agentWorkspaceIdentity` (Standard `false`) legt der Foundation-Stack eine User-assigned Identity `id-mmk-ops-workspace-<env>`, eine Federated Credential für den ServiceAccount `operations-agent-workspace` und drei Rollenzuweisungen an: `Reader` auf die Plattform- und die Node-Resource-Group, `Log Analytics Reader` auf den Workspace. Der AKS-Workload-Identity-Webhook bleibt aus; der Agent projiziert das Token selbst, der Cluster behält nur seinen OIDC-Issuer. `Reader` kann keine Schlüssel auflisten. Die Rollenzuweisungen verlangen Owner oder User Access Administrator; wer nur Contributor hat, lässt den Schalter aus. Die Werte `clientId`, `tenantId` und `subscriptionId` gibt die Ausgabe `agentWorkspaceAzure` aus; sie liegt außerhalb des Ausgabevertrags (kein Schema-Eintrag, `contractVersion` bleibt 1) und fließt über den Sollzustand der App (`workspace.azure.*`) in den Agenten. Stand 31.10.2026: Identität und Rollen wurden am 30.10. mit der Azure CLI angelegt (Tag `managed-by=az-cli-pending-pulumi`), der Code ist gemergt, die Übernahme in den Pulumi-State steht aus (Ablauf in `operations.md`). Status der Entscheidung: vorgeschlagen (decisions.md, dort doppelt als D14 nummeriert). Verworfen: AKS-Workload-Identity-Add-on (ändert den Cluster ohne Nutzen), Service Principal mit Secret (Entra-App-Registrierung, rotierendes Secret).

(geändert 31.10.2026; ergänzt)

## Risks / Trade-offs

- [Container Insights braucht doch eine Rollenzuweisung] → Logs sind nur für Menschen gedacht; Agent und Ausfallmeldung hängen nicht davon ab. Früh in Task 3 prüfen. Stand 31.10.2026: Eine Testzeile kam ohne Rollenzuweisung in Log Analytics an (Task 3.2).
- [Ein Alarm- oder Testtyp verlangt eine Managed Identity mit Rolle] → Standardtests und Metrik-Alarme ohne Identität verwenden; bei Problemen auf reine Verfügbarkeitstests beschränken.
- [Let's Encrypt-Ratenlimits für sslip.io] → HTTP über dieselben Hosts als dokumentierter Fallback; Action Group und GitHub-Webhook akzeptieren HTTP für Staging.
- [Ingress-IP ändert sich beim Ersetzen des Controllers] → `baseDomain` ändert sich mit; App-Stacks und Verfügbarkeitstests werden danach neu ausgerollt. Den Controller nicht ohne Not ersetzen.
- [Breite Schlüssel liegen im State] → Akzeptiertes Staging-Risiko; State-Zugriff nur über Repo-Secrets.
- [Wiederherstellung hängt an einer Person mit lokalem Zugang] → Verfahren dokumentieren und mindestens zwei Personen mit Contributor durchspielen lassen.
- [Privater Schlüssel der GitHub App wird bekannt oder geht verloren] → Neuen Schlüssel in den App-Einstellungen erzeugen, über `foundation.sh <env-file> secrets` hinterlegen, ausrollen und den alten Schlüssel in der App löschen. Ein optionaler PAT im Repo-Secret `OPS_GITHUB_TOKEN` läuft weiterhin ab; sein Ablaufdatum wird dokumentiert. Geändert 2026-10-30 (Erik); war: [PAT läuft ab oder Person verlässt das Team].

## Migration Plan

1. Rechte-Vorabcheck, Bootstrap-Skript, Repo-Secrets.
2. Konventionen und Ausgabe-Schema v0 mit Platzhalterwerten veröffentlichen, damit B, C und D früh dagegen bauen.
3. Foundation-Stack lokal ausrollen, Zugangscheck in CI grün.
4. Nach den ersten Katalogeinträgen von B erneut ausrollen, um Verfügbarkeitstests anzulegen.
5. Rücknahme: Ein fehlerhafter Foundation-Lauf wird durch erneutes Ausrollen eines früheren Commits korrigiert; State-Fehler über Blob-Versionierung.

## Open Questions

- Region und Knotengröße werden beim Bootstrap nach Verfügbarkeit und Budget gewählt und in der Foundation-Konfiguration festgehalten. Beantwortet 31.10.2026: `westeurope`, Kubernetes 1.35.8, ein Knoten `Standard_D2as_v6` (`Pulumi.staging.yaml`). `germanywestcentral` (Region gesperrt) und `swedencentral` (`Standard_B1ms` nicht verfügbar) schieden für PostgreSQL Flexible Server aus; das State-Backend liegt weiter in `germanywestcentral`.
- Der Ingress-Controller wird beim Implementieren nach aktuellem Pflegestatus gewählt und dokumentiert. Beantwortet 31.10.2026: Traefik (ingress-nginx ist eingestellt), Zertifikate über cert-manager mit HTTP-01; Begründung in decisions.md (D1, D2).
