# Abweichung von P1-07: keine Azure-Diagnose durch die typisierten Werkzeuge

**Status (geändert 31.10.2026):** Die Abweichung gilt für die typisierten Werkzeuge (sie kennen kein Azure) und für Läufe ohne Workspace-Identität. Mit der Workspace-Identität (Task 9.5) liest die Workspace-Shell Azure: `az-login` meldet sich mit dem projizierten Federated Token an, ohne AKS-Webhook. Review mit Strang A ausstehend.

**Nachtrag 30.10.2026, Stand 31.10.2026:** Der Agent bekommt einen Workspace-Pod mit `az` (siehe `paket-betrieb.md`). In Staging existiert die Identität `id-mmk-ops-workspace-staging` (per az CLI angelegt, Federated Credential auf `system:serviceaccount:operations-agent-staging:operations-agent-workspace`) mit den Rollen Reader auf `rg-mmk-platform-staging` und `rg-mmk-platform-staging-aks-nodes` sowie Log Analytics Reader auf `log-mmk-platform-staging`; der Sollzustand setzt `workspace.azure.*`. Die Foundation-Konfiguration `agentWorkspaceIdentity` (Entscheidung D14) bildet sie ab, die Übernahme in den Pulumi-State steht aus (`platform/operations/foundation/operations.md`, „Agent workspace identity“). Mit der Identität liest der Agent Azure Resource Manager, Messwerte und Log Analytics über die Shell; ohne sie (kein `workspace.azure.clientId`, kein Workspace) gilt der Ersatz unten weiter. Ändernde Azure-Aufrufe sind nicht vorgesehen: die Identität erhält nur die Rollen Reader und Log Analytics Reader. Ein `az-login` und eine Log-Analytics-Abfrage in einem Staging-Pod sind noch nicht nachgewiesen.

## Vorgabe

P1-07 und das Produktdokument (Abschnitt 6) sehen vor, dass der Agent Azure Monitor abfragt: Messwerte, Verfügbarkeitstests und Container-Logs in Log Analytics.

## Warum die typisierten Werkzeuge kein Azure kennen

Das Team hat auf der Subscription nur Contributor-Rechte ohne Rollenzuweisungen und ohne Entra-App-Registrierungen (E3). Damit ließ sich dem Agenten zunächst keine Identität mit Leserechten auf Azure Resource Manager, Messwerte oder Log Analytics geben (E6). Die Workspace-Identität (siehe Nachtrag) umgeht das nur für die Shell; die typisierten Werkzeuge bleiben bei Kubernetes, HTTP und Alarm-Payload. Der AKS-Workload-Identity-Webhook fehlt; der Worker projiziert das Token selbst (`src/tools/workspace.py`).

## Ersatz

| Ursprüngliche Quelle | Ersatz im Agenten |
| --- | --- |
| Alarm und Messwert aus Azure Monitor | Alarm-Payload im Auftrag (Common Alert Schema): Regel, Zustand, Zeitpunkt, Bedingung, Testergebnis (`tools/alert.py`) |
| Verfügbarkeitstest-Ergebnisse | eigene HTTP-Prüfung `http_check` gegen `<app-id>.<baseDomain><Prüfpfad>` |
| Container-Logs in Log Analytics | begrenzte Pod-Logs über die Kubernetes-API (`get_logs`) |
| Ressourcenzustand | Kubernetes-Zustand und Events (`get_workload_state`, `get_events`) |
| Verifikationsmesswerte | Smoke-Test-Job und aktueller Kubernetes-Zustand |

Typisierte Werkzeuge für Azure Resource Manager, Azure-Messwerte oder Log Analytics existieren im Code nicht. Ohne `workspace.azure.clientId` startet der Agent ohne Azure-Zugangsdaten, und kein Diagnoseschritt hängt von Azure-Rechten ab (geändert 31.10.2026: mit Identität gibt es Azure-Lesezugriff nur über `execute` im Workspace).

## Folgen

- Ohne Workspace-Identität keine Verlaufsdaten aus Azure; der Agent sieht nur, was Alarm-Payload, Cluster und eigene Prüfungen jetzt zeigen. Für die beiden Phase-1-Fälle (0 Replicas, ungültige Konfiguration) reicht das.
- Container-Logs in Log Analytics sind ohne Workspace-Identität Menschen vorbehalten; mit ihr kann der Agent sie über `az monitor log-analytics query` lesen (geändert 31.10.2026).
- Ein eigenes typisiertes, lesendes Azure-Werkzeug entsteht erst, wenn die Shell-Lösung nicht reicht; Betriebsregeln und diese Datei werden dann angepasst.
