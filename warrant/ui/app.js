/* WARRANT dashboard — status, live agent timeline, policy decisions,
   root cause, recovery and the incident report modal, all driven by the
   1 Hz poll over ../state (falling back to ../fixtures). */
(function () {
  "use strict";

  const POLL_MS = 1000;
  const TRIGGER_URL = "http://localhost:8081/demo/trigger";
  const FLASH_MS = 600;
  const ALARM_MS = 900; // DENIED shake + flash, matches the CSS keyframe

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

  /* ---------- Phase 2: panel plumbing ---------- */

  const PHASES = [
    "detect", "investigate", "diagnose", "plan",
    "policy", "act", "verify", "report",
  ];

  // What each panel has already drawn. The loop polls at 1 Hz, so panels append
  // or re-render only on change — that keeps the entry animations firing once
  // rather than restarting every second.
  const view = {
    timelineSeq: 0,
    policySeq: 0,
    rootCauseKey: null,
    recoveryKey: null,
    reportKey: null,
    report: null,
  };

  function panelBody(panelId) {
    const panel = el(panelId);
    return panel ? panel.querySelector(".panel-body") : null;
  }

  // Swaps a panel placeholder for a real container, once.
  function ensureContainer(panelId, className) {
    const body = panelBody(panelId);
    if (!body) return null;
    let box = body.querySelector("." + className);
    if (!box) {
      body.classList.remove("placeholder");
      body.textContent = "";
      delete body.dataset.placeholder;
      box = div(className);
      body.appendChild(box);
    }
    return box;
  }

  // Returns a panel to its muted placeholder state.
  function showPlaceholder(panelId, text) {
    const body = panelBody(panelId);
    if (!body || body.dataset.placeholder === text) return;
    body.classList.add("placeholder");
    body.textContent = text;
    body.dataset.placeholder = text;
  }

  function div(className, text) {
    const d = document.createElement("div");
    if (className) d.className = className;
    if (text !== undefined && text !== null) d.textContent = text;
    return d;
  }

  function bySeq(a, b) {
    return (Number(a.seq) || 0) - (Number(b.seq) || 0);
  }

  function formatDuration(ms) {
    // null/undefined means the event carried no timing — render nothing, not 0ms.
    if (ms === null || ms === undefined || ms === "") return "";
    const n = Number(ms);
    return Number.isFinite(n) ? n + "ms" : "";
  }

  function formatPercent(v) {
    const n = Number(v);
    return Number.isFinite(n) ? (n * 100).toFixed(1) + "%" : "—";
  }

  function formatMs(v) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.round(n) + " ms" : "—";
  }

  function formatStamp(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  }

  /* ---------- 1. timeline ---------- */

  function renderTimeline(events) {
    if (!events || !events.length) return;
    const box = ensureContainer("panel-timeline", "timeline");
    if (!box) return;

    const fresh = events
      .filter(function (e) { return (Number(e.seq) || 0) > view.timelineSeq; })
      .sort(bySeq);
    if (!fresh.length) return;

    // Respect a manual scroll-up: only follow the tail if we were already there.
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 72;

    for (const e of fresh) {
      box.appendChild(timelineRow(e));
      view.timelineSeq = Math.max(view.timelineSeq, Number(e.seq) || 0);
    }

    if (nearBottom) box.scrollTop = box.scrollHeight;
  }

  function timelineRow(e) {
    const phase = PHASES.indexOf(e.phase) >= 0 ? e.phase : "report";
    const row = div("tl-row tl-enter");

    if (e.kind === "policy_decision") row.classList.add("tl-policy");
    if (e.policy_verdict === "DENIED") row.classList.add("tl-denied");
    if (e.policy_verdict === "ALLOWED") row.classList.add("tl-allowed");

    row.appendChild(div("tl-phase phase-" + phase, e.phase || "—"));

    const main = div("tl-main");
    main.appendChild(div("tl-title", e.title || ""));
    if (e.detail) {
      const detail = div("tl-detail", e.detail);
      if (e.kind === "tool_call" || e.kind === "tool_result") {
        detail.classList.add("mono");
      }
      main.appendChild(detail);
    }
    if (e.policy_verdict) {
      const verdict = String(e.policy_verdict);
      main.appendChild(
        div(
          "tl-verdict verdict-" + verdict.toLowerCase(),
          (verdict === "DENIED" ? "✕ " : "✓ ") + verdict
        )
      );
    }
    row.appendChild(main);

    row.appendChild(div("tl-dur mono", formatDuration(e.duration_ms)));
    return row;
  }

  /* ---------- 2. policy decisions ---------- */

  function renderPolicy(events) {
    const decided = (events || [])
      .filter(function (e) {
        return e.policy_verdict === "ALLOWED" || e.policy_verdict === "DENIED";
      })
      .sort(bySeq);
    if (!decided.length) return;

    const box = ensureContainer("panel-policy", "policy-list");
    if (!box) return;

    let newDenial = false;
    for (const e of decided) {
      const seq = Number(e.seq) || 0;
      if (seq <= view.policySeq) continue;
      box.appendChild(policyRow(e));
      view.policySeq = seq;
      if (e.policy_verdict === "DENIED") newDenial = true;
    }

    if (newDenial) soundAlarm(el("panel-policy"));
  }

  function policyRow(e) {
    const denied = e.policy_verdict === "DENIED";
    const row = div("pol-row tl-enter " + (denied ? "pol-denied" : "pol-allowed"));

    const head = div("pol-head");
    head.appendChild(div("pol-mark", denied ? "✕" : "✓"));
    head.appendChild(div("pol-verdict", denied ? "DENIED" : "ALLOWED"));
    row.appendChild(head);

    row.appendChild(div("pol-title", e.title || ""));
    if (e.detail) row.appendChild(div("pol-action mono", e.detail));
    if (e.policy_reason) row.appendChild(div("pol-reason", e.policy_reason));
    return row;
  }

  // The demo key frame. Fires once per new denial; the reflow lets a second
  // denial restart the animation even if the first is still playing.
  function soundAlarm(panel) {
    if (!panel) return;
    panel.classList.remove("denied-alarm");
    void panel.offsetWidth;
    panel.classList.add("denied-alarm");
    setTimeout(function () {
      panel.classList.remove("denied-alarm");
    }, ALARM_MS);
  }

  /* ---------- 3. root cause ---------- */

  function renderRootCause(events, report) {
    const diagnose = (events || [])
      .filter(function (e) { return e.phase === "diagnose"; })
      .sort(bySeq);
    if (!diagnose.length) return;

    const reasoning = diagnose.filter(function (e) { return e.kind === "reasoning"; });
    const pick = reasoning.length
      ? reasoning[reasoning.length - 1]
      : diagnose[diagnose.length - 1];

    const sha = report && report.offending_commit ? String(report.offending_commit) : "";
    const key = pick.seq + "|" + sha;
    if (key === view.rootCauseKey) return;
    view.rootCauseKey = key;

    const box = ensureContainer("panel-rootcause", "rc");
    if (!box) return;
    box.textContent = "";
    box.appendChild(div("rc-title", pick.title || ""));
    if (pick.detail) box.appendChild(div("rc-detail", pick.detail));
    if (sha) box.appendChild(commitChip(sha));
  }

  function commitChip(sha) {
    const chip = div("rc-chip");
    chip.appendChild(div("rc-chip-label", "offending commit"));
    chip.appendChild(div("rc-chip-sha mono", sha));
    return chip;
  }

  /* ---------- 4. recovery ---------- */

  function renderRecovery(report) {
    if (!report || report.final_status !== "RECOVERED") {
      showPlaceholder("panel-recovery", "Awaiting recovery…");
      view.recoveryKey = null;
      return;
    }

    const before = report.metrics_before || {};
    const after = report.metrics_after || {};
    const key = JSON.stringify([before, after]);
    if (key === view.recoveryKey) return;
    view.recoveryKey = key;

    const box = ensureContainer("panel-recovery", "rec");
    if (!box) return;
    box.textContent = "";
    box.appendChild(
      recoveryRow(
        "Failure Rate",
        formatPercent(before.failure_rate),
        formatPercent(after.failure_rate)
      )
    );
    box.appendChild(
      recoveryRow(
        "P95 Latency",
        formatMs(before.latency_p95_ms),
        formatMs(after.latency_p95_ms)
      )
    );
  }

  function recoveryRow(label, before, after) {
    const row = div("rec-row");
    row.appendChild(div("rec-label", label));
    const pair = div("rec-pair");
    pair.appendChild(div("rec-before", before));
    pair.appendChild(div("rec-arrow", "→"));
    pair.appendChild(div("rec-after", after));
    row.appendChild(pair);
    return row;
  }

  /* ---------- 5. report + modal ---------- */

  function renderReport(report) {
    view.report = report || null;
    if (!report) {
      showPlaceholder("panel-report", "Report pending…");
      view.reportKey = null;
      return;
    }

    const key = [report.incident_id, report.final_status, report.report_sha256].join("|");
    if (key === view.reportKey) return;
    view.reportKey = key;

    const box = ensureContainer("panel-report", "rep");
    if (!box) return;
    box.textContent = "";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "report-open-btn";
    btn.className = "rep-open";
    btn.textContent = "View Report";
    btn.addEventListener("click", openReportModal);
    box.appendChild(btn);

    const meta = div("rep-meta");
    meta.appendChild(div("rep-id mono", report.incident_id || "—"));
    if (report.final_status) meta.appendChild(div("rep-status", report.final_status));
    box.appendChild(meta);

    if (report.report_sha256) {
      box.appendChild(
        div("rep-sha mono", "sha256 " + String(report.report_sha256).slice(0, 16) + "…")
      );
    }

    // A modal left open while the report changes should show the new content.
    const overlay = document.getElementById("report-modal");
    if (overlay && overlay.classList.contains("open")) {
      fillReport(overlay.querySelector(".modal-body"), report);
    }
  }

  function ensureModal() {
    let overlay = document.getElementById("report-modal");
    if (overlay) return overlay;

    overlay = div("modal-overlay");
    overlay.id = "report-modal";

    const card = div("modal-card");
    const head = div("modal-head");
    head.appendChild(div("modal-title", "Incident Report"));

    const close = document.createElement("button");
    close.type = "button";
    close.className = "modal-x";
    close.textContent = "✕";
    close.setAttribute("aria-label", "Close report");
    close.addEventListener("click", closeReportModal);
    head.appendChild(close);

    card.appendChild(head);
    card.appendChild(div("modal-body"));
    overlay.appendChild(card);

    // Backdrop click only — a click inside the card must not close it.
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay) closeReportModal();
    });

    document.body.appendChild(overlay);
    return overlay;
  }

  function openReportModal() {
    if (!view.report) return;
    const overlay = ensureModal();
    fillReport(overlay.querySelector(".modal-body"), view.report);
    overlay.classList.add("open");
    document.addEventListener("keydown", onModalKey);
  }

  function closeReportModal() {
    const overlay = document.getElementById("report-modal");
    if (overlay) overlay.classList.remove("open");
    document.removeEventListener("keydown", onModalKey);
  }

  function onModalKey(ev) {
    if (ev.key === "Escape") closeReportModal();
  }

  function section(title) {
    const s = div("rep-section");
    s.appendChild(div("rep-section-title", title));
    return s;
  }

  function fact(label, value) {
    const f = div("rep-fact");
    f.appendChild(div("rep-fact-label", label));
    f.appendChild(div("rep-fact-value", value || "—"));
    return f;
  }

  function fillReport(bodyEl, r) {
    if (!bodyEl) return;
    bodyEl.textContent = "";

    const facts = div("rep-facts");
    facts.appendChild(fact("Incident", r.incident_id));
    facts.appendChild(fact("Detected", formatStamp(r.detected_at)));
    facts.appendChild(fact("Resolved", formatStamp(r.resolved_at)));
    facts.appendChild(fact("Final status", r.final_status));
    bodyEl.appendChild(facts);

    if (Array.isArray(r.symptoms) && r.symptoms.length) {
      const s = section("Symptoms");
      const ul = document.createElement("ul");
      ul.className = "rep-list";
      for (const text of r.symptoms) {
        const li = document.createElement("li");
        li.textContent = text;
        ul.appendChild(li);
      }
      s.appendChild(ul);
      bodyEl.appendChild(s);
    }

    if (Array.isArray(r.evidence) && r.evidence.length) {
      const s = section("Evidence");
      for (const ev of r.evidence) {
        const row = div("ev-row");
        row.appendChild(div("ev-source", ev.source || "—"));
        row.appendChild(div("ev-finding", ev.finding || ""));
        s.appendChild(row);
      }
      bodyEl.appendChild(s);
    }

    if (r.root_cause) {
      const s = section("Root cause");
      s.appendChild(div("rep-rootcause", r.root_cause));
      if (r.offending_commit) s.appendChild(commitChip(r.offending_commit));
      bodyEl.appendChild(s);
    }

    if (Array.isArray(r.actions_proposed) && r.actions_proposed.length) {
      const s = section("Actions proposed");
      for (const a of r.actions_proposed) {
        const denied = a.verdict === "DENIED";
        const row = div("act-row " + (denied ? "act-denied" : "act-allowed"));
        row.appendChild(div("act-verdict", a.verdict || "—"));
        const main = div("act-main");
        main.appendChild(div("act-action mono", a.action || ""));
        if (a.reason) main.appendChild(div("act-reason", a.reason));
        row.appendChild(main);
        s.appendChild(row);
      }
      bodyEl.appendChild(s);
    }

    if (r.action_executed) {
      const s = section("Action executed");
      s.appendChild(div("rep-exec", r.action_executed));
      bodyEl.appendChild(s);
    }

    if (r.tests) {
      const s = section("Verification");
      const t = div("rep-tests");
      t.appendChild(div("test-pass", (r.tests.passed || 0) + " passed"));
      t.appendChild(div("test-fail", (r.tests.failed || 0) + " failed"));
      s.appendChild(t);
      bodyEl.appendChild(s);
    }

    if (r.metrics_before || r.metrics_after) {
      const before = r.metrics_before || {};
      const after = r.metrics_after || {};
      const s = section("Metrics");
      const box = div("rec");
      box.appendChild(
        recoveryRow(
          "Failure Rate",
          formatPercent(before.failure_rate),
          formatPercent(after.failure_rate)
        )
      );
      box.appendChild(
        recoveryRow(
          "P95 Latency",
          formatMs(before.latency_p95_ms),
          formatMs(after.latency_p95_ms)
        )
      );
      s.appendChild(box);
      bodyEl.appendChild(s);
    }

    // Computed by warrant/report/report_renderer.py — the UI only displays it.
    const sha = div("sha-block");
    sha.appendChild(div("sha-label", "Report SHA-256 · tamper-evident"));
    sha.appendChild(div("sha-value mono", r.report_sha256 || "—"));
    bodyEl.appendChild(sha);
  }

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

      renderRootCause(events, report);
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
