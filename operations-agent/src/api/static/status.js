/* No framework, third-party requests, credentials or write actions. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const labels = { healthy: "Erreichbar", unavailable: "Gestört", unknown: "Unbekannt" };
  const jobLabels = { received: "Eingegangen", queued: "In Warteschlange", investigating: "Untersuchung", acting: "Maßnahme läuft", verifying: "Verifikation", resolved: "Abgeschlossen", failed: "Fehlgeschlagen", needs_attention: "Aufmerksamkeit nötig" };
  const names = { affine: "AFFiNE", "operations-agent": "Operations-Agent" };
  const timeFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short", timeZone: "Europe/Berlin" });
  const dateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
  let snapshot = null;
  let loading = false;
  let failed = false;

  function stateBadge(element, state, label) {
    element.dataset.state = state;
    element.replaceChildren();
    const dot = document.createElement("span");
    dot.className = "dot";
    element.append(dot, document.createTextNode(label || labels[state] || "Unbekannt"));
  }

  function summary(state, title, detail) {
    $("summary").dataset.state = state;
    $("summary").querySelector(".summary-icon").textContent = state === "healthy" ? "✓" : state === "degraded" ? "!" : "·";
    $("summary-title").textContent = title;
    $("summary-detail").textContent = detail;
  }

  function staleState() {
    const stale = !snapshot || failed || Date.now() - new Date(snapshot.checkedAt).getTime() > 90000;
    document.body.dataset.stale = String(stale);
    $("data-warning").hidden = !stale;
    if (stale) {
      $("data-warning").textContent = snapshot ? "Die Messwerte sind nicht mehr aktuell. Der letzte bekannte Stand bleibt sichtbar." : "Aktuelle Messwerte sind noch nicht verfügbar. Die Verbindung wird automatisch erneut geprüft.";
      if (snapshot || failed) summary("unknown", "Aktueller Status unbekannt.", "Es liegen keine aktuellen, vollständigen Messwerte vor.");
      $("refresh-note").textContent = "Nächster Verbindungsversuch automatisch";
    }
    return stale;
  }

  function render(data) {
    const ops = data.operations;
    $("environment").textContent = data.environment;
    $("checked-at").textContent = timeFormat.format(new Date(data.checkedAt));
    $("checked-at").dateTime = data.checkedAt;
    const healthy = data.services.filter((service) => service.status === "healthy").length;
    $("healthy-count").textContent = data.services.length ? healthy : "—";
    $("service-total").textContent = ` / ${data.services.length || "—"}`;
    $("active-count").textContent = ops.activeJobs ?? "—";
    $("resolved-count").textContent = ops.resolved24h ?? "—";
    $("attention-count").textContent = ops.attentionJobs ?? "—";
    $("attention-count").style.color = ops.attentionJobs > 0 ? "var(--accent-text)" : "";
    if (data.status === "healthy") {
      summary("healthy", "Alle Systeme erreichbar.", "Die öffentlichen Dienste, die Datenbank und der Worker antworten.");
    } else if (data.status === "degraded") {
      const issues = data.services.filter((service) => service.status === "unavailable").length + [ops.database, ops.worker].filter((s) => s === "unavailable").length;
      summary("degraded", "Der Betrieb ist eingeschränkt.", `${issues} ${issues === 1 ? "Prüfung meldet eine Störung" : "Prüfungen melden eine Störung"}. Details stehen bei den betroffenen Komponenten.`);
    } else {
      summary("unknown", "Noch kein vollständiger Status.", "Für einzelne Komponenten fehlen Messwerte oder die Konfiguration.");
    }
    $("catalog-count").textContent = `${data.services.length} ${data.services.length === 1 ? "Dienst" : "Dienste"}`;
    $("service-list").replaceChildren();
    for (const service of data.services) {
      const row = $("service-template").content.cloneNode(true);
      row.querySelector(".service-symbol").textContent = service.id === "affine" ? "A" : service.id === "operations-agent" ? "↳" : service.name.slice(0, 1).toUpperCase();
      row.querySelector("h3").textContent = service.name;
      row.querySelector(".service-info p").textContent = service.url ? new URL(service.url).host : "Prüfziel nicht konfiguriert";
      row.querySelector(".service-version").textContent = service.version ? `Sollversion ${service.version}` : "Sollversion unbekannt";
      stateBadge(row.querySelector(".state"), service.status);
      row.querySelector(".latency").textContent = service.latencyMs !== null ? `${service.latencyMs} ms · ${service.detail}` : service.detail;
      const link = row.querySelector(".service-link");
      if (service.url) {
        link.href = service.url;
        link.setAttribute("aria-label", `${service.name} in einem neuen Tab öffnen`);
      } else link.remove();
      $("service-list").append(row);
    }
    if (!data.services.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Für diese Umgebung ist kein lesbarer App-Katalog verfügbar.";
      $("service-list").append(empty);
    }
    stateBadge($("api-state"), "healthy", "Verbunden");
    $("api-detail").textContent = "Statusdaten erfolgreich abgerufen";
    stateBadge($("database-state"), ops.database, ops.database === "healthy" ? "Verbunden" : null);
    $("database-detail").textContent = ops.database === "healthy" ? "Betriebsdaten erfolgreich gelesen" : ops.database === "unknown" ? "Keine Datenbank verbunden" : "Betriebsdaten nicht lesbar";
    stateBadge($("worker-state"), ops.worker, ops.worker === "healthy" ? "Aktiv" : null);
    const age = ops.heartbeatAgeSeconds;
    $("worker-detail").textContent = age === null ? "Kein Heartbeat verfügbar" : `Letzter Heartbeat vor ${age < 60 ? `${Math.floor(age)} s` : `${Math.floor(age / 60)} min`}`;
    $("activity-list").replaceChildren();
    for (const job of ops.recentJobs) {
      const row = document.createElement("tr");
      const app = document.createElement("td");
      app.textContent = names[job.appId] || job.appId || "Nicht zugeordnet";
      const state = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "state";
      const tone = job.state === "resolved" ? "healthy" : job.state === "failed" ? "unavailable" : job.state === "needs_attention" ? "attention" : "active";
      stateBadge(badge, tone, jobLabels[job.state] || "Unbekannt");
      state.append(badge);
      const date = document.createElement("td");
      const time = document.createElement("time");
      time.dateTime = job.updatedAt;
      time.textContent = dateFormat.format(new Date(job.updatedAt));
      time.title = `${new Date(job.updatedAt).toLocaleString("de-DE", { timeZone: "Europe/Berlin" })} Europe/Berlin`;
      date.append(time);
      row.append(app, state, date);
      $("activity-list").append(row);
    }
    if (!ops.recentJobs.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 3;
      cell.className = "empty";
      cell.textContent = ops.database === "healthy" ? "Noch keine Betriebsaufträge in dieser Umgebung." : "Betriebsaufträge derzeit nicht verfügbar.";
      row.append(cell);
      $("activity-list").append(row);
    }
    $("refresh-note").replaceChildren();
    const dot = document.createElement("span");
    dot.className = "dot live-dot";
    $("refresh-note").append(dot, document.createTextNode("Automatische Prüfung alle 30 Sekunden"));
    staleState();
  }

  async function refresh(manual = false) {
    if (loading) return;
    loading = true;
    $("refresh").disabled = true;
    $("refresh").setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/status", { cache: "no-store", signal: AbortSignal.timeout(12000), headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Status request failed");
      const data = await response.json();
      if (!Array.isArray(data.services) || !data.operations || !Array.isArray(data.operations.recentJobs) || !Number.isFinite(new Date(data.checkedAt).getTime())) throw new Error("Invalid status response");
      snapshot = data;
      failed = false;
      render(data);
      if (manual) $("announcement").textContent = `Aktualisiert. ${$("summary-title").textContent}`;
    } catch {
      failed = true;
      stateBadge($("api-state"), "unavailable", "Nicht verbunden");
      $("api-detail").textContent = "Statusdaten konnten nicht abgerufen werden";
      if (!snapshot) {
        $("service-list").textContent = "Anwendungsstatus derzeit nicht verfügbar.";
        $("activity-list").replaceChildren();
        const row = $("activity-list").insertRow();
        const cell = row.insertCell();
        cell.colSpan = 3;
        cell.className = "empty";
        cell.textContent = "Betriebsaufträge derzeit nicht verfügbar.";
        $("environment").textContent = "Nicht verbunden";
      }
      staleState();
      if (manual) $("announcement").textContent = "Aktualisierung fehlgeschlagen. Der Status ist unbekannt.";
    } finally {
      loading = false;
      $("refresh").disabled = false;
      $("refresh").setAttribute("aria-busy", "false");
    }
  }
  $("refresh").addEventListener("click", () => refresh(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.setInterval(() => { staleState(); if (!document.hidden) refresh(); }, 30000);
  refresh();
})();
