# AFFiNE: Fehlerszenarien

Zwei kontrollierte, datensichere Fehler für die gemeinsame Abnahme (Szenarien 4 und 5). Beide sind nur für `staging` ausführbar und ändern weder Datenbank noch PVC noch Secrets. Anforderungen an das AFFiNE-Paket: [LIEFERANFORDERUNGEN.md](LIEFERANFORDERUNGEN.md).

| Fall | Auslösen | Erwartung | Rücksetzen |
| --- | --- | --- | --- |
| Reparierbar | `fault-scale-to-zero.sh trigger --environment staging` | Smoke-Test negativ, Alarm `alert-affine-staging-availability`, Agent startet `app-deploy.yml`, danach Smoke-Test grün und Alarm aufgelöst | `fault-scale-to-zero.sh reset --environment staging` (startet `app-deploy.yml`) |
| Nicht reparierbar | `fault-invalid-config.sh trigger --environment staging`, PR mergen | Rollout scheitert bzw. AFFiNE unerreichbar, Alarm, `restart`/`redeploy` wirkungslos, Agent endet mit `needs_attention` und nennt den Commit | `fault-invalid-config.sh revert --environment staging`, Revert-PR mergen |

Den reparierbaren Fall mit Alarm und Reparatur durch den Agenten von Anfang bis Ende verfolgt [playbook-self-heal.md](playbook-self-heal.md) (`playbook-self-heal.sh run --environment staging`).

## Vor jedem Durchlauf

1. Ausgangszustand: `app-deploy.yml` für AFFiNE grün, Smoke-Test hat Testdokument und Anhang angelegt (Ergebnis-Artefakt sichern).
2. `KUBECONFIG` auf den Staging-Cluster (Admin-Kubeconfig aus den Foundation-Ausgaben), `gh` angemeldet.
3. Agent läuft, wenn die autonome Reparatur Teil des Durchlaufs ist; sonst Rücksetzen manuell.

## Reparierbarer Fall: 0 Replicas

```bash
platform/operations/apps/affine/fault-scale-to-zero.sh trigger --environment staging
```

Das Skript setzt nur `spec.replicas` des Deployments `affine` in `affine-staging` auf 0 und gibt die erwartete Wirkung aus. Der nächste Lauf von `app-deploy.yml` setzt die Replica-Zahl über das erzwungene Helm-Upgrade (Chart-Wert `rolloutMarker`) auf den Sollzustand zurück, ohne dass sich die Sollzustandsdatei ändert.

Prüfen: Alarm im Azure-Portal, Fired-Ereignis für `affine`/`staging` beim Agenten, Deploy-Lauf mit der `operationId` des Agenten im `run-name`, grüner Smoke-Test, aufgelöster Alarm.

## Nicht reparierbarer Fall: ungültiger Konfigurationswert

Pfad und Wert legt die AFFiNE-Lieferung in [fault-invalid-config.yaml](fault-invalid-config.yaml) fest; bis dahin bricht das Skript ab.

```bash
platform/operations/apps/affine/fault-invalid-config.sh trigger --environment staging
```

Das Skript legt einen Branch mit genau einem Commit an, der nur `values` in `platform/environments/staging/affine.yaml` ändert, validiert ihn und öffnet einen PR. Commit-Betreff: `fault(affine/staging): ungültiger Konfigurationswert`. Nach dem Merge rollt `app-deploy.yml` den Fehler aus; der Lauf scheitert im Rollout oder im Smoke-Test.

Erwartung an den Agenten: `restart` und `redeploy` lassen den Smoke-Test negativ; der auslösende Commit ist über `git log -- platform/environments/staging/affine.yaml` ermittelbar, das Ergebnis ist `needs_attention` mit diesem Commit.

Rücksetzen:

```bash
platform/operations/apps/affine/fault-invalid-config.sh revert --environment staging
```

Nach dem Merge des Revert-PRs rollt `app-deploy.yml` den vorherigen Wert aus; der Smoke-Test ist wieder grün.

## Datensicherheit nachweisen

1. Mit anderer Umgebung als `staging` aufrufen; beide Skripte brechen ohne Änderung ab:
   ```bash
   platform/operations/apps/affine/fault-scale-to-zero.sh trigger --environment production   # Exit 1
   ```
2. Nach beiden Szenarien `app-deploy.yml` für AFFiNE: Der Smoke-Test findet das vor den Szenarien angelegte Testdokument und den Anhang wieder (`evidence`).
3. Unverändert geblieben: Datenbank und Login `affine`, PVC `affine-data`, Secret `affine-platform` (Pulumi-Preview ohne Änderungen an diesen Ressourcen).
