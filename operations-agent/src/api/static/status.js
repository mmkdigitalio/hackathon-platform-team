"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const path = location.pathname.replace(/\/$/, "") || "/";
  const route = path === "/" ? "dashboard" : path === "/activity" ? "activity" : path === "/applications" ? "applications" : "detail";
  const appId = route === "detail" ? path.split("/").pop() : null;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const labels = { healthy: "Erreichbar", unavailable: "Gestört", unknown: "Unbekannt", success: "Erfolgreich", failure: "Fehlgeschlagen", attention: "Aufmerksamkeit", running: "In Bearbeitung", detected: "Erkannt", closed: "Abgeschlossen", completed: "Abgeschlossen", active: "In Bearbeitung", failed: "Fehlgeschlagen" };
  const categories = { detected: "Erkannt", agent: "Agent", system: "System", resolved: "Verifiziert abgeschlossen" };
  const dateFormat = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
  const timeFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short", timeZone: "Europe/Berlin" });
  let data = null, loading = false, failed = false, timer = null;
  let chat = null;
  const badge = (state, text) => `<span class="badge" data-state="${esc(state)}"><span class="dot" aria-hidden="true"></span>${esc(text || labels[state] || "Unbekannt")}</span>`;
  const date = (value) => value && Number.isFinite(new Date(value).getTime()) ? `<time datetime="${esc(value)}" title="${esc(new Date(value).toLocaleString("de-DE", { timeZone: "Europe/Berlin" }))} Europe/Berlin">${esc(dateFormat.format(new Date(value)))}</time>` : "Noch kein Nachweis";
  const urlFor = (app) => `/applications/${encodeURIComponent(app.id)}`;
  const name = (id) => data.services.find((s) => s.id === id)?.name || "Nicht zugeordnet";
  const appStats = (id) => data.admin?.available ? data.admin.perApp[id] || {} : {};
  const count = (value) => value === null || value === undefined ? "—" : esc(value);
  const adminReady = () => data.admin?.available === true;
  const currentActivity = () => data.admin?.activity || [];
  function selectedOptions(items, current) {
    return items.map(([value, label]) => `<option value="${esc(value)}"${value === current ? " selected" : ""}>${esc(label)}</option>`).join("");
  }
  function field(label, name, options, current) {
    return `<label>${esc(label)}<select name="${name}">${selectedOptions(options, current)}</select></label>`;
  }
  function dataHint() {
    return adminReady() ? "" : '<p class="notice">Betriebsverlauf und Rollout-Nachweise sind derzeit nicht verfügbar. Die HTTP-Prüfungen werden separat angezeigt.</p>';
  }
  function metric(label, value, note, href) {
    return `<a class="metric" href="${esc(href)}"><span class="meta">${label}</span><strong>${count(value)}</strong><span class="muted">${note}</span></a>`;
  }
  function summary() {
    const stale = failed || Date.now() - new Date(data.checkedAt).getTime() > 90000;
    const state = stale ? "unknown" : data.status;
    const title = state === "healthy" ? "Alle geprüften Systeme erreichbar." : state === "degraded" ? "Der Betrieb ist eingeschränkt." : "Aktueller Status unvollständig.";
    const detail = state === "healthy" ? "Anwendungen, Datenbank und Worker antworten. Der Aktivitätenverlauf zeigt Veränderungen und Nachweise." : state === "degraded" ? "Prüfe die betroffenen Anwendungen und die Betriebsbereitschaft des Agenten." : "Fehlende oder veraltete Messwerte erlauben derzeit keine vollständige Aussage.";
    return `<section id="overall-summary" class="summary" data-state="${esc(state)}" aria-label="Gesamtzustand"><h2>${title}</h2><p>${detail}</p></section>`;
  }
  function component(label, state, detail) {
    return `<div class="component"><div><strong>${label}</strong><p class="muted">${esc(detail)}</p></div>${badge(state, state === "healthy" ? "Bereit" : null)}</div>`;
  }
  function activityRows(items) {
    if (!adminReady()) return '<p class="empty">Aktivitäten sind derzeit nicht verfügbar.</p>';
    if (!items.length) return '<p class="empty">Keine passenden Aktivitäten im verfügbaren Zeitraum.<small>Auch ein ruhiger Betrieb wird als leerer Verlauf dargestellt. Es werden keine Ereignisse erfunden.</small></p>';
    return `<ol class="timeline">${items.map((item) => `<li><div class="activity-time">${date(item.at)}</div><div class="activity-copy"><div class="activity-meta"><span class="category">${esc(categories[item.category] || "System")}</span>${categories[item.category] !== item.source ? `<span>${esc(item.source)}</span>` : ""}</div><strong>${esc(item.title)}</strong><p>${item.appId ? `<a href="/applications/${encodeURIComponent(item.appId)}">${esc(name(item.appId))}</a>` : "Nicht zugeordnet"}${item.verified ? '<span class="proof">Prüfnachweis vorhanden</span>' : ""}</p></div>${badge(item.result)}</li>`).join("")}</ol>`;
  }
  function dashboard() {
    const issues = data.services.filter((s) => s.status !== "healthy").sort((a, b) => (a.status === "unavailable" ? 0 : 1) - (b.status === "unavailable" ? 0 : 1));
    const healthy = data.services.filter((s) => s.status === "healthy").length;
    const ops = data.operations;
    const activity = currentActivity().slice(0, 5);
    return `${summary()}<section class="metrics" aria-label="Schneller Tagescheck">
      ${metric("Anwendungen erreichbar", data.services.length ? `${healthy} / ${data.services.length}` : null, "Öffentliche HTTP-Prüfung", "/applications")}
      ${metric("Aufmerksamkeit · 24 h", ops.attentionJobs, "Fehlgeschlagene oder eskalierte Aufträge", "/activity?result=attention&range=24h")}
      ${metric("Aktive Aufträge", ops.activeJobs, "Warteschlange und Bearbeitung", "/applications?state=active")}
      ${metric("Verifizierte Abschlüsse · 24 h", data.admin?.verifiedResolved24h, "Behebung oder Auftrag mit Prüfnachweis", "/activity?category=resolved&range=24h")}
    </section>${dataHint()}<div class="dashboard-grid"><section class="panel"><div class="section-heading"><h2>Handlungsbedarf</h2><a href="/applications">Alle Anwendungen</a></div>${ops.attentionJobs > 0 ? `<a class="issue-row" href="/activity?result=attention&range=24h"><div><strong>Aufträge mit Aufmerksamkeit</strong><p class="muted">${count(ops.attentionJobs)} fehlgeschlagene oder eskalierte Aufträge in 24 Stunden</p></div>${badge("attention", "Verlauf prüfen")}</a>` : ""}${issues.length ? issues.map((s) => `<a class="issue-row" href="${urlFor(s)}"><div><strong>${esc(s.name)}</strong><p class="muted">${esc(s.detail)}</p></div>${badge(s.status)}<span class="text-link">Analysieren</span></a>`).join("") : '<div class="quiet-state"><h3>Keine gestörte Anwendung erkannt.</h3><p>Für die Detailprüfung stehen Überwachung und Verlauf im Anwendungsbestand bereit.</p><a href="/applications">Anwendungsbestand prüfen</a></div>'}</section>
    <section class="panel"><div class="section-heading"><h2>Betriebsbereitschaft</h2><span class="meta">Plattform</span></div>${component("Agent-Worker", ops.worker, ops.heartbeatAgeSeconds === null ? "Kein Heartbeat verfügbar" : `Heartbeat vor ${Math.floor(ops.heartbeatAgeSeconds)} Sekunden`)}${component("Operations-Datenbank", ops.database, ops.database === "healthy" ? "Betriebsdaten lesbar" : "Betriebsdaten nicht bestätigt")}<p class="footnote">Prozessgesundheit und aktuelle App-Erreichbarkeit werden getrennt geprüft.</p></section></div>
    <section class="panel"><div class="section-heading"><h2>Zuletzt passiert</h2><a href="/activity">Alle Aktivitäten</a></div>${activityRows(activity)}</section>`;
  }
  function appsTable(services) {
    if (!services.length && !data.catalogAvailable) return '<p class="empty">Der Anwendungsbestand ist derzeit nicht verfügbar.</p>';
    if (!services.length) return '<p class="empty">Keine Anwendung passt zur Suche.<small>Suchtext oder Zustandsfilter anpassen.</small></p>';
    return `<div class="table-scroll"><table><caption class="sr-only">Anwendungsbestand und Monitoring</caption><thead><tr><th scope="col">Anwendung</th><th scope="col">Erreichbarkeit</th><th scope="col">Überwachung</th><th scope="col">Letzter dokumentierter Rollout</th></tr></thead><tbody>${services.map((s) => {
      const dep = data.admin?.deployments?.[s.id];
      return `<tr><td><a class="app-name" href="${urlFor(s)}">${esc(s.name)}</a><span class="table-sub mono">${esc(s.url ? new URL(s.url).host : "Prüfziel fehlt")}</span></td><td>${badge(s.status)}<span class="table-sub">${esc(s.latencyMs === null ? s.detail : `${s.latencyMs} ms · ${s.detail}`)}</span></td><td><span>${s.url ? "HTTP + TLS".replace(" + TLS", s.monitoring?.tls ? " + TLS" : "") : "Kein Prüfziel"}</span><span class="table-sub">Externe Konfiguration vorhanden</span></td><td>${dep ? `${badge(dep.verified ? "success" : dep.status, dep.verified ? "Verifiziert" : null)}<span class="table-sub">${date(dep.finishedAt || dep.startedAt)}</span>` : `<span class="muted">${adminReady() ? "Noch kein Rollout dokumentiert" : "Daten nicht verfügbar"}</span>`}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  function applications() {
    const query = (params.get("q") || "").trim().toLocaleLowerCase("de");
    const state = params.get("state") || "";
    const services = data.services.filter((s) => (!query || `${s.name} ${s.id} ${s.url || ""}`.toLocaleLowerCase("de").includes(query)) && (!state || (state === "missing" ? !s.url : state === "active" ? appStats(s.id).activeJobs > 0 : s.status === state)));
    return `<form class="filters" action="/applications" method="get"><label class="search">Anwendung oder Host<input type="search" name="q" value="${esc(params.get("q") || "")}" placeholder="Zum Beispiel AFFiNE" autocomplete="off"></label>${field("Zustand", "state", [["", "Alle Zustände"], ["unavailable", "Gestört"], ["unknown", "Unbekannt"], ["healthy", "Erreichbar"], ["missing", "Prüfziel fehlt"], ["active", "Mit aktiven Aufträgen"]], state)}<button type="submit">Filtern</button><a class="reset" href="/applications">Zurücksetzen</a></form>${dataHint()}<section class="panel"><div class="section-heading"><h2>${services.length} ${services.length === 1 ? "Anwendung" : "Anwendungen"}</h2><span class="muted">Details durch Auswahl des Namens</span></div>${appsTable(services)}</section><p class="footnote">HTTP-Erreichbarkeit ist keine Funktionsgarantie. Monitoring-Konfiguration und empfangene Signale stehen auf der jeweiligen Detailseite.</p>`;
  }
  function monitoring(app, stats, deployment) {
    const external = app.monitoring?.external;
    const verification = stats.lastVerification;
    return `<section class="panel" id="monitoring"><div class="section-heading"><h2>Wie wird überwacht?</h2><span class="meta">Quellen getrennt</span></div><div class="monitor-grid">
      <article class="monitor-card"><p class="eyebrow">Live-Prüfung</p><h3>Öffentliche Erreichbarkeit</h3>${badge(app.status)}<dl><dt>Prüfpfad</dt><dd class="mono">${esc(app.checkPath)}</dd><dt>TLS-Zertifikat</dt><dd>${app.monitoring?.tls ? "Wird validiert" : "HTTP, keine TLS-Prüfung"}</dd><dt>Takt</dt><dd>Mit jedem neuen Status-Snapshot</dd><dt>Nachweis</dt><dd>${esc(app.detail)}${app.latencyMs !== null ? ` · ${app.latencyMs} ms` : ""}</dd></dl><p class="footnote">Nur HTTP 200 gilt als erreichbar. Redirects werden nicht verfolgt.</p></article>
      <article class="monitor-card"><p class="eyebrow">Konfiguration</p><h3>Externe Verfügbarkeit</h3>${badge("unknown", "Aktivierung nicht verifiziert")}<dl><dt>Quelle</dt><dd>${esc(external?.source || "Unbekannt")}</dd><dt>Intervall</dt><dd>${external ? `${external.intervalSeconds / 60} Minuten · ${external.locations} Standorte` : "Unbekannt"}</dd><dt>Alarmregel</dt><dd>${external ? `Ab ${external.failedLocations} fehlschlagenden Standorten` : "Unbekannt"}</dd><dt>Alarmziel</dt><dd class="mono">${esc(external?.notificationTarget || "Unbekannt")}</dd><dt>Letztes empfangenes Signal</dt><dd>${adminReady() ? date(stats.lastMonitorSignalAt) : "Daten nicht verfügbar"}</dd></dl><p class="footnote">Diese Angaben beschreiben die IaC-Konfiguration. Ein fehlendes Signal beweist weder Betrieb noch Ausfall des Azure-Monitors.</p></article>
      <article class="monitor-card"><p class="eyebrow">Nach Änderung</p><h3>Smoke-Test</h3>${badge("unknown", app.monitoring?.smokeConfigured ? "Im Katalog konfiguriert" : "Nicht konfiguriert")}<dl><dt>Ausführung</dt><dd>Nach dem Deployment</dd><dt>Letzter Auftrags-Prüfnachweis</dt><dd>${verification ? `${badge(verification.success ? "success" : "failure")}${date(verification.at)}` : adminReady() ? "Noch kein Nachweis" : "Daten nicht verfügbar"}</dd><dt>Letzte Rollout-Prüfung</dt><dd>${deployment?.verified ? date(deployment.finishedAt) : "Kein verifizierter Rollout dokumentiert"}</dd></dl><p class="footnote">Ein einzelner Smoke-Test ist keine kontinuierliche Funktionsüberwachung.</p></article>
    </div></section>`;
  }
  function detail() {
    const app = data.services.find((s) => s.id === appId);
    if (!app) return '<section class="panel"><h2>Anwendungsdaten derzeit nicht verfügbar.</h2><p>Der Katalog konnte nicht vollständig gelesen werden.</p><a href="/applications">Zum Anwendungsbestand</a></section>';
    const stats = appStats(app.id), dep = data.admin?.deployments?.[app.id];
    const items = currentActivity().filter((item) => item.appId === app.id).slice(0, 12);
    return `<p class="breadcrumb"><a href="/applications">Anwendungen</a><span>/</span>${esc(app.name)}</p><nav class="section-tabs" aria-label="Bereiche der Anwendung"><a href="#state">Zustand und Rollout</a><a href="#monitoring">Überwachung</a><a href="#activity">Aktivitäten</a></nav>${dataHint()}<div class="detail-grid" id="state"><section class="panel"><div class="section-heading"><h2>Aktueller Zustand</h2>${badge(app.status)}</div><dl class="facts"><dt>Host</dt><dd class="mono">${esc(app.url ? new URL(app.url).host : "Nicht konfiguriert")}</dd><dt>HTTP-Prüfung</dt><dd>${esc(app.detail)}${app.latencyMs !== null ? ` · ${app.latencyMs} ms` : ""}</dd><dt>Konfigurationsstand im Image</dt><dd>${esc(app.version || "Unbekannt")}</dd><dt>Aktive Aufträge</dt><dd>${count(stats.activeJobs)}</dd><dt>Aufmerksamkeit · 24 h</dt><dd>${count(stats.attentionJobs)}</dd></dl>${app.url ? `<a class="text-link" href="${esc(app.url)}" target="_blank" rel="noopener noreferrer">Anwendung öffnen (neuer Tab)</a>` : ""}</section>
      <section class="panel"><div class="section-heading"><h2>Letzter dokumentierter Rollout</h2>${dep ? badge(dep.verified ? "success" : dep.status, dep.verified ? "Verifiziert" : null) : ""}</div>${dep ? `<dl class="facts"><dt>Zeitpunkt</dt><dd>${date(dep.finishedAt || dep.startedAt)}</dd><dt>Chart-Version</dt><dd class="mono">${esc(dep.chartVersion || "Nicht dokumentiert")}</dd><dt>Git-Stand</dt><dd class="mono">${esc(dep.gitSha ? dep.gitSha.slice(0, 12) : "Nicht dokumentiert")}</dd></dl><details><summary>Image-Referenz</summary><code>${esc(dep.imageDigest || "Nicht dokumentiert")}</code></details>${dep.runUrl ? `<a class="text-link" href="${esc(dep.runUrl)}" target="_blank" rel="noopener noreferrer">Deployment-Nachweis in GitHub (neuer Tab)</a>` : ""}` : `<p class="empty">${adminReady() ? "Noch kein Deployment-Lauf dokumentiert." : "Rollout-Daten sind derzeit nicht verfügbar."}</p>`}<p class="footnote">Dokumentierter Release-Stand zum Prüfzeitpunkt, kein Live-Inventar der Kubernetes-Container.</p></section></div>
      ${monitoring(app, stats, dep)}<section class="panel" id="activity"><div class="section-heading"><h2>Aktivitäten für ${esc(app.name)}</h2><a href="/activity?app=${encodeURIComponent(app.id)}&range=7d">Gefilterten Verlauf öffnen</a></div>${activityRows(items)}</section>`;
  }
  function activity() {
    const app = params.get("app") || "", category = params.get("category") || "", result = params.get("result") || "";
    const range = params.get("range") === "7d" ? "7d" : "24h";
    const cutoff = Date.now() - (range === "7d" ? 7 : 1) * 86400000;
    const items = currentActivity().filter((item) => (!app || item.appId === app) && (!category || item.category === category) && (!result || (result === "attention" ? ["attention", "failure"].includes(item.result) : item.result === result)) && new Date(item.at).getTime() >= cutoff);
    return `<form class="filters activity-filters" action="/activity" method="get">${field("Anwendung", "app", [["", "Alle Anwendungen"], ...data.services.map((s) => [s.id, s.name])], app)}${field("Kategorie", "category", [["", "Alle Kategorien"], ...Object.entries(categories)], category)}${field("Ergebnis", "result", [["", "Alle Ergebnisse"], ["attention", "Aufmerksamkeit / Fehler"], ["success", "Erfolgreich"], ["running", "In Bearbeitung"], ["detected", "Erkannt"], ["closed", "Abgeschlossen"]], result)}${field("Zeitraum", "range", [["24h", "Letzte 24 Stunden"], ["7d", "Letzte 7 Tage"]], range)}<button type="submit">Filtern</button><a class="reset" href="/activity">Zurücksetzen</a></form>${dataHint()}<section class="panel"><div class="section-heading"><h2>${adminReady() ? `${items.length} ${items.length === 1 ? "Aktivität" : "Aktivitäten"}` : "Aktivitäten"}</h2><span class="muted">Neueste zuerst</span></div><p class="footnote activity-limit">Verfügbarer Ausschnitt: die letzten ${data.admin?.activityLimit || 100} Einträge aus bis zu sieben Tagen. Filter grenzen diesen Ausschnitt ein.</p>${activityRows(items)}</section><p class="footnote">„Verifiziert abgeschlossen“ erfordert einen abgeschlossenen Auftrag mit erfolgreichem jüngstem Prüfnachweis. Monitoring-Störungen, Support-Fälle und andere Aufträge werden getrennt benannt. Entwarnungen oder einzelne Aktionen sind keine automatische Behebungsbestätigung.</p>`;
  }
  function createPortal(panel) {
    // Keep this DOM independent of status refreshes so drafts, focus and live regions survive.
    const read = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
    const write = (key, value) => { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* Storage is optional. */ } };
    let conversations = {};
    try {
      const saved = JSON.parse(read("mmk-portal-conversations"));
      if (saved && typeof saved === "object" && !Array.isArray(saved)) conversations = saved;
    } catch { /* Ignore damaged or unavailable storage. */ }
    let ids = Array.isArray(conversations[appId]) ? conversations[appId].filter((id) => typeof id === "string" && id.length > 0) : [];
    ids = [...new Set(ids)];
    const finished = new Set(["resolved", "failed", "needs_attention"]);
    const states = {
      received: ["running", "Meldung angenommen"], queued: ["running", "Agent wartet auf Bearbeitung …"],
      investigating: ["running", "Agent untersucht …"], acting: ["running", "Agent handelt …"],
      verifying: ["running", "Agent prüft die Wirkung …"], resolved: ["success", "Behoben (verifiziert)"],
      failed: ["failure", "Fehlgeschlagen"], needs_attention: ["attention", "Braucht Aufmerksamkeit"],
    };
    const uuid = () => globalThis.crypto?.randomUUID?.() || "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
      const value = Math.floor(Math.random() * 16);
      return (character === "x" ? value : (value & 3) | 8).toString(16);
    });
    let code = read("mmk-portal-code") || "", selected = ids[0] || "";
    let enabled = false, base = "", authenticated = false, offline = false, busy = false, stopped = false;
    let pollTimer = null, polling = false, generation = 0, notice = "", lastMessages = null;
    const requests = new Set(), threads = new Map();
    const thread = () => {
      if (!threads.has(selected)) threads.set(selected, { draft: "", messages: [], jobs: [], pending: [], attempt: null });
      return threads.get(selected);
    };
    panel.innerHTML = `<div class="section-heading"><h2 id="portal-title">Problem melden</h2><span class="meta">Support-Chat</span></div>
      <p id="portal-disabled" class="muted" hidden>Der Support-Chat ist derzeit nicht eingerichtet.</p>
      <p id="portal-notice" class="notice" role="status" aria-live="polite" hidden></p>
      <button id="portal-reconnect" type="button" hidden>Verbindung erneut prüfen</button>
      <form id="portal-gate"><p class="muted">Zugangscode von der Plattform-Betreuung</p><label for="portal-code">Zugangscode</label><input id="portal-code" type="password" autocomplete="off" required><button type="submit">Code prüfen</button></form>
      <div id="portal-chat" hidden><label for="portal-conversations">Bisherige Meldungen</label><select id="portal-conversations"></select>
        <div id="portal-messages" class="portal-messages" role="log" aria-label="Nachrichten und Bearbeitungsstand" aria-live="polite" aria-relevant="additions text" tabindex="0"></div>
        <form id="portal-compose"><fieldset><legend class="sr-only">Nachricht schreiben</legend>
          <label for="portal-reporter">Dein Name</label><input id="portal-reporter" autocomplete="name" maxlength="120" required>
          <label for="portal-text">Deine Nachricht</label><textarea id="portal-text" rows="3" maxlength="4000" required aria-describedby="portal-help portal-counter" placeholder="Ich bekomme 400er-Fehler beim Hochladen. Bitte den Dienst neu starten."></textarea>
          <div class="portal-compose-meta"><span id="portal-help">Enter sendet, Shift+Enter für eine neue Zeile.</span><span id="portal-counter">0 / 4000</span></div>
          <button id="portal-send" type="submit">Nachricht senden</button>
        </fieldset></form>
      </div>`;
    const el = (id) => panel.querySelector(`#portal-${id}`);
    el("reporter").value = read("mmk-portal-reporter") || "";
    function options() {
      el("conversations").innerHTML = `<option value="">Neue Meldung</option>${ids.map((id, i) => `<option value="${esc(id)}">Meldung ${ids.length - i}</option>`).join("")}`;
      el("conversations").value = selected;
    }
    function controls() {
      el("disabled").hidden = enabled;
      el("gate").hidden = !enabled || authenticated;
      el("chat").hidden = !enabled || !authenticated;
      el("gate").querySelectorAll("input, button").forEach((input) => { input.disabled = busy || offline || !enabled; });
      el("compose").querySelector("fieldset").disabled = busy || offline || !enabled;
      el("conversations").disabled = busy || offline;
      el("reconnect").hidden = !enabled || !offline;
      el("reconnect").disabled = busy;
      el("notice").hidden = !notice || !enabled;
      el("notice").textContent = notice;
      el("counter").textContent = `${el("text").value.length} / 4000`;
      el("send").textContent = thread().attempt ? "Erneut senden" : "Nachricht senden";
      el("compose").setAttribute("aria-busy", String(busy));
    }
    function messages() {
      const current = thread(), jobs = current.jobs;
      const jobBadge = (job) => badge(...(states[job.state] || ["unknown", "Bearbeitungsstand unbekannt"]));
      const rows = [...current.messages, ...current.pending.filter((pending) => !current.messages.some((message) => message.id === pending.id))];
      const html = rows.map((message, index) => {
        const job = jobs.find((item) => item.id === message.jobId);
        const reporter = message.role === "reporter";
        const author = reporter ? message.author || "Du" : message.role === "agent" ? "Betriebsagent" : "System";
        const result = message.kind === "result";
        const status = job && !rows.slice(index + 1).some((item) => item.jobId === job.id);
        return `<div class="portal-message${reporter ? " portal-reporter" : ""}${result ? " portal-result" : ""}"><div class="portal-message-meta"><strong>${esc(author)}</strong>${message.createdAt ? date(message.createdAt) : ""}</div>${result ? `<div class="portal-result-badge">${job ? jobBadge(job) : badge("unknown", "Ergebnis")}</div>` : ""}<p class="portal-body">${esc(message.body)}</p>${message.pending ? `<small>${message.accepted ? "Angenommen · Verlauf wird geladen …" : message.failed ? "Noch nicht bestätigt · erneut senden" : "Wird gesendet …"}</small>` : ""}</div>${status ? `<p class="portal-job-status">${jobBadge(job)}</p>` : ""}`;
      }).join("") + jobs.filter((job) => !rows.some((message) => message.jobId === job.id)).map((job) => `<p class="portal-job-status">${jobBadge(job)}</p>`).join("");
      const content = html || '<p class="empty">Beschreibe das Problem. Der Betriebsagent antwortet hier.</p>';
      const log = el("messages");
      if (lastMessages !== content) {
        lastMessages = content;
        const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
        log.innerHTML = content;
        if (atEnd) log.scrollTop = log.scrollHeight;
      }
    }
    function clearPoll() { clearTimeout(pollTimer); pollTimer = null; }
    function schedule(delay) {
      clearPoll();
      if (stopped || document.hidden || busy || !panel.isConnected || !enabled || !authenticated || offline || !selected) return;
      const current = thread();
      const active = current.pending.length > 0 || current.jobs.some((job) => !finished.has(job.state));
      pollTimer = setTimeout(poll, delay ?? (active ? 3000 : 15000));
    }
    async function request(path, body, accessCode = code) {
      const controller = new AbortController();
      requests.add(controller);
      const timeout = setTimeout(() => controller.abort(), 12000);
      try {
        const response = await fetch(`${base}/portal${path}`, {
          method: body ? "POST" : "GET", cache: "no-store", credentials: "omit", signal: controller.signal,
          headers: { "X-Portal-Code": accessCode, ...(body ? { "Content-Type": "application/json" } : {}) },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        if (!response.ok) throw Object.assign(new Error("portal request failed"), { status: response.status });
        return await response.json();
      } finally { clearTimeout(timeout); requests.delete(controller); }
    }
    function showError(error) {
      if (error.status === 401) {
        code = ""; authenticated = false; offline = false;
        write("mmk-portal-code", null); el("code").value = "";
        notice = "Bitte den Zugangscode erneut eingeben.";
        clearPoll();
      } else if (error.status === 429) {
        notice = "Zu viele Nachrichten, bitte kurz warten.";
      } else if (!error.status || error.status >= 500) {
        offline = true; clearPoll();
        notice = "Support-Chat derzeit nicht erreichbar. Bitte erneut versuchen. Dein Entwurf bleibt erhalten.";
      } else {
        notice = error.status === 404 ? "Diese Meldung ist nicht verfügbar. Wähle eine andere oder eine neue Meldung." : "Die Anfrage konnte nicht verarbeitet werden. Bitte Eingaben prüfen und erneut versuchen.";
      }
      controls();
      if (error.status === 401 && !busy) el("code").focus({ preventScroll: true });
    }
    async function poll() {
      clearPoll();
      if (polling || stopped || document.hidden || busy || !authenticated || !enabled || offline || !selected) return;
      polling = true;
      const id = selected, current = thread(), version = generation;
      try {
        const result = await request(`/conversations/${encodeURIComponent(id)}`);
        if (stopped || version !== generation) return;
        if (result.conversationId !== id || result.appId !== appId || !Array.isArray(result.messages) || !Array.isArray(result.jobs)) throw new Error("invalid conversation");
        current.messages = result.messages; current.jobs = result.jobs;
        current.pending = current.pending.filter((pending) => !pending.accepted || !result.messages.some((message) => message.id === pending.id));
        if (selected === id) messages();
      } catch (error) { if (!stopped && version === generation) showError(error); }
      finally {
        if (version === generation) { polling = false; schedule(selected !== id ? 0 : undefined); }
      }
    }
    async function verify() {
      if (busy || !enabled || stopped) return;
      const candidate = authenticated ? code : el("code").value.trim() || code;
      if (!candidate) { offline = false; controls(); el("code").focus(); return; }
      busy = true; offline = false; notice = "Zugang wird geprüft …"; controls();
      const version = generation;
      try {
        const session = await request("/session", null, candidate);
        if (stopped || version !== generation) return;
        if (session.ok !== true) throw Object.assign(new Error("invalid session"), { status: 401 });
        code = candidate; authenticated = true; notice = "";
        write("mmk-portal-code", code); el("code").value = "";
      } catch (error) { if (!stopped && version === generation) showError(error); }
      finally {
        if (!stopped && version === generation) {
          busy = false; controls();
          if (authenticated && !offline) { el("text").focus({ preventScroll: true }); poll(); }
          else if (offline) el("reconnect").focus({ preventScroll: true });
          else el("code").focus({ preventScroll: true });
        }
      }
    }
    async function send(event) {
      event.preventDefault();
      if (busy || offline || !authenticated || stopped || !enabled || !el("compose").reportValidity()) return;
      const current = thread(), text = el("text").value.trim(), reporter = el("reporter").value.trim();
      if (!text || !reporter) { notice = "Bitte Namen und Nachricht eingeben."; controls(); return; }
      // Retrying an uncertain POST uses exactly the same payload and id, including conversation creation.
      if (!current.attempt || current.attempt.text !== text || current.attempt.reporter !== reporter) {
        current.pending = current.pending.filter((message) => message.accepted);
        current.attempt = { text, reporter, clientMessageId: uuid() };
        current.pending.push({ id: current.attempt.clientMessageId, role: "reporter", author: reporter, body: text, pending: true });
      }
      const attempt = current.attempt, optimistic = current.pending.find((message) => message.id === attempt.clientMessageId);
      optimistic.failed = false;
      const id = selected, version = generation;
      busy = true; notice = ""; clearPoll(); controls(); messages();
      write("mmk-portal-reporter", reporter);
      try {
        const result = await request(id ? `/conversations/${encodeURIComponent(id)}/messages` : "/conversations", id ? attempt : { appId, ...attempt });
        if (stopped || version !== generation) return;
        const conversationId = id || result.conversationId;
        if (!conversationId || !result.messageId) throw new Error("invalid receipt");
        if (!id) {
          selected = conversationId; threads.set(selected, current); threads.delete("");
          ids = [selected, ...ids.filter((item) => item !== selected)];
          conversations = { ...conversations, [appId]: ids };
          write("mmk-portal-conversations", JSON.stringify(conversations)); options();
        }
        optimistic.id = result.messageId; optimistic.jobId = result.jobId; optimistic.accepted = true;
        if (result.jobId && !current.jobs.some((job) => job.id === result.jobId)) current.jobs.push({ id: result.jobId, state: result.status || "received" });
        current.attempt = null; current.draft = ""; el("text").value = "";
        notice = "Nachricht angenommen.";
      } catch (error) {
        if (!stopped && version === generation) { optimistic.failed = true; showError(error); }
      } finally {
        if (!stopped && version === generation) {
          busy = false; controls(); messages();
          if (authenticated && !offline) el("text").focus({ preventScroll: true });
          else if (offline) el("reconnect").focus({ preventScroll: true });
          else el("code").focus({ preventScroll: true });
          if (authenticated && !offline) poll();
        }
      }
    }
    el("gate").addEventListener("submit", (event) => { event.preventDefault(); verify(); });
    el("reconnect").addEventListener("click", verify);
    el("compose").addEventListener("submit", send);
    el("reporter").addEventListener("input", () => write("mmk-portal-reporter", el("reporter").value));
    el("text").addEventListener("input", () => { thread().draft = el("text").value; controls(); });
    el("text").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); el("compose").requestSubmit(); }
    });
    el("conversations").addEventListener("change", () => {
      thread().draft = el("text").value; selected = el("conversations").value;
      el("text").value = thread().draft; notice = ""; controls(); messages();
      clearPoll(); poll();
    });
    document.addEventListener("visibilitychange", () => { clearPoll(); if (!document.hidden) poll(); });
    function stop() {
      stopped = true; generation++; clearPoll();
      requests.forEach((controller) => controller.abort()); requests.clear(); busy = false; polling = false;
    }
    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", (event) => {
      if (event.persisted) { stopped = false; controls(); if (authenticated) poll(); else if (code) verify(); }
    });
    options(); messages();
    return {
      update(config) {
        const nextEnabled = config?.enabled === true, nextBase = (config?.url || "").replace(/\/$/, "");
        if (enabled === nextEnabled && base === nextBase) { controls(); return; }
        stop(); stopped = false; enabled = nextEnabled; base = nextBase; authenticated = false; offline = false; notice = "";
        controls();
        if (enabled && code) verify();
      },
    };
  }
  function updateStale() {
    const stale = failed || !data || Date.now() - new Date(data.checkedAt).getTime() > 90000;
    document.body.dataset.stale = String(stale);
    $("data-warning").hidden = !stale;
    $("data-warning").textContent = data ? "Messwerte sind nicht mehr aktuell. Der letzte bekannte Stand bleibt sichtbar; der aktuelle Zustand ist unbekannt." : "Aktuelle Daten sind nicht verfügbar. Die Verbindung wird automatisch erneut geprüft.";
    if (stale && data && $("overall-summary")) $("overall-summary").outerHTML = summary();
  }
  function render() {
    const focused = document.activeElement;
    const currentForm = document.querySelector(".filters");
    const draft = currentForm ? { name: focused?.form === currentForm ? focused.name : null, values: Object.fromEntries(new FormData(currentForm)), start: focused?.selectionStart, end: focused?.selectionEnd } : null;
    const titles = { dashboard: ["Status", "Der schnelle Tagescheck: Handlungsbedarf erkennen und gezielt weitergehen."], applications: ["Anwendungen", "Deployte Anwendungen finden, Zustand prüfen und Überwachung verstehen."], activity: ["Aktivitäten", "Erkannte Signale, Agentenarbeit, Systemänderungen und verifizierte Behebungen."] };
    const app = data.services.find((s) => s.id === appId);
    const [title, description] = route === "detail" ? [app?.name || "Anwendung", app?.description || "Zustand, Monitoring und Betriebsverlauf."] : titles[route];
    $("page-title").textContent = title;
    $("page-description").textContent = description;
    document.title = `${title} · Plattform · mmkdigital`;
    document.querySelectorAll("[data-nav]").forEach((a) => { if (a.dataset.nav === (route === "detail" ? "applications" : route)) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    $("environment").textContent = data.environment;
    $("checked-at").textContent = `Letzte Prüfung ${timeFormat.format(new Date(data.checkedAt))}`;
    const catalogHint = data.catalogAvailable ? "" : '<p class="notice">Der Anwendungskatalog ist nicht vollständig verfügbar. Fehlende Anwendungen sind kein Nachweis eines leeren Bestands.</p>';
    if (route === "detail") {
      if (!$("detail-main")) {
        $("page-content").innerHTML = '<div class="application-layout"><div id="detail-main"></div><aside id="portal-panel" class="panel portal-panel" aria-labelledby="portal-title"></aside></div>';
        chat = createPortal($("portal-panel"));
      }
      $("detail-main").innerHTML = catalogHint + detail();
      chat.update(data.portal);
    } else {
      $("page-content").innerHTML = catalogHint + (route === "dashboard" ? dashboard() : route === "applications" ? applications() : activity());
    }
    $("page-content").setAttribute("aria-busy", "false");
    if (draft) {
      const form = document.querySelector(".filters");
      for (const [key, value] of Object.entries(draft.values)) if (form.elements[key]) form.elements[key].value = value;
      const target = draft.name ? form.elements[draft.name] : null;
      if (target) { target.focus({ preventScroll: true }); if (draft.start !== null && target.setSelectionRange) target.setSelectionRange(draft.start, draft.end); }
    }
    updateStale();
  }
  function schedule(seconds = 30) {
    clearTimeout(timer);
    const interval = Math.max(10, Math.min(300, Number(seconds) || 30));
    $("refresh-note").textContent = `Automatische Aktualisierung alle ${interval} Sekunden`;
    timer = setTimeout(() => { if (!document.hidden) refresh(); else schedule(interval); }, interval * 1000);
  }
  async function refresh(manual = false) {
    if (loading) return;
    loading = true; $("refresh").disabled = true; $("refresh").setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/status", { cache: "no-store", signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw new Error("status unavailable");
      const next = await response.json();
      if (!Array.isArray(next.services) || !next.operations || !Number.isFinite(new Date(next.checkedAt).getTime())) throw new Error("invalid data");
      data = next; failed = false; render();
      if (manual) $("announcement").textContent = "Status und Betriebsdaten aktualisiert.";
    } catch {
      failed = true; updateStale();
      if (!data) { $("page-content").innerHTML = '<section class="panel"><h2>Daten derzeit nicht verfügbar.</h2><p>Bitte später aktualisieren. Fehlende Daten werden nicht als störungsfreier Betrieb dargestellt.</p></section>'; $("page-content").setAttribute("aria-busy", "false"); }
      if (manual) $("announcement").textContent = "Aktualisierung fehlgeschlagen. Der aktuelle Zustand ist unbekannt.";
    } finally {
      loading = false; $("refresh").disabled = false; $("refresh").setAttribute("aria-busy", "false"); schedule(data?.refreshAfterSeconds);
    }
  }
  $("refresh").addEventListener("click", () => refresh(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(updateStale, 5000);
  refresh();
})();
