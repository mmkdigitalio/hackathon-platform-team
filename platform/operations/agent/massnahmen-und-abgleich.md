# Maßnahmen, Grenzen, Sperre, Abgleich und Verifikation

Code: `operations-agent/src/agent/` (Ablauf, Grenzen, Ergebnis) und `operations-agent/src/tools/` (Neustart, Dispatch, Smoke-Test).

## Ablauf eines Auftrags

1. C's Worker übergibt `AgentInvocation` (Vertrag v0.1 in `operations-agent/src/contracts/`) und die Dienste für Aktionen, Sperre, Zustandsübergänge, Besitz und Checkpoints an `agent.execute`.
2. Der Agent lädt den Kontext zum Git-Stand des Auftrags: Katalog `platform/catalog/<app>/app.yaml`, Sollzustand `platform/environments/<env>/<app>.yaml`, Runbook in der im Katalog genannten Commit-Version. Getrennt davon liest er den Istzustand aus dem Cluster und die Release-Annotationen am Namespace.
3. Offene Aktionen der Operation werden abgeglichen (siehe unten). Ist der Auftrag danach belegt gelöst, entfällt der Modelllauf.
4. Der Deep Agent untersucht und handelt nur über die Werkzeuge.
5. Fehlt nach der letzten Maßnahme ein Nachweis, führt der Code den Smoke-Test selbst aus.
6. Der Code bildet den Ergebnisvorschlag aus den Nachweisen. C prüft und persistiert jeden Zustandsübergang; der Agent schreibt keinen Auftragszustand.

## Grenzen

Startwerte, je Auftrag über `RunLimits` von C und ohne Codeänderung über `OPS_LIMIT_*` bzw. Chart-Werte `limits.*` anpassbar:

| Grenze | Start | Erzwungen in | Wirkung |
| --- | --- | --- | --- |
| Laufzeit | 600 s | `RunBudget.check_runtime` vor jedem Modell- und Werkzeugaufruf; Warte-Schleifen enden vor der Frist | `needs_attention`, `limit_reached: runtime` |
| Tool-Aufrufe | 30 | `BudgetMiddleware.wrap_tool_call` | der 31. Aufruf wird nicht ausgeführt |
| Reparaturversuche | 2 | `RunBudget.begin_repair` vor jeder ändernden Maßnahme; frühere Maßnahmen derselben Operation zählen mit | keine dritte ändernde Maßnahme |
| Modellkosten | 1,00 USD | nach jedem Modellaufruf aus `usage_metadata` und konfigurierten Preisen | kein weiterer Modellaufruf |

Ein Reparaturversuch ist eine ändernde Maßnahme (Neustart oder Dispatch) mit anschließender Verifikation. Bei erreichter Grenze enthält das Ergebnis die Grenze, alle Befunde und die Aktionsreferenzen. Liegt trotz Grenze ein gültiger positiver Nachweis vor, lautet der Vorschlag `resolved`.

## Bekannte Reparatur: AFFiNE auf 0 Replicas

1. `compare_desired_actual` meldet `scaled_to_zero`: das Deployment hat 0 Replicas, der Sollzustand mehr (oder keinen Wert, dann gilt der Chart-Standard ≥ 1).
2. `redeploy_desired_state` prüft Workflow-Liste, Katalogaktion `redeploy`, Abweichung und Sperre, legt die Aktion an und startet `app-deploy.yml` mit `appId`, `environment`, `operationId`, `actionId`, `force=false`.
3. Der Lauf wird über die `actionId` im `run-name` zugeordnet und bis zum echten Abschluss verfolgt. Ein laufender Workflow ist kein Erfolg.
4. `run_smoke_test` startet den Smoke-Test-Job und erfasst danach den Kubernetes-Zustand.
5. `resolved` nur bei positivem Test nach der Maßnahme, abgeschlossenem Rollout, mindestens einem bereiten Replica und dem Image-Digest des Sollzustands.

## Nicht reparierbarer Fall: ungültiger Konfigurationswert

Entspricht der Istzustand dem Sollzustand (`in_sync`), lehnt `redeploy_desired_state` ab: ein erneutes Deployment reproduziert denselben Zustand. Ein Neustart bleibt innerhalb der Grenzen erlaubt, wenn das Runbook ihn vorsieht. Bleibt die Verifikation negativ, ergänzt der Code:

- `references.desiredStateCommit`: SHA, Autor, Zeitpunkt, URL und Commit-Nachricht des letzten Commits auf die Sollzustandsdatei,
- `references.logLines`: verdächtige Logzeilen (error, invalid, fatal …),
- `required_change`: die nötige Korrektur am Sollzustand per Pull Request.

Der Agent erstellt keinen Commit und keinen Pull Request.

## Sperre

| Maßnahme | Sperre |
| --- | --- |
| Neustart | erwirbt die gemeinsame Sperre über C's Client ohne Warten, prüft Besitz unmittelbar vor dem Patch, gibt sie nach der Rollout-Beobachtung frei |
| Workflow-Dispatch | prüft nur, ob die Sperre frei oder von der eigenen Operation belegt ist, und hält beim Warten keine Ziel-Sperre; `app-deploy.yml` erwirbt sie selbst |
| Lesen, Smoke-Test | keine Sperre |

Belegt eine andere Operation die Sperre, endet der Lauf sofort mit `needs_attention` und dem Grund „gesperrt durch Operation <operationId>“. Es gibt keinen Neustart, keinen Dispatch und kein Warten. `--force` nutzt der Agent nie.

## Stabile Korrelation

Vor jedem ändernden externen Aufruf legt der Adapter über C einen Aktionsdatensatz an (`record_planned`) und erhält die `actionId`.

- **Neustart:** Annotation `ops.mmk/action-id=<actionId>` am Pod-Template. Die Template-Änderung löst den Rollout aus.
- **Dispatch:** Eingaben `operationId` und `actionId`; B's `run-name` enthält beide. Zugeordnet wird nur über gespeicherte Lauf-ID oder die `actionId` im Titel. Gibt die API beim Dispatch eine Lauf-ID zurück (`return_run_details`), wird sie direkt übernommen.

## Abgleich nach Timeout oder Neustart

| Lage | Verhalten |
| --- | --- |
| Neustart, Template trägt die `actionId` | lief bereits: nur Rollout beobachten, nicht erneut patchen |
| Neustart, Template trägt sie nicht | lief nicht: Aktion als nicht ausgeführt markieren; neue Maßnahme nur unter gültigem Besitz |
| Dispatch mit gespeicherter Lauf-ID | denselben Lauf weiterverfolgen, keinen Ersatzlauf starten |
| Dispatch-Antwort verloren | Lauf mit der `actionId` suchen; genau einer: übernehmen |
| mehrere Läufe mit derselben `actionId` | `needs_attention` mit den Lauf-IDs |
| keine `actionId`-Treffer, aber nicht zugeordnete Läufe derselben `operationId` | `needs_attention` mit den Lauf-IDs, kein Dispatch |
| nur geplant, nach 3 Minuten kein Lauf | als nicht ausgeführt markieren; ein neuer Dispatch braucht eine neue `actionId` und zählt als Reparaturversuch |
| gestartet oder unklar, kein Lauf auffindbar | `needs_attention`, keine Wiederholung |
| Workflow läuft beim Fristende noch | `needs_attention` mit Laufreferenz (`references.openRun`); ein späterer Lauf setzt die Beobachtung fort |

Besitzverlust (Lease oder Sperre) stoppt jeden weiteren Schreibschritt. Gestartete externe Vorgänge gleicht der nächste Besitzer ab; C verhindert die Neuvergabe ungeklärter Änderungen.

## Verifikation

- Smoke-Test-Image und Prüfpfad aus dem Katalog, per Digest. Job im App-Namespace, Name `smoke-<actionId>`, `TARGET_URL=https://<app-id>.<baseDomain>` (bei `OPS_HTTP_SCHEME=http` der dokumentierte HTTP-Fallback nach E8), Image-Pull-Secret aus `OPS_APP_IMAGE_PULL_SECRET`, Test-Secrets als `secretKeyRef` auf `<app-id>-platform`.
- Ausgewertet werden Exit-Code und die letzte JSON-Zeile auf stdout. Erfolg nur bei Exit-Code 0 **und** `success: true`.
- Endet der Job nicht innerhalb von `OPS_SMOKE_TIMEOUT_SECONDS` (begrenzt durch die Restlaufzeit), wird er gelöscht und als Zeitüberschreitung ohne Erfolg gemeldet.
- Zum Ergebnis kommen bereite Replicas, Image-Digests, Neustartzähler und Rollout-Status; dazu `appId`, `environment`, tatsächlicher Release (Namespace-Annotationen plus laufender Digest), `checkRef` (Digest des Test-Images), Zeitraum und Evidenzreferenzen.

## Nachvollziehbare Läufe

`operations-agent/tests/agent/test_runner.py` spielt beide Fälle über den echten Deep-Agents-Graph mit simulierten Gegenstellen durch:

- positiv: `test_known_repair_redeploys_and_verifies` (Dispatch mit `operationId`/`actionId`, Lauf verfolgt, Smoke-Test positiv, `resolved`),
- negativ: `test_unrepairable_config_ends_in_needs_attention_with_commit` (Redeploy abgewiesen, Neustart, Smoke-Test negativ, `needs_attention` mit Commit und Logzeilen),
- dazu Sperrkonflikt, Grenzen, Wiederaufnahme, mehrdeutige Läufe und eingeschleuste Anweisungen.

Der Nachweis über echte Staging-Schnittstellen folgt in der gemeinsamen Abnahme (Szenarien 4 und 5).
