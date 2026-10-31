# Standard-Runbook für web-app

Gilt für Katalog-Apps mit `chart.platform.name: web-app`.
App-ID, Umgebung und erlaubte Aktionen immer aus dem Katalog laden.
Namespace ist `<app-id>-<env>`, Deployment und Service heißen `<app-id>`.

## Normalfunktion

Die App beantwortet HTTP-Anfragen am öffentlichen Host.
`availability.path` liefert ohne Anmeldung HTTP 200.
`values.probes.path` soll denselben Prüfpfad verwenden.
Der Service leitet Port 80 an den benannten Container-Port `http` weiter.
Alle gewünschten Pods sind Ready, der Image-Digest entspricht dem Sollzustand.
Mit PVC verwendet das Deployment `Recreate`; dabei entsteht eine kurze Unterbrechung.

## Signale

- Verfügbarkeitsalarm `alert-<app-id>-<env>-availability`.
- Fehlgeschlagener geplanter Health-Check-Job.
- Chat-Meldungen zu Ausfällen, langsamen Antworten oder Fehlern.

Zeitpunkt, App, Umgebung und Auswirkung der Meldung festhalten.
Ein erfolgreiches HTTP-Signal belegt nur die Erreichbarkeit des Prüfpfads.

## Diagnose durch den Agenten

1. Katalog und Sollzustand laden, Aktionsfreigaben und Release-Referenz prüfen.
2. Deployment, ReplicaSets und Pods im App-Namespace lesen.
   Gewünschte, verfügbare und bereite Replikate sowie Neustarts vergleichen.
3. Events auf Scheduling-, Image-Pull-, Mount- und Probe-Fehler prüfen.
4. Aktuelle und vorherige Container-Logs auf Startfehler und Abstürze prüfen.
   Keine Secrets ausgeben oder in Berichte übernehmen.
5. HTTP-Aufruf am Host und Prüfpfad ausführen, Status und Dauer festhalten.
6. Image-Digest, Chart-Version, Replikate und Konfiguration mit dem Sollzustand vergleichen.
   Namespace-Annotationen `ops.mmk/release-*` ergänzen die Pod- und Helm-Daten.
7. Bei Speicherwachstum Verlauf und Limits prüfen; OOMKilled und Neustarts dokumentieren.

## Erlaubte Aktionen

Nur Aktionen aus `allowedActions` und innerhalb der Betriebssperre ausführen.
`restart` ist bei hängenden Pods, belegtem Speicherwachstum oder nach einer
Config-Secret-Rotation erlaubt. Der Neustart lädt die Secret-Werte neu.
Vorher Diagnose sichern, danach den Rollout abwarten.
`redeploy` ist ausschließlich bei belegtem Drift gegenüber dem Sollzustand erlaubt,
zum Beispiel bei tatsächlich 0 Replikaten trotz gewünschter 1.
Dafür den bestehenden App-Deploy-Pfad mit unverändertem Sollzustand verwenden.
Ein fehlgeschlagener HTTP-Check allein begründet kein Redeploy.

## Grenzen

Keine Datenänderungen, Migrationen oder Wiederherstellungen.
Nicht skalieren und keine Ressourcen, Pods, PVCs oder Daten löschen.
Konfigurationsfehler brauchen einen PR auf den Sollzustand und menschliche Prüfung.
Keine Secrets manuell ändern und keine neue Image- oder Chart-Version auswählen.
Datenverfahren im Katalog dienen der Eskalation, nicht als autonome Freigabe.

## Verifikation und Eskalation

Nach jeder Aktion müssen die gewünschten Pods Ready sein.
`smoke-http` mit `CHECK_PATH=availability.path` gegen die App-URL ausführen.
Erfolg braucht Exit-Code 0 und `success: true` im gültigen Testergebnis.
Bei fehlender Freigabe, unklarer Ursache oder erfolgloser Reparatur `needs_attention` setzen.
Diagnose, Release, Aktion und Prüfergebnis an Menschen übergeben.
