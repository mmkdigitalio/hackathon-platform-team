# Playbook: Selbstheilung von AFFiNE

Durchlauf des reparierbaren Fehlerszenarios (Szenario 4) von Anfang bis Ende mit einem Befehl: AFFiNE in `staging` stoppen, dann nacheinander prüfen, dass der Verfügbarkeitsalarm feuert, der Betriebsagent den Dienst wieder startet und Alarm und Auftrag sauber abgeschlossen werden. Skript: [playbook-self-heal.sh](playbook-self-heal.sh). Den Stopp übernimmt [fault-scale-to-zero.sh](fault-scale-to-zero.sh); das Playbook greift nur beobachtend ein.

```text
playbook-self-heal.sh run
  └─ fault-scale-to-zero.sh trigger ─> affine-staging/affine: 0 Replicas
       └─ /info ≠ 200 ─> webtest-affine-staging-availability scheitert (2 von 5 Standorten)
            └─ alert-affine-staging-availability Fired ─> ops-intake ─> Auftrag beim Agenten
                 └─ Agent: Soll-/Istvergleich scaled_to_zero ─> redeploy_desired_state
                      └─ app-deploy.yml (run-name op=op-…) ─> Replicas aus dem Sollzustand, Smoke-Test grün
                           └─ /info 200 ─> Alarm Resolved, Auftrag resolved
```

Bei 0 Replicas stellt der Agent laut [Betriebsregeln](../../agent/betriebsregeln.md) den Sollzustand mit `app-deploy.yml` wieder her. Ein Neustart (`restart_workload`, Annotation `ops.mmk/action-id`) ist für hängende Pods gedacht und bringt ein Deployment mit 0 Replicas nicht zurück; das Playbook erkennt ihn trotzdem und meldet ihn als „Agent: Neustart“.

## Voraussetzungen

| Was | Prüfen bzw. einrichten |
| --- | --- |
| Admin-Kubeconfig des Staging-Clusters | `az aks get-credentials --admin -g rg-mmk-platform-staging -n aks-mmk-platform-staging --file ~/.kube/mmk-staging-admin`, dann `export KUBECONFIG=~/.kube/mmk-staging-admin` |
| GitHub-CLI | `gh auth status`; Aufruf aus dem Repository, damit `gh` das Repo kennt |
| Azure-CLI | `az login`; Leserechte auf die Subscription aus `platform/scripts/bootstrap/staging.env` (andere mit `AZURE_SUBSCRIPTION_ID`) |
| Werkzeuge | `kubectl`, `gh`, `az`, `jq`, `curl` |
| Optional: Auftragsstatus | `psql` und `OPS_DATABASE_URL` oder `PG*`-Variablen für die Datenbank `operations` (Foundation-Ausgaben); ohne sie entfällt die letzte Stufe |
| Ankündigung | Laut [Betrieb des App-Pfads](../README.md#stoppen-und-wieder-starten) den Stopp im Team ankündigen: Der Alarm feuert absichtlich |

Den Ausgangszustand prüft das Skript selbst (siehe Stufe 0).

## Durchführung

Aus dem Wurzelverzeichnis des Repositorys:

```bash
# 1. Nur Vorprüfung, ändert nichts
platform/operations/apps/affine/playbook-self-heal.sh check --environment staging

# 2. Durchlauf: stoppt AFFiNE und verfolgt alle Stufen (25 bis 45 Minuten)
platform/operations/apps/affine/playbook-self-heal.sh run --environment staging \
  --report playbook-affine-staging-$(date -u +%Y%m%d%H%M).json
```

| Option | Standard | Bedeutung |
| --- | --- | --- |
| `--scheme https\|http` | aus dem Ingress (TLS vorhanden → `https`) | bei [HTTP-Fallback](../../foundation/monitoring.md#http-fallback) `http` |
| `--poll-seconds <n>` | `30` | Abstand der Abfragen |
| `--report <datei>` | keiner | JSON mit Ergebnis (`passed`, `failed`, `aborted`, `checked`) und je Stufe Zustand, Detail, Link, Beginn und Ende |
| `--html <verzeichnis>` | keiner | Live-Ansicht für den Browser (siehe [Demo](#demo)) |

Exit-Code 0: alle Stufen erfüllt. Exit-Code 1: Vorprüfung oder eine Stufe gescheitert bzw. Zeitgrenze erreicht; die Meldung nennt Stufe und nächsten Diagnoseschritt. Im Terminal erscheint jede Stufe beim Abschluss mit ✓ bzw. ✗ und Zeit ab Start; während des Wartens zeigt eine Statuszeile Stufe, Wartezeit und die letzte Beobachtung (HTTP-Status, Laufschritt von GitHub Actions, Replicas). Am Ende steht eine Zusammenfassung mit Zeitpunkt und Dauer je Stufe. Ohne Terminal (CI, Umleitung in eine Datei) entfallen Statuszeile und Farben; `NO_COLOR=1` schaltet nur die Farben ab.

## Demo

```bash
platform/operations/apps/affine/playbook-self-heal.sh check --environment staging        # vorher, ändert nichts
platform/operations/apps/affine/playbook-self-heal.sh run --environment staging \
  --html /tmp/selbstheilung --poll-seconds 15
```

`--html` kopiert [playbook-self-heal.html](playbook-self-heal.html) als `index.html` in das Verzeichnis, schreibt bei jeder Abfrage `status.js` neu und öffnet die Seite unter macOS im Standardbrowser. Die Seite braucht keinen Server und kein Netz; sie lädt `status.js` alle 2 Sekunden und zeigt:

- Gesamtuhr und Ergebnis (läuft, selbstgeheilt, gescheitert, abgebrochen),
- die aktuelle Stufe mit beteiligtem System (Playbook, AFFiNE, Azure Monitor, Betriebsagent, GitHub Actions, Kubernetes), Wartezeit und letzter Beobachtung,
- alle Stufen als Zeitleiste mit Detail, Zeit ab Start und Dauer sowie Links zum Alarm im Azure-Portal und zum Deploy-Lauf.

Hell- und Dunkelmodus folgen dem System. Schreibt das Skript länger als 3 Minuten nichts, markiert die Seite den Stand als veraltet. Für die Demo Terminal und Browser nebeneinander zeigen; die Wartezeit auf den Alarm (5–10 Minuten) eignet sich, um Agent, Betriebsregeln und Alarmweg zu erklären.

### Simulation (ohne staging)

```bash
platform/operations/apps/affine/playbook-self-heal.sh simulate --environment staging
```

Spielt denselben Ablauf in gut 2 Minuten mit simulierten Gegenstellen ab: [simulation/fake-cli.sh](simulation/fake-cli.sh) ersetzt über `simulation/bin` (Symlinks) `kubectl`, `gh`, `az`, `curl` und `psql`; `KUBECONFIG=/dev/null`, kein Datenbank- und kein Azure-Zugriff. Es ist dasselbe Skript mit denselben Stufen, Prüfungen und Ausgaben; Terminal und Live-Ansicht tragen den Hinweis „Simulation“, der Bericht `"simulation": true`, und Links zu Portal und Lauf entfallen. Ohne `--html` schreibt `simulate` die Live-Ansicht nach `$TMPDIR/selbstheilung-simulation`; Abfrage alle 2 Sekunden.

| Sekunde nach dem Stopp | Simuliertes Ereignis |
| --- | --- |
| 0 | AFFiNE mit 0 Replicas, `/info` nicht erreichbar |
| 25 | Alarm Fired |
| 40 | Agent startet `app-deploy.yml` (Schritte Foundation-Ausgaben, Sperre erwerben, Ausrollen, Rollout prüfen, Smoke-Test) |
| 100 | Lauf grün, AFFiNE wieder bereit |
| 125 | Alarm Resolved |
| 130 | Auftrag `resolved` |

Gedacht als Probe und als Rückfallebene, wenn staging in der Demo nicht bereit ist; nicht als Nachweis, dass die Selbstheilung in staging funktioniert.

## Stufen

| # | Stufe | Erfüllt, wenn | Zeitgrenze | Erwartete Dauer |
| --- | --- | --- | --- | --- |
| 0 | Vorprüfung | Deployment hat mindestens 1 bereite Replica, `/info` und Agent-`/healthz` liefern 200, Alarm `alert-affine-staging-availability` nicht aktiv, kein `app-deploy.yml` für `affine/staging` in Warteschlange oder Ausführung | – | Sekunden |
| 1 | Stopp ausgelöst | `fault-scale-to-zero.sh trigger` hat `spec.replicas` auf 0 gesetzt | – | Sekunden |
| 2 | App nicht erreichbar | `/info` antwortet nicht mehr mit 200 | 5 min | Sekunden |
| 3 | Alarm Fired | Neue Alarminstanz der Regel mit `monitorCondition: Fired`, Start nach dem Stopp | 20 min | 5–10 min |
| 4 | Agent: Redeploy | `app-deploy.yml`-Lauf für `affine/staging` mit `op=op-…` (operationId des Agenten) nach dem Stopp; alternativ geänderte Annotation `ops.mmk/action-id` (Neustart) | 15 min | 1–3 min |
| 5 | Deploy-Lauf grün | Lauf `completed` mit `success`, also Rollout und Smoke-Test erfolgreich | 30 min | 5–15 min, bei belegter Sperre bis zu 15 min länger |
| 6 | App erreichbar | Mindestens 1 bereite Replica und `/info` 200 | 10 min | Sekunden |
| 7 | Alarm Resolved | Dieselbe Alarminstanz hat `monitorCondition: Resolved` | 20 min | 5–10 min |
| 8 | Auftrag resolved | Auftrag zur Operation des Agenten in `resolved` (nur mit Datenbankzugang) | 15 min | kurz nach Stufe 5 |

Die erwarteten Dauern sind abgeleitet, nicht live gemessen: Stufe 3 und 7 aus den Alarmeinstellungen in [monitoring.md](../../foundation/monitoring.md#availability-tests-from-the-catalog) (Test alle 5 Minuten, Fenster 5 Minuten), Stufe 4 aus dem kind-Durchlauf in [testen.md](../../agent/testen.md#stufe-3-lokaler-cluster-mit-echtem-modell-ca-5-minuten-ca-010-usd-je-szenario). Nach dem ersten Staging-Durchlauf die gemessenen Werte aus dem Bericht hier eintragen.

Alarme liest das Skript über die Azure Alerts Management API (`az rest`, Zeitraum 1 Tag) und ordnet sie über den Regelnamen am Ende von `essentials.alertRule` zu. Für GitHub und Azure gilt eine Minute Spielraum für Uhrabweichungen: Läufe und Alarme zählen ab einer Minute vor dem Stopp.

## Wenn eine Stufe scheitert

Nach dem Stopp nennt jede Fehlermeldung den Rücksetzbefehl. AFFiNE ohne Agent wieder starten:

```bash
platform/operations/apps/affine/fault-scale-to-zero.sh reset --environment staging
```

Nicht zurücksetzen, solange ein `app-deploy.yml`-Lauf des Agenten noch läuft: Der manuelle Lauf würde nur auf dessen Sperre warten.

| Gescheitert in | Zuerst ansehen |
| --- | --- |
| 0, Agent nicht bereit | `/healthz`-Antwort (`worker`, `database`), [worker.md](../../state/worker.md); ohne bereiten Agenten kein Durchlauf |
| 0, Alarm aktiv | Auflösung abwarten oder AFFiNE erst reparieren; sonst gibt es kein neues Fired-Ereignis |
| 3 | Application Insights `appi-mmk-platform-staging` → Availability bzw. `AppAvailabilityResults` ([Alarm verfolgen](../../foundation/monitoring.md#tracing-an-alert)); fehlt der Test, wurde der Foundation-Stack nach dem Katalogeintrag nicht ausgerollt |
| 4 | Zustellung an `ops-intake` im Alarmverlauf; API-Log `kubectl -n operations-agent-staging logs deploy/operations-agent-api --since=30m` (Zeile `POST /events/azure-monitor?key=*** -> 202`); Worker-Log `deploy/operations-agent-worker`; Auftrag mit `ops runs show <auftragsId>` |
| 5 | `gh run view <lauf> --log-failed`; Zusammenfassung des Laufs (Sperre, Rollout, Smoke-Test) |
| 6, 7 | `kubectl -n affine-staging get deploy,pods`, Events; Resolved kommt erst, wenn der Test wieder an mindestens 4 Standorten erfolgreich ist |
| 8 | `ops runs show <auftragsId>`: abgelehnte Übergänge, Verifikationen, Befunde |

Mit Strg+C bricht das Skript ab, gibt die Zusammenfassung aus und schreibt den Bericht mit `aborted`. Der Agent arbeitet unabhängig davon weiter.

## Nachweise

1. Bericht aus `--report` und die Zusammenfassung.
2. Deploy-Lauf: URL aus Stufe 4; `run-name` enthält `op=<operationId>` und `action=<actionId>` des Agenten.
3. Auftrag: `ops runs show <auftragsId>` (ID aus Stufe 8) bzw. `curl -s -H "X-Api-Key: $MAINTENANCE_API_KEY" https://<operationsApiHost>/runs/<auftragsId>`. Erwartet: Soll-/Istvergleich `scaled_to_zero`, eine Aktion `workflow_dispatch`, positiver Smoke-Test, Zustand `resolved`.
4. Azure: Alarmverlauf der Regel mit Fired- und Resolved-Zeitpunkt.
5. Datensicherheit wie in [README.md](README.md#datensicherheit-nachweisen): Der Stopp ändert nur `spec.replicas`; Datenbank, PVC `affine-data` und Secrets bleiben unverändert.

## Grenzen

- Nur `staging`; andere Umgebungen brechen ohne Änderung ab.
- Nur AFFiNE: Namespace, Deployment und Stoppverfahren stammen aus `fault-scale-to-zero.sh`.
- Die Alarmabfrage liest nur die erste Ergebnisseite der API; bei sehr vielen Alarmen in der Subscription innerhalb eines Tages kann eine Instanz fehlen.
- Der Stopp per `kubectl scale` ist ein Testeingriff, kein Stoppverfahren für den Betrieb (siehe [Stoppen und wieder starten](../README.md#stoppen-und-wieder-starten)).
