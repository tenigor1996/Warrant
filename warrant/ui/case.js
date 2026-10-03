/* WARRANT — case-file display.
   ----------------------------------------------------------------------------
   Two jobs, both pure display:

     1. The record header. Incident id, detected-at, elapsed and final status.
        This is what makes the page read as a document about one incident
        rather than a console that happens to be pointed at one.

     2. The timeline's elapsed gutter. app.js emits a phase, the event body and
        a duration; it does not emit the event's clock time, so the elapsed
        offset is written in here as a .tl-time node.

   app.js hands this module the same report, health and event array the panels
   already render, from a guarded safeRender block, and that is the only
   coupling. Nothing here reads state/, decides anything, or writes back.

   HOW THE ROWS ARE MATCHED
   app.js appends one row per newly-seen seq, in seq order, and clears the
   container on a stream reset. Replaying that same first-seen bookkeeping here
   gives the row order exactly. The model is checked against the real child
   count every tick, and on any disagreement every stamp is removed and the
   model is rebuilt on the next pass. A missing stamp is a cosmetic gap; a
   wrong one would be a lie about when something happened, so the mismatch
   branch always drops the stamps rather than guessing. */
(function () {
  "use strict";

  /* The three outcomes the team froze, matching app.js. NOT_RECOVERED is a
     real result reported honestly, not an error state, and UNVERIFIED means
     the agent never confirmed the after-metrics. */
  var OUTCOMES = { RECOVERED: 1, NOT_RECOVERED: 1, UNVERIFIED: 1 };

  // Row order model, mirroring app.js's own first-seen bookkeeping.
  var order = [];
  var seen = Object.create(null);
  var lastBase = null;
  var lastCount = -1;

  function el(id) {
    return document.getElementById(id);
  }

  function normEnum(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  // Missing means missing: Number(null) is 0 and Number("") is 0, either of
  // which would paint an absent seq as a real zero.
  function toNumber(v) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function seqOf(e) {
    return toNumber(e && e.seq);
  }

  function msOf(ts) {
    if (!ts) return null;
    var t = new Date(ts).getTime();
    return isNaN(t) ? null : t;
  }

  /* ---------- formatting ---------- */

  // Short enough for a gutter, tabular so the column stays a column.
  function offset(ms) {
    if (ms === null || ms < 0) return "";
    var total = Math.round(ms / 1000);
    if (total < 60) return "+" + total + "s";
    var mins = Math.floor(total / 60);
    var secs = total % 60;
    if (mins < 60) return "+" + mins + ":" + (secs < 10 ? "0" : "") + secs;
    var hrs = Math.floor(mins / 60);
    var rm = mins % 60;
    return "+" + hrs + ":" + (rm < 10 ? "0" : "") + rm + ":" + (secs < 10 ? "0" : "") + secs;
  }

  function span(ms) {
    if (ms === null || ms < 0) return "—";
    var total = Math.round(ms / 1000);
    var secs = total % 60;
    var mins = Math.floor(total / 60) % 60;
    var hrs = Math.floor(total / 3600);
    if (hrs) return hrs + "h " + (mins < 10 ? "0" : "") + mins + "m " + (secs < 10 ? "0" : "") + secs + "s";
    if (mins) return mins + "m " + (secs < 10 ? "0" : "") + secs + "s";
    return secs + "s";
  }

  /* ---------- the record header ---------- */

  function renderHeader(report, events) {
    var r = report || {};

    var id = el("case-id");
    if (id) {
      var incident = normEnum(r.incident_id);
      id.textContent = incident || "pending";
    }

    var detectedAt = msOf(r.detected_at);
    // Before the report lands, the first event is the honest detection time.
    if (detectedAt === null && events && events.length) {
      for (var i = 0; i < events.length; i++) {
        var t = msOf(events[i].timestamp);
        if (t !== null && (detectedAt === null || t < detectedAt)) detectedAt = t;
      }
    }

    var detected = el("case-detected");
    if (detected) {
      detected.textContent =
        detectedAt === null ? "—" : new Date(detectedAt).toLocaleString();
    }

    /* A closed record shows the span it took. An open one counts up, and the
       1 Hz poll is exactly the right granularity for a seconds display, so no
       timer of its own is needed. */
    var resolvedAt = msOf(r.resolved_at);
    var elapsed = el("case-elapsed");
    if (elapsed) {
      if (detectedAt === null) {
        elapsed.textContent = "—";
      } else {
        var end = resolvedAt === null ? Date.now() : resolvedAt;
        elapsed.textContent = span(end - detectedAt);
      }
    }

    var status = el("case-status");
    if (status) {
      var raw = normEnum(r.final_status).toUpperCase();
      var known = OUTCOMES[raw] ? raw : "";
      status.textContent = known || raw || (detectedAt === null ? "—" : "IN PROGRESS");
      status.className = known ? "case-status outcome-" + known.toLowerCase() : "case-status";
    }

    return detectedAt;
  }

  /* ---------- the timeline's elapsed gutter ---------- */

  function resetModel() {
    order = [];
    seen = Object.create(null);
    lastCount = -1;
  }

  function clearStamps(box) {
    var nodes = box.querySelectorAll(".tl-time");
    for (var i = 0; i < nodes.length; i++) nodes[i].parentNode.removeChild(nodes[i]);
  }

  function stampRow(row, text) {
    var node = row.querySelector(".tl-time");
    if (!text) {
      if (node) row.removeChild(node);
      return;
    }
    if (!node) {
      node = document.createElement("span");
      node.className = "tl-time";
      row.appendChild(node);
    }
    if (node.textContent !== text) node.textContent = text;
  }

  function renderGutter(events, base) {
    var box = document.querySelector("#panel-timeline .timeline");
    if (!box) {
      resetModel();
      return;
    }

    var rows = box.children;

    // The container was wiped by a stream reset, so start the model over.
    if (rows.length < order.length) resetModel();

    var valid = [];
    for (var i = 0; i < (events || []).length; i++) {
      if (seqOf(events[i]) !== null) valid.push(events[i]);
    }

    var fresh = valid
      .filter(function (e) {
        return !seen[String(seqOf(e))];
      })
      .sort(function (a, b) {
        return seqOf(a) - seqOf(b);
      });

    for (var j = 0; j < fresh.length; j++) {
      var key = String(seqOf(fresh[j]));
      if (seen[key]) continue; // duplicate seq inside one batch, as app.js does
      seen[key] = fresh[j];
      order.push(key);
    }

    // The model and the DOM disagree, so drop every stamp rather than risk
    // attaching a time to the wrong event, and rebuild on the next tick.
    if (rows.length !== order.length) {
      clearStamps(box);
      resetModel();
      return;
    }

    // Nothing moved and the clock we measure from has not changed.
    if (rows.length === lastCount && base === lastBase) return;
    lastCount = rows.length;
    lastBase = base;

    for (var k = 0; k < order.length; k++) {
      var e = seen[order[k]];
      var t = msOf(e && e.timestamp);
      stampRow(rows[k], base === null || t === null ? "" : offset(t - base));
    }
  }

  /* ---------- entry point, called from app.js's guarded block ---------- */

  function render(report, events) {
    var base = renderHeader(report, events);
    renderGutter(events, base);
  }

  window.WarrantCase = { render: render };
})();
