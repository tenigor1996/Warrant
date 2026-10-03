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

  // Which file each feed actually came from on the last tick: "live"
  // (state/), "fixture" (fell back) or "none" (neither answered). Surfaced in
  // the header so a live run can never be mistaken for fixture data.
  const provenance = { health: "none", events: "none", report: "none" };
  const FEEDS = ["health", "events", "report"];
  const SOURCE_LABEL = { live: "LIVE", fixture: "FIXTURE", none: "NO DATA" };

  // One warning per key, so a recurring condition does not spam the console
  // every tick. New, distinct problems still get a line.
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
  async function fetchWithFallback(stateUrl, fixtureUrl, parse, feed) {
    const parseBody = parse || ((res) => res.json());
    const urls = [stateUrl, fixtureUrl];
    for (let i = 0; i < urls.length; i++) {
      const url = urls[i];
      if (!url) continue;
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (!res.ok) {
          warnOnce(url, "HTTP " + res.status + ", falling back");
          continue;
        }
        // A half-written file throws here, inside parseBody, and falls back the
        // same way a 404 does. That is deliberate, but it must be visible.
        const data = await parseBody(res);
        if (feed) provenance[feed] = i === 0 ? "live" : "fixture";
        return data;
      } catch (err) {
        warnOnce(url + "|" + err.message, "unusable (" + err.message + "), falling back");
      }
    }
    if (feed) provenance[feed] = "none";
    return null;
  }

  /* ---------- data source indicator ---------- */

  // The header has to answer "am I looking at real data?" at a glance.
  function renderDataSource() {
    for (const feed of FEEDS) {
      const chip = el("src-" + feed);
      if (!chip) continue;
      const state = provenance[feed];
      chip.textContent = feed + " " + SOURCE_LABEL[state];
      chip.className = "src-chip src-" + state;
    }

    const mode = el("src-mode");
    if (!mode) return;
    // Any feed on fixtures means the screen is not purely live.
    const fellBack = FEEDS.some(function (f) { return provenance[f] === "fixture"; });
    const anyLive = FEEDS.some(function (f) { return provenance[f] === "live"; });
    const state = fellBack ? "fixture" : anyLive ? "live" : "none";
    mode.textContent = SOURCE_LABEL[state];
    mode.className = "src-mode src-" + state;
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

      let parsed;
      try {
        parsed = JSON.parse(trimmed);
      } catch (err) {
        // Usually the agent mid-append on the final line: skip it, and it
        // parses on the next poll once the write completes.
        warnOnce("malformed|" + err.message, "skipping malformed event line: " + err.message);
        continue;
      }

      // Valid JSON that is not an event object (a bare null, a string, an
      // array) would throw deeper inside a renderer. Drop it here.
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        warnOnce("non-object-event", "dropping non-object line(s) in events.jsonl");
        continue;
      }
      rows.push(parsed);
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
    // The agent's casing must not decide whether the pill reads UNKNOWN.
    const value = normEnum(status).toLowerCase();
    const known = value === "healthy" || value === "degraded" || value === "recovering";
    const label = known ? value : "unknown";
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

    // toNumber() treats null/blank/non-numeric as "no value", so a missing
    // metric shows a dash instead of a healthy-looking zero.
    el("metric-failure").textContent = formatPercent(h.failure_rate);
    el("metric-latency").textContent = formatMs(h.latency_p95_ms);

    const errors = toNumber(h.errors_last_minute);
    el("metric-errors").textContent = errors === null ? "—" : String(Math.round(errors));

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
    // Sets, not high-water marks: seq 0 renders, a late seq 2 arriving after
    // 1 and 3 still renders, and a duplicate seq never renders twice.
    timelineSeen: new Set(),
    policySeen: new Set(),
    maxSeq: null,
    firstFingerprint: null,
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

  // Missing means missing. Number(null) is 0 and Number("") is 0, which would
  // paint an absent metric as a real zero, so screen those out first.
  function toNumber(v) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function normEnum(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  // Casing from the agent must never decide whether the DENIED frame fires.
  function verdictOf(e) {
    const v = normEnum(e && e.policy_verdict).toUpperCase();
    return v === "ALLOWED" || v === "DENIED" ? v : "";
  }

  function seqOf(e) {
    return toNumber(e && e.seq);
  }

  function bySeq(a, b) {
    return seqOf(a) - seqOf(b);
  }

  function formatDuration(ms) {
    const n = toNumber(ms);
    return n === null ? "" : n + "ms";
  }

  function formatPercent(v) {
    const n = toNumber(v);
    return n === null ? "—" : (n * 100).toFixed(1) + "%";
  }

  function formatMs(v) {
    const n = toNumber(v);
    return n === null ? "—" : Math.round(n) + " ms";
  }

  function formatStamp(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  }

  /* ---------- stream identity ---------- */

  // Events without a usable seq cannot be de-duplicated or ordered, so they
  // are dropped rather than rendered in an arbitrary place.
  function validEvents(events) {
    const valid = [];
    for (const e of events || []) {
      if (seqOf(e) === null) {
        warnOnce("seqless-event", "dropping event(s) with a missing or non-numeric seq");
        continue;
      }
      valid.push(e);
    }
    return valid;
  }

  function lowestSeq(valid) {
    let min = valid[0];
    for (const e of valid) {
      if (seqOf(e) < seqOf(min)) min = e;
    }
    return min;
  }

  function highestSeq(valid) {
    let max = valid[0];
    for (const e of valid) {
      if (seqOf(e) > seqOf(max)) max = e;
    }
    return max;
  }

  function eventFingerprint(e) {
    return [seqOf(e), e.timestamp, e.title].join("\u0001");
  }

  // Restarting the agent truncates events.jsonl and restarts seq at 1. Without
  // this the dashboard would filter every event of the new run out forever.
  function detectStreamReset(valid) {
    if (!view.timelineSeen.size || !valid.length) return false;

    // The stream shrank: fewer/lower seqs than we have already drawn.
    if (view.maxSeq !== null && seqOf(highestSeq(valid)) < view.maxSeq) return true;

    // Or the event now sitting at the lowest seq is not the one we drew there,
    // which catches a restart that has already overtaken the previous run.
    if (view.firstFingerprint !== null &&
        eventFingerprint(lowestSeq(valid)) !== view.firstFingerprint) {
      return true;
    }
    return false;
  }

  // Wipe a panel back to its muted placeholder, container and all.
  function resetPanel(panelId, text) {
    const body = panelBody(panelId);
    if (!body) return;
    body.textContent = "";
    delete body.dataset.placeholder;
    showPlaceholder(panelId, text);
  }

  function resetStream() {
    view.timelineSeen = new Set();
    view.policySeen = new Set();
    view.maxSeq = null;
    view.firstFingerprint = null;
    view.rootCauseKey = null;
    resetPanel("panel-timeline", "Waiting for agent activity…");
    resetPanel("panel-policy", "No policy decisions yet…");
    resetPanel("panel-rootcause", "Not yet diagnosed…");
    console.warn("[warrant] event stream reset - new agent run, panels cleared");
  }

  /* ---------- 1. timeline ---------- */

  function renderTimeline(valid) {
    if (!valid || !valid.length) return;
    const box = ensureContainer("panel-timeline", "timeline");
    if (!box) return;

    const fresh = valid
      .filter(function (e) { return !view.timelineSeen.has(seqOf(e)); })
      .sort(bySeq);
    if (!fresh.length) return;

    // Respect a manual scroll-up: only follow the tail if we were already there.
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 72;

    for (const e of fresh) {
      const seq = seqOf(e);
      if (view.timelineSeen.has(seq)) continue;  // duplicate seq within one batch
      view.timelineSeen.add(seq);
      if (view.maxSeq === null || seq > view.maxSeq) view.maxSeq = seq;
      box.appendChild(timelineRow(e));
    }

    if (nearBottom) box.scrollTop = box.scrollHeight;
  }

  function timelineRow(e) {
    const phase = normEnum(e.phase).toLowerCase();
    const phaseClass = PHASES.indexOf(phase) >= 0 ? phase : "report";
    const kind = normEnum(e.kind).toLowerCase();
    const verdict = verdictOf(e);
    const row = div("tl-row tl-enter");

    if (kind === "policy_decision") row.classList.add("tl-policy");
    if (verdict === "DENIED") row.classList.add("tl-denied");
    if (verdict === "ALLOWED") row.classList.add("tl-allowed");

    row.appendChild(div("tl-phase phase-" + phaseClass, e.phase || "—"));

    const main = div("tl-main");
    main.appendChild(div("tl-title", e.title || ""));
    if (e.detail) {
      const detail = div("tl-detail", e.detail);
      if (kind === "tool_call" || kind === "tool_result") {
        detail.classList.add("mono");
      }
      main.appendChild(detail);
    }
    if (verdict) {
      main.appendChild(
        div(
          "tl-verdict verdict-" + verdict.toLowerCase(),
          (verdict === "DENIED" ? "✕ " : "✓ ") + verdict
        )
      );
    } else {
      // An unrecognized verdict is shown plainly rather than dropped, so a
      // contract drift is visible instead of silent.
      const raw = normEnum(e.policy_verdict).toUpperCase();
      if (raw) main.appendChild(div("tl-verdict verdict-unknown", raw));
    }
    row.appendChild(main);

    row.appendChild(div("tl-dur mono", formatDuration(e.duration_ms)));
    return row;
  }

  /* ---------- 2. policy decisions ---------- */

  function renderPolicy(valid) {
    const decided = (valid || [])
      .filter(function (e) { return verdictOf(e) !== ""; })
      .sort(bySeq);
    if (!decided.length) return;

    const box = ensureContainer("panel-policy", "policy-list");
    if (!box) return;

    let newDenial = false;
    for (const e of decided) {
      const seq = seqOf(e);
      if (view.policySeen.has(seq)) continue;
      view.policySeen.add(seq);
      box.appendChild(policyRow(e));
      if (verdictOf(e) === "DENIED") newDenial = true;
    }

    if (newDenial) soundAlarm(el("panel-policy"));
  }

  function policyRow(e) {
    const denied = verdictOf(e) === "DENIED";
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

  function renderRootCause(valid, report) {
    const diagnose = (valid || [])
      .filter(function (e) { return normEnum(e.phase).toLowerCase() === "diagnose"; })
      .sort(bySeq);
    if (!diagnose.length) return;

    const reasoning = diagnose.filter(function (e) {
      return normEnum(e.kind).toLowerCase() === "reasoning";
    });
    const pick = reasoning.length
      ? reasoning[reasoning.length - 1]
      : diagnose[diagnose.length - 1];

    const sha = report && report.offending_commit ? String(report.offending_commit) : "";
    const key = seqOf(pick) + "|" + sha;
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

  // The three outcomes the team froze. NOT_RECOVERED is a real result reported
  // honestly, not an error state; UNVERIFIED means the agent never confirmed
  // the after-metrics, so the after side is marked unknown rather than guessed.
  const OUTCOMES = {
    RECOVERED: { cls: "rec-ok", banner: null, unknownAfter: false },
    NOT_RECOVERED: { cls: "rec-failed", banner: "Recovery failed", unknownAfter: false },
    UNVERIFIED: { cls: "rec-unverified", banner: "Recovery not verified", unknownAfter: true },
  };

  function outcomeOf(report) {
    const status = normEnum(report && report.final_status).toUpperCase();
    return OUTCOMES[status] ? status : "";
  }

  function outcomeClass(status) {
    const known = OUTCOMES[status];
    return known ? "outcome-" + status.toLowerCase() : "";
  }

  function renderRecovery(report) {
    const status = outcomeOf(report);
    const outcome = OUTCOMES[status];
    if (!report || !outcome) {
      showPlaceholder("panel-recovery", "Awaiting recovery…");
      view.recoveryKey = null;
      return;
    }

    const before = report.metrics_before || {};
    const after = report.metrics_after || {};
    const key = status + "|" + JSON.stringify([before, after]);
    if (key === view.recoveryKey) return;
    view.recoveryKey = key;

    const box = ensureContainer("panel-recovery", "rec");
    if (!box) return;
    box.textContent = "";
    // Rewritten whole, so an outcome change swaps the treatment cleanly.
    box.className = "rec " + outcome.cls;

    if (outcome.banner) box.appendChild(div("rec-banner", outcome.banner));

    const unknown = outcome.unknownAfter;
    box.appendChild(
      recoveryRow(
        "Failure Rate",
        formatPercent(before.failure_rate),
        unknown ? "?" : formatPercent(after.failure_rate),
        unknown
      )
    );
    box.appendChild(
      recoveryRow(
        "P95 Latency",
        formatMs(before.latency_p95_ms),
        unknown ? "?" : formatMs(after.latency_p95_ms),
        unknown
      )
    );
  }

  function recoveryRow(label, before, after, unknownAfter) {
    const row = div("rec-row");
    row.appendChild(div("rec-label", label));
    const pair = div("rec-pair");
    pair.appendChild(div("rec-before", before));
    pair.appendChild(div("rec-arrow", "→"));
    pair.appendChild(
      div("rec-after" + (unknownAfter ? " rec-after-unknown" : ""), after)
    );
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
    if (report.final_status) {
      // A failed run must not wear the green pill.
      const status = outcomeOf(report);
      meta.appendChild(
        div(
          ("rep-status " + outcomeClass(status)).trim(),
          status || normEnum(report.final_status).toUpperCase() || report.final_status
        )
      );
    }
    box.appendChild(meta);

    if (report.report_sha256) {
      box.appendChild(
        div("rep-sha mono", "sha256 " + String(report.report_sha256).slice(0, 16) + "…")
      );
    }

    // A modal left open while the report changes should show the new content.
    const overlay = document.getElementById("report-modal");
    if (overlay && overlay.classList.contains("open")) {
      fillReportSafe(overlay.querySelector(".modal-body"), report);
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
    // Open first: a malformed field must never leave the button looking dead.
    overlay.classList.add("open");
    document.addEventListener("keydown", onModalKey);
    fillReportSafe(overlay.querySelector(".modal-body"), view.report);
  }

  // The modal is built outside the poll loop's guard, so it needs its own.
  function fillReportSafe(bodyEl, report) {
    if (!bodyEl) return;
    try {
      fillReport(bodyEl, report);
    } catch (err) {
      bodyEl.textContent = "";
      bodyEl.appendChild(
        div("rep-error", "This report could not be rendered: " + err.message)
      );
      warnOnce("modal|" + err.message, "report modal render error: " + err.message);
    }
  }

  // Report arrays may carry nulls or scalars from a partial write.
  function objectEntries(list) {
    return Array.isArray(list)
      ? list.filter(function (x) { return x && typeof x === "object" && !Array.isArray(x); })
      : [];
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

  function fact(label, value, valueClass) {
    const f = div("rep-fact");
    f.appendChild(div("rep-fact-label", label));
    f.appendChild(
      div(("rep-fact-value " + (valueClass || "")).trim(), value || "—")
    );
    return f;
  }

  function fillReport(bodyEl, r) {
    if (!bodyEl) return;
    bodyEl.textContent = "";

    const facts = div("rep-facts");
    facts.appendChild(fact("Incident", r.incident_id));
    facts.appendChild(fact("Detected", formatStamp(r.detected_at)));
    facts.appendChild(fact("Resolved", formatStamp(r.resolved_at)));
    const outcome = outcomeOf(r);
    facts.appendChild(
      fact(
        "Final status",
        outcome || normEnum(r.final_status).toUpperCase() || r.final_status,
        outcomeClass(outcome)
      )
    );
    bodyEl.appendChild(facts);

    const symptoms = Array.isArray(r.symptoms)
      ? r.symptoms.filter(function (t) { return t !== null && t !== undefined && t !== ""; })
      : [];
    if (symptoms.length) {
      const s = section("Symptoms");
      const ul = document.createElement("ul");
      ul.className = "rep-list";
      for (const text of symptoms) {
        const li = document.createElement("li");
        li.textContent = typeof text === "object" ? JSON.stringify(text) : text;
        ul.appendChild(li);
      }
      s.appendChild(ul);
      bodyEl.appendChild(s);
    }

    const evidence = objectEntries(r.evidence);
    if (evidence.length) {
      const s = section("Evidence");
      for (const ev of evidence) {
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

    const actions = objectEntries(r.actions_proposed);
    if (actions.length) {
      const s = section("Actions proposed");
      for (const a of actions) {
        const denied = normEnum(a.verdict).toUpperCase() === "DENIED";
        const row = div("act-row " + (denied ? "act-denied" : "act-allowed"));
        row.appendChild(div("act-verdict", normEnum(a.verdict).toUpperCase() || "—"));
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

    if (r.tests && typeof r.tests === "object") {
      const s = section("Verification");
      const t = div("rep-tests");
      t.appendChild(div("test-pass", (toNumber(r.tests.passed) || 0) + " passed"));
      t.appendChild(div("test-fail", (toNumber(r.tests.failed) || 0) + " failed"));
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

  // One panel's exception must not blank the other five, and must not stop
  // the poll loop.
  function safeRender(name, fn) {
    try {
      fn();
    } catch (err) {
      warnOnce(
        "render|" + name + "|" + err.message,
        "render error in " + name + " panel: " + err.message
      );
    }
  }

  async function tick() {
    const [health, eventsText, report] = await Promise.all([
      fetchWithFallback(SOURCES.health[0], SOURCES.health[1], null, "health"),
      fetchWithFallback(SOURCES.events[0], SOURCES.events[1], asText, "events"),
      fetchWithFallback(SOURCES.report[0], SOURCES.report[1], null, "report"),
    ]);

    let valid = [];
    safeRender("parse", function () {
      valid = validEvents(parseJsonl(eventsText));
    });

    // Detected once per tick, before any panel draws, so the timeline and the
    // policy panel always agree on which run they are showing.
    safeRender("stream-reset", function () {
      if (detectStreamReset(valid)) resetStream();
      if (valid.length && view.firstFingerprint === null) {
        view.firstFingerprint = eventFingerprint(lowestSeq(valid));
      }
    });

    safeRender("data-source", renderDataSource);
    safeRender("status", function () { renderStatus(health); });

    // Display only: hands the same health reading to the vitals ribbon
    // (vitals.js). Guarded like every other panel, so a shader or GPU
    // problem can never blank a panel or stop the poll loop.
    safeRender("vitals", function () {
      if (window.WarrantVitals) {
        window.WarrantVitals.setHealth(health, report && report.final_status);
      }
    });
    safeRender("timeline", function () { renderTimeline(valid); });
    safeRender("policy", function () { renderPolicy(valid); });
    safeRender("rootcause", function () { renderRootCause(valid, report); });
    safeRender("recovery", function () { renderRecovery(report); });
    safeRender("report", function () { renderReport(report); });

    // Display only: hands the same report and the same event array the panels
    // already show to the case-file header and the timeline's elapsed gutter
    // (case.js). Guarded like every other panel, so a display problem there
    // can never blank a panel or stop the poll loop.
    safeRender("casefile", function () {
      if (window.WarrantCase) {
        window.WarrantCase.render(report, valid);
      }
    });
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
