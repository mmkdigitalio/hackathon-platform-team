# Prozesse, Lease und Wiederanlauf

## Prozesse

Ein Image (D), drei Einstiegspunkte aus demselben Paket:

| Prozess | Befehl | Aufgabe |
| --- | --- | --- |
| API | `ops-api` (`python -m api`), Port 8080 | Eingang, `/healthz`, `/runs` |
| Worker | `ops-worker` (`python -m worker`) | Auftragsübernahme, Lease, Heartbeat, Aufruf von D's Ausführung |
| Migration | `ops migrate` | Helm-Hook-Job `pre-install,pre-upgrade` und `ops-migrate.yml` |

API und Worker prüfen beim Start nur die Schema-Version. Bei Abweichung beenden sie sich mit Exit-Code 3 und der Meldung `Schema-Version passt nicht: erwartet N, vorhanden M` bzw. bei fehlendem Schema mit Verweis auf `ops-migrate.yml`. Sie migrieren nie. Geprüft werden auch die Checkpoint-Tabellen von D. Der Worker beendet sich mit Exit-Code 4, wenn `OPS_AGENT_EXECUTOR` (Standard `agent:execute`) nicht ladbar ist. Mit `OPS_AGENT_EXECUTOR=worker.placeholder:execute` übernimmt er Aufträge und beendet sie sichtbar in `needs_attention`, ohne Modell und ohne Änderung.

In Staging läuft genau **ein** Worker-Replica (Chart von D). Mehrere Worker sind durch das Lease-Verfahren abgesichert und lokal getestet (`tests/state/test_worker_and_states.py`), werden in Staging aber nicht betrieben.

## Migrationen

- Migrationen liegen als `operations-agent/src/persistence/migrations/NNNN_name.sql`, je eine Transaktion, Prüfsumme in `schema_migrations`.
- `ops migrate` holt eine Advisory-Lock per `pg_try_advisory_lock` (Abfrage alle 0,5 s, höchstens 10 min), spielt fehlende Versionen ein und endet auf aktuellem Schema mit „keine Änderung“. Eine nachträglich veränderte Migration oder eine neuere Datenbankversion bricht ab. Blockierendes `pg_advisory_lock` wäre ein Deadlock: Der wartende Prozess hielte eine Transaktion offen, auf die `CREATE INDEX CONCURRENTLY` in D's Checkpoint-Migrationen wartet.
- Im selben Lauf legt `ops migrate` die Checkpoint-Tabellen der gepinnten LangGraph-Version an (`agent.persistence.sdk_migrations`, Strang D); D ruft `PostgresSaver.setup()` nie auf.
- Änderungen nur kompatibel und nur als neue Migration: Der Hook-Job läuft vor den neuen Pods, die alten laufen kurz gegen das neue Schema. Ein Code-Rollback setzt Schema-Kompatibilität voraus; Aufträge und Aktionen werden nie gelöscht.
- `ops schema-version` zeigt erwartete und vorhandene Version (Exit-Code 3 bei Abweichung).

## Zustände

```text
received -> queued -> investigating -> acting -> verifying -> resolved
                          investigating -> verifying          (ohne Maßnahme)
                          verifying -> investigating          (erneuter begrenzter Versuch)
received|queued|investigating|acting|verifying -> failed | needs_attention
```

Jeder beantragte Übergang landet in `job_transitions`, abgelehnte mit `accepted = false` und Grund. `resolved` verlangt:

1. einen positiven Smoke-Test (`verifications.kind = 'smoke-test'`) für App und Umgebung des Auftrags, begonnen **nach** der letzten ändernden Aktion des Auftrags (ohne Aktion: nach Beginn der Untersuchung),
2. einen Kubernetes-Zustand (`kind = 'k8s-state'`, bereit) des Ziels, erfasst nach diesem Zeitpunkt,
3. Übereinstimmung beider mit dem erwarteten Release (`expected_release`: Image-Digest und Chart-Version, Git-SHA falls angegeben).

Lehnt die Prüfung den Ergebnisvorschlag `resolved` ab, endet der Auftrag in `needs_attention` mit „Abschluss abgelehnt: …“.

## Lease

- Übernahme: `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)` auf Aufträge in `queued`, `investigating`, `acting`, `verifying` ohne gültigen Lease. Dabei steigen `lease_version` und `attempts`.
- Erneuerung alle 30 s auf 120 s, gegen die Datenbankzeit, in einem eigenen Thread. Gleichzeitig erneuert er die Ziel-Sperre des Auftrags.
- Jeder Schreibzugriff (Übergang, Aktion, Verifikation, Werkzeugaufruf, Befund) prüft `lease_owner`, `lease_version` und `lease_expires_at > now()` in derselben Transaktion. Ein veralteter Besitzer bekommt `LeaseLost` und schreibt nichts mehr.
- Nach mehr als 3 Übernahmen endet der Auftrag in `needs_attention` („Wiederholungslimit“).

## Heartbeat und /healthz (E10)

Der Worker schreibt alle 30 s `worker_heartbeats(worker_id, beat_at = now(), current_job_id)`, in einem eigenen Thread, auch während eines langen Auftrags. `GET /healthz` antwortet:

| Bedingung | Status |
| --- | --- |
| Datenbank nicht erreichbar | 503 `{"database": "unreachable"}` |
| kein Heartbeat oder jüngster älter als 10 Minuten | 503 `{"worker": "…"}` |
| sonst | 200 |

A's Verfügbarkeitstest ruft `/healthz` alle fünf Minuten auf und alarmiert `ops-humans`.

## Start

```bash
ops migrate                     # bzw. Helm-Hook / ops-migrate.yml
ops-api &                       # Port 8080
ops-worker
```

## Kontrolliertes Beenden

SIGTERM/SIGINT am Worker: Er übernimmt keinen neuen Auftrag mehr, stoppt den Heartbeat-Thread und beendet sich nach dem laufenden Auftrag. Endet der Pod vorher (Grace-Period abgelaufen, Absturz), bleibt der Auftrag mit seinem Zustand stehen; sein Lease läuft nach höchstens 120 s ab.

## Wiederanlauf

Ein Worker übernimmt den Auftrag mit abgelaufenem Lease in dessen gespeichertem Zustand, nicht in `queued`:

1. C übergibt dieselbe `operationId`, dieselbe `thread_id` (= Auftrags-ID, damit D's Checkpoint fortgesetzt wird) und die offenen Aktionen (`planned`, `started`, `unclear`) in `resume.open_actions`, dazu den neuen Besitz (`ownership.lease_version`).
2. D gleicht sie gegen das externe System ab (`platform/operations/agent/massnahmen-und-abgleich.md`): Ein bekannter Workflow-Lauf wird über `runId` bzw. die `actionId` im `run-name` weiterverfolgt, kein zweiter gestartet; ein Neustart wird an der Annotation erkannt.
3. Nicht aufklärbar: D setzt die Aktion auf `unclear` und schlägt `needs_attention` vor. C vergibt die Ziel-Sperre einer solchen Operation nach Ablauf nicht neu (siehe [locks.md](locks.md#konfliktregel)).

Nachvollziehen eines gespeicherten Auftrags nach Neustart:

```sql
SELECT id, state, lease_owner, lease_version, lease_expires_at, attempts FROM jobs WHERE id = '<auftragsId>';
SELECT id, kind, status, external_refs FROM actions WHERE job_id = '<auftragsId>';
```

Lokaler Ablauf zum Nachstellen: Worker mit `OPS_LEASE_SECONDS=10` starten, während eines Auftrags hart beenden (`kill -9`), Worker neu starten; nach Ablauf von 10 s erscheint in `jobs` eine höhere `lease_version`, und `executions.resumed` ist `true`.

## Störungen

| Symptom | Vorgehen |
| --- | --- |
| `/healthz` 503 `unreachable` | PostgreSQL bzw. Netzwerk prüfen (A); Eingang antwortet derweil 503, Quellen stellen erneut zu. |
| `/healthz` 503 Heartbeat | Worker-Pod-Log prüfen; Startabbruch wegen Schema-Version → Hook-Job prüfen; Exit-Code 4 → Ausführung nicht ladbar (D). |
| Auftrag hängt in aktivem Zustand | `lease_expires_at` prüfen; nach Ablauf übernimmt der Worker ihn wieder. |
| `needs_attention` „Klärung erforderlich“ | Externen Vorgang manuell prüfen, dann `ops actions resolve` (siehe [locks.md](locks.md)). |
