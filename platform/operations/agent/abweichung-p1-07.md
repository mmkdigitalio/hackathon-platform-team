# Abweichung von P1-07: keine Azure-Diagnose durch den Agenten

**Status:** gilt für Phase 1. Review mit Strang A ausstehend.

## Vorgabe

P1-07 und das Produktdokument (Abschnitt 6) sehen vor, dass der Agent Azure Monitor abfragt: Messwerte, Verfügbarkeitstests und Container-Logs in Log Analytics.

## Warum das in Phase 1 nicht geht

Das Team hat auf der Subscription nur Contributor-Rechte ohne Rollenzuweisungen und ohne Entra-App-Registrierungen (E3). Damit lässt sich dem Agenten keine Identität mit Leserechten auf Azure Resource Manager, Messwerte oder Log Analytics geben (E6). Workload Identity fällt aus demselben Grund weg.

## Ersatz

| Ursprüngliche Quelle | Ersatz im Agenten |
| --- | --- |
| Alarm und Messwert aus Azure Monitor | Alarm-Payload im Auftrag (Common Alert Schema): Regel, Zustand, Zeitpunkt, Bedingung, Testergebnis (`tools/alert.py`) |
| Verfügbarkeitstest-Ergebnisse | eigene HTTP-Prüfung `http_check` gegen `<app-id>.<baseDomain><Prüfpfad>` |
| Container-Logs in Log Analytics | begrenzte Pod-Logs über die Kubernetes-API (`get_logs`) |
| Ressourcenzustand | Kubernetes-Zustand und Events (`get_workload_state`, `get_events`) |
| Verifikationsmesswerte | Smoke-Test-Job und aktueller Kubernetes-Zustand |

Werkzeuge für Azure Resource Manager, Azure-Messwerte oder Log Analytics existieren im Code nicht. Der Agent startet ohne Azure-Zugangsdaten, und kein Diagnoseschritt hängt von Azure-Rechten ab.

## Folgen

- Keine Verlaufsdaten aus Azure; der Agent sieht nur, was Alarm-Payload, Cluster und eigene Prüfungen jetzt zeigen. Für die beiden Phase-1-Fälle (0 Replicas, ungültige Konfiguration) reicht das.
- Container-Logs in Log Analytics bleiben Menschen vorbehalten.
- Wenn spätere Phasen Rollenzuweisungen erlauben, kommt ein eigenes lesendes Azure-Werkzeug mit eigener Identität hinzu; Betriebsregeln und diese Datei werden dann angepasst.
