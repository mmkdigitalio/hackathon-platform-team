# Tasks

D beginnt mit dem lokalen echten Framework-Nachweis. C liefert Ausführungs-, Persistenz- und Sperrvertrag sowie Migrations-CLI, A Foundation-Ausgaben und Composite Action, B Katalog, Sollzustand, Deploy- und Sollzustands-Workflow sowie App-Stack des Agenten, der AFFiNE-Kollege Smoke-Test-Image und Runbook. D baut diese Lieferungen nicht nach; siehe [Abgrenzung](proposal.md#out-of-scope-und-lieferungen-anderer-stränge). Alle Pfade liegen im Monorepo (E1).

## 1. Echter Framework-Nachweis, P1-02

- [x] 1.1 Deep-Agents-, Modelladapter- und Persistenzpakete in Manifest und Lockdatei unter `operations-agent/` pinnen; Abschluss durch reproduzierbare Installation aus der Lockdatei prüfen.
- [x] 1.2 Modellanbieter Azure OpenAI und Anthropic konfigurierbar anbinden, Keys aus `modelApiKeys` bzw. lokal aus Umgebungsvariablen; Abschluss durch echten Tool-Calling-Lauf mit typisiertem Diagnosewerkzeug und Anbieterwechsel allein per Konfiguration prüfen.
- [x] 1.3 SDK-Checkpoint-/Store-Anbindung mit den gepinnten Versionen erproben und den Befund an C übergeben; Abschluss durch ein dokumentiertes Persistenzbeispiel ohne konkurrierende Initialisierung prüfen.
- [x] 1.4 Prototypstart, Modellkonfiguration und Ergebnisse in `platform/operations/agent/` dokumentieren; Abschluss durch ausgeführten lokalen Start aus der Anleitung prüfen.

## 2. Aufrufbare Agentenausführung

- [x] 2.1 Agentenausführung in `operations-agent/src/agent/` gegen C's Vertrag unter `src/contracts/` implementieren; Abschluss durch Worker-Aufruf mit strukturierten Befunden und ausschließlich C-seitig persistierten Zustandsübergängen prüfen.
- [ ] 2.2 Katalog-, Sollzustands-, Release-, Runbook- und Alarm-Payload-Kontext versionsbezogen laden; Abschluss durch getrennte Soll-/Iststände und korrekt zugeordneten AFFiNE-Kontext prüfen.
- [x] 2.3 Grenzen für Laufzeit (10 min), Tool-Aufrufe (30), Reparaturversuche (2) und Modellkosten (etwa 1 USD) konfigurierbar im Code erzwingen; Abschluss durch jede ausgelöste Grenze mit `needs_attention` statt weiterer Aufrufe prüfen.
- [x] 2.4 Kontextdaten von Betriebsregeln trennen und Herkunft von Erkenntnissen dokumentieren; Abschluss durch Log-/Tickettext mit unerlaubter Anweisung ohne zusätzliche Berechtigung prüfen.
- [x] 2.5 Belegte Operations-Sperre ohne Warten behandeln; Abschluss durch `needs_attention` mit „gesperrt durch Operation X“ und ohne ändernde Maßnahme prüfen.

## 3. Lesende Betriebswerkzeuge, Anteil P1-07

- [ ] 3.1 Kubernetes-Zustand, Events und begrenzte Logs über den ServiceAccount `operations-agent` mit Zielbeschränkung auf Katalog-Namespaces anbinden; Abschluss durch reale AFFiNE-Befunde, strukturierten Fehler bei verweigertem Zugriff und abgelehnten Fremd-Namespace prüfen.
- [ ] 3.2 Alarm-Payload-Auswertung und eigene HTTP-Prüfungen anbinden, ohne Azure-Werkzeuge; Abschluss durch Diagnose eines echten Staging-Alarms ohne Azure-Zugangsdaten prüfen.
- [ ] 3.3 GitHub-Dateien und Commit-Historie über die GitHub App, mit dem PAT aus `opsGithubToken` als Rückfall, lesen (geändert 2026-10-30 (Cornelius); war: über den PAT aus `opsGithubToken`); Abschluss durch letzten Commit auf `platform/environments/staging/affine.yaml` mit SHA, Autor und Diff prüfen.
- [ ] 3.4 Issue-Kommentare mit `<!-- mmk-ops-agent -->` und Auftragsreferenz anbinden; Abschluss durch Kommentar im ursprünglichen Support-Issue ohne neuen Auftrag prüfen.
- [x] 3.5 Werkzeug-Eingaben, Fehlerformat und Evidenzreferenzen dokumentieren; Abschluss durch positive, Timeout- und Berechtigungsfehlerfälle der Adaptertests prüfen.
- [ ] 3.6 Abweichung von P1-07 (keine Azure-Diagnose, E6) in `platform/operations/agent/` dokumentieren; Abschluss durch Review mit A prüfen.

## 4. Ändernde Aktionen und Workflow-Verfolgung, Anteil P1-07

- [ ] 4.1 Kontrollierten Neustart über Annotation `ops.mmk/action-id` und C's Aktions-/Sperrclient implementieren; Abschluss durch protokollierten echten Neustart und abgewiesene Änderung ohne gültigen Besitz prüfen.
- [ ] 4.2 Dispatch vorgegebener Workflows aus einer konfigurierten Liste mit Eingaben `operationId` und `actionId` anbinden; Abschluss durch gestarteten, über `actionId` eindeutig zugeordneten Lauf von `app-deploy.yml` und abgelehnten nicht gelisteten Workflow prüfen.
- [ ] 4.3 Tatsächlichen Workflow-Status und Abschluss abrufen; Abschluss durch laufenden Status ohne falsche Erfolgsmeldung und korrekt zugeordnetes Endergebnis prüfen.
- [ ] 4.4 Abgleich nach Timeout oder Neustart für Neustart (Annotation) und Dispatch (Lauf-ID, `operationId`) implementieren; Abschluss durch Wiederfinden derselben Aktion, keine doppelte Ausführung und `needs_attention` bei mehreren passenden Läufen prüfen.
- [ ] 4.5 Dispatch-, Sperr- und Abgleichverfahren in `platform/operations/agent/` dokumentieren; Abschluss durch Workflow-Warten ohne eigene Ziel-Sperre und gemeinsam mit C geprüfte Besitzverlustfälle prüfen.

## 5. Verifikation und bekannte Reparatur, Anteil P1-10

- [ ] 5.1 Smoke-Test-Image aus dem Katalog als Kubernetes-Job mit `TARGET_URL` starten und Exit-Code sowie JSON auf stdout auswerten; Abschluss durch echten Lauf gegen AFFiNE in Staging und gemeldete Zeitüberschreitung bei hängendem Job prüfen.
- [ ] 5.2 Nachweis um aktuellen Kubernetes-Zustand ergänzen und an C liefern; Abschluss durch Nachweis mit Release, `checkRef` und Zeitraum nach der Maßnahme prüfen.
- [ ] 5.3 AFFiNE auf 0 Replicas erkennen und Sollzustand per `app-deploy.yml` wiederherstellen; Abschluss durch verfolgten Lauf und positiven Smoke-Test über echte Staging-Schnittstellen prüfen.
- [ ] 5.4 Ungültigen Konfigurationswert im Sollzustand als nicht reparierbar behandeln; Abschluss durch `needs_attention` mit Verweis auf den Sollzustands-Commit statt behauptetem Erfolg prüfen.
- [ ] 5.5 Maßnahmen, Grenzen und Verifikation in `platform/operations/agent/` dokumentieren; Abschluss durch nachvollziehbaren positiven und negativen Lauf prüfen.

## 6. Agentenpaket liefern, Anteil P1-08

- [ ] 6.1 C's API-/Worker-Einstiegspunkte und Tests in `operations-agent/Dockerfile` integrieren; Abschluss durch getrennten Prozessstart aus demselben Image prüfen.
- [ ] 6.2 Helm-Chart unter `operations-agent/deploy/` mit Deployments `api` und `worker`, Service und Werten für Image-Digest, Secret-Name und Image-Pull-Secret-Name liefern; Abschluss durch `helm lint`, Template-Prüfung und Start gegen ein vom App-Stack angelegtes Secret prüfen.
- [ ] 6.3 Helm-Hook-Job (`pre-install,pre-upgrade`) mit C's Migrations-CLI und Schema-Versionsprüfung in API und Worker einbinden; Abschluss durch Upgrade mit neuer Migration ohne konkurrierende Schemaänderung prüfen.
- [x] 6.4 ServiceAccount `operations-agent`, ClusterRole und ClusterRoleBinding im Chart liefern; Abschluss durch `kubectl auth can-i` für erlaubte und verweigerte Verben prüfen.
- [ ] 6.5 `.github/workflows/agent-build.yml` mit Tests, Image-Push und Chart-Veröffentlichung über den ACR-Admin-User aus `foundation-outputs` sowie Aufruf von `app-update-desired-state.yml` anlegen; Abschluss durch aktualisiertes `platform/environments/staging/operations-agent.yaml` ohne Azure-Anmeldung prüfen.
- [ ] 6.6 Paketstart, Stopp, Wiederanlauf und kompatibles Rollback in `platform/operations/agent/` dokumentieren; Abschluss durch Agenten-Update mit erhaltenem Auftrag und fortgesetzter Workflow-Beobachtung prüfen.

## 7. Gemeinsame Integration, Anteil P1-12

- [ ] 7.1 Abnahmeszenarien 4 und 5 federführend durchführen; Abschluss durch autonome AFFiNE-Reparatur sowie `needs_attention` im nicht reparierbaren Fall mit Smoke-Test-Nachweisen prüfen.
- [ ] 7.2 C bei Szenarien 6, 7 und 8 unterstützen; Abschluss durch gezählte Aktionsaufrufe, wiedergefundenen Workflow-Lauf und Sperrkonflikt ohne zweiten Workflow oder parallele Zieländerung prüfen.
- [ ] 7.3 Modellverbrauchs-, Werkzeug- und Evidenzdaten an C übergeben; Abschluss durch vollständigen Laufnachweis aus Szenario 10 ohne interne Modellgedanken als Pflichtnachweis prüfen.

## 8. Repository-Änderungen über die GitHub App (Änderung vom 30.10.2026)

- [x] 8.1 GitHub App als Zugang mit Installations-Token, Erneuerung vor Ablauf und PAT-Rückfall anbinden; Abschluss durch echten Token-Abruf für beide Repositories und Tests für Cache, Erneuerung und Fehler prüfen.
- [x] 8.2 Git-Werkzeuge (klonen, lesen, ändern, Diff, Commit und Push auf `agent/<operationId>`, Pull Request) mit Allowlist, Pfadsperren, Aktionsdatensatz und Push-Abgleich liefern; Abschluss durch echten Push über die App auf einen Agenten-Branch sowie abgewiesenen Push auf `main` und abgewiesene Workflow-Änderung prüfen.
- [ ] 8.3 App-ID, Installations-ID und privaten Schlüssel über Foundation-Ausgabe und App-Stack an den Agenten liefern (A, B); Abschluss durch Agenten-Pod, der ohne PAT ein Installations-Token erhält.
- [ ] 8.4 Nicht reparierbaren Fall in Staging mit Pull Request des Agenten durchspielen; Abschluss durch `needs_attention` mit Commit-Verweis und geöffnetem, nicht gemergtem Pull Request prüfen.
