/* WARRANT dashboard — Phase 1: status panel + trigger button.
   Timeline / policy / root cause / recovery / report panels are wired for
   Phase 2: the polling and fetch scaffolding is here, the renderers are no-ops. */
(function () {
  "use strict";

  const POLL_MS = 1000;
  const TRIGGER_URL = "http://localhost:8081/demo/trigger";
  const FLASH_MS = 600;

  const SOURCES = {
    health: ["../state/health.json", "../fixtures/health.json"],
    events: ["../state/events.jsonl", "../fixtures/events.jsonl"],
    report: ["../state/report.json", "../fixtures/report.json"],
  };

  // One warning per URL, so a missing file does not spam the console every tick.
  const warned = Object.create(null);

  function warnOnce(url, message) {
    if (warned[url]) return;
    warned[url] = true;
    console.warn("[warrant] " + message + " (" + url + ")");
  }

  function el(id) {
    return document.getElementById(id);
  }

  /* ---------- fetching ---------- */

  // Returns the parsed body of whichever URL answers first, or null.
  // `parse` lets Phase 2 pull events.jsonl as text; it defaults to JSON.
  async function fetchWithFallback(stateUrl, fixtureUrl, parse) {
    const parseBody = parse || ((res) => res.json());
    for (const url of [stateUrl, fixtureUrl]) {
      if (!url) continue;
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (!res.ok) {
          warnOnce(url, "HTTP " + res.status + ", falling back");
          continue;
        }
        return await parseBody(res);
      } catch (err) {
        warnOnce(url, "fetch failed: " + err.message);
      }
    }
    return null;
  }

  function asText(res) {
    return res.text();
  }

  function parseJsonl(text) {
    if (!text) return [];
    const rows = [];
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        rows.push(JSON.parse(trimmed));
      } catch (err) {
        console.warn("[warrant] skipping malformed event line: " + err.message);
      }
    }
    return rows;
  }

  /* ---------- rendering ---------- */

  const PILL_CLASSES = [
    "pill-healthy",
    "pill-degraded",
    "pill-recovering",
    "pill-unknown",
  ];

  function setPill(status) {
    const pill = el("status-pill");
    if (!pill) return;
    const known = status === "healthy" || status === "degraded" || status === "recovering";
    const label = known ? status : "unknown";
    pill.textContent = label.toUpperCase();
    pill.classList.remove.apply(pill.classList, PILL_CLASSES);
    pill.classList.add("pill-" + label);
  }

  function renderStatus(h) {
    if (!h) {
      setPill(null);
      el("metric-failure").textContent = "—";
      el("metric-latency").textContent = "—";
      el("metric-errors").textContent = "—";
      el("status-updated").textContent = "Last update —";
      return;
    }

    setPill(h.status);

    const rate = Number(h.failure_rate);
    el("metric-failure").textContent = Number.isFinite(rate)
      ? (rate * 100).toFixed(1) + "%"
      : "—";

    const latency = Number(h.latency_p95_ms);
    el("metric-latency").textContent = Number.isFinite(latency)
      ? Math.round(latency) + " ms"
      : "—";

    const errors = Number(h.errors_last_minute);
    el("metric-errors").textContent = Number.isFinite(errors)
      ? String(Math.round(errors))
      : "—";

    el("status-updated").textContent = "Last update " + formatTime(h.timestamp);
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";
    const d = new Date(timestamp);
    if (isNaN(d.getTime())) return String(timestamp);
    return d.toLocaleTimeString();
  }

  // Phase 2: fill these in. They already receive fresh data every tick.
  function renderTimeline(events) {} // eslint-disable-line no-unused-vars
  function renderPolicy(events) {} // eslint-disable-line no-unused-vars
  function renderRootCause(report) {} // eslint-disable-line no-unused-vars
  function renderRecovery(report) {} // eslint-disable-line no-unused-vars
  function renderReport(report) {} // eslint-disable-line no-unused-vars

  /* ---------- polling ---------- */

  async function tick() {
    const [health, eventsText, report] = await Promise.all([
      fetchWithFallback(SOURCES.health[0], SOURCES.health[1]),
      fetchWithFallback(SOURCES.events[0], SOURCES.events[1], asText),
      fetchWithFallback(SOURCES.report[0], SOURCES.report[1]),
    ]);

    try {
      renderStatus(health);

      const events = parseJsonl(eventsText);
      renderTimeline(events);
      renderPolicy(events);

      renderRootCause(report);
      renderRecovery(report);
      renderReport(report);
    } catch (err) {
      // A render bug must never stop the poll loop during a demo.
      console.warn("[warrant] render error: " + err.message);
    }
  }

  function startPolling() {
    tick();
    setInterval(tick, POLL_MS);
  }

  /* ---------- trigger ---------- */

  function flash(btn, cls) {
    btn.classList.remove("flash-ok", "flash-err");
    btn.classList.add(cls);
    setTimeout(function () {
      btn.classList.remove(cls);
    }, FLASH_MS);
  }

  async function onTrigger() {
    const btn = el("trigger-btn");
    try {
      const res = await fetch(TRIGGER_URL, { method: "POST" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      flash(btn, "flash-ok");
      console.log("[warrant] incident trigger OK");
    } catch (err) {
      flash(btn, "flash-err");
      console.warn("[warrant] incident trigger failed: " + err.message);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const btn = el("trigger-btn");
    if (btn) btn.addEventListener("click", onTrigger);
    startPolling();
  });
})();
