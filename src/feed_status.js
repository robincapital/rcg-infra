/*
 * feed_status.js — universal data-freshness widget for RCG dashboards.
 *
 * Drop-in: <script src="feed_status.js"></script> at the bottom of any
 * dashboard HTML and a fixed-position chip appears in the top-right
 * showing the age of every critical JSON feed. Color-coded:
 *   green:  fresh (within per-feed threshold)
 *   amber:  warm (1–2× threshold)
 *   red:    stale (> 2× threshold)
 *
 * Uses HTTP HEAD to read each feed's Last-Modified header — works with
 * the existing Python http.server, no backend changes needed.
 *
 * Configure feeds + thresholds in FEEDS below. To monitor a new file:
 * add an entry. Threshold is in minutes; what counts as "live" depends
 * on the feed's natural cadence (e.g. bloomberg_prices = 15 min,
 * leaderboard = 24 hr because it's a batch job).
 *
 * This implements the "Track" leg of track/monitor/repair on the UI
 * side. The "Monitor" + "Repair" sides live in the watchdog
 * (rcg-screener-watchdog) + the app-side sd_notify ping loop.
 */
(function () {
    "use strict";

    const FEEDS = [
        // file              threshold_min  label
        { file: "bloomberg_prices.json",   t: 15,    label: "bbg prices" },
        { file: "factor_signals_bbg.json", t: 60,    label: "bbg sentiment" },
        { file: "finnhub_signals.json",    t: 60,    label: "finnhub" },
        { file: "leaderboard.json",        t: 24*60, label: "leaderboard" },
        { file: "factor_signals.json",     t: 24*60, label: "factor signals" },
        { file: "watchlist.json",          t: 24*60, label: "watchlist" },
        { file: "correlations.json",       t: 24*60, label: "correlations" },
        { file: "markouts.json",           t: 24*60, label: "markouts" },
        { file: "meta_model_weights.json", t: 7*24*60, label: "meta-blend" },
    ];

    const REFRESH_INTERVAL_MS = 30 * 1000;   // poll every 30s

    // ─── Inject CSS ───
    const css = `
    .rcg-feed-status {
        position: fixed; top: 0.75rem; right: 0.75rem; z-index: 9999;
        background: rgba(10, 22, 40, 0.92); border: 1px solid #1e3050;
        border-radius: 6px; padding: 0.4rem 0.6rem;
        font-family: 'JetBrains Mono', monospace, sans-serif;
        font-size: 0.65rem; color: #e2e8f0;
        max-width: 240px;
        backdrop-filter: blur(4px);
        box-shadow: 0 2px 8px rgba(0,0,0,0.4);
    }
    .rcg-feed-status-header {
        font-size: 0.55rem; color: #8b7635; text-transform: uppercase;
        letter-spacing: 0.1em; margin-bottom: 0.25rem;
        display: flex; justify-content: space-between; cursor: pointer;
    }
    .rcg-feed-status-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 0.12rem 0; font-size: 0.62rem;
    }
    .rcg-feed-status-name { color: #94a3b8; }
    .rcg-feed-status-age  { font-weight: 600; padding-left: 0.5rem; }
    .rcg-fs-fresh { color: #22c55e; }
    .rcg-fs-warm  { color: #c8a84e; }
    .rcg-fs-stale { color: #ef4444; }
    .rcg-fs-dead  { color: #ef4444; font-weight: 800; }
    .rcg-feed-status.collapsed .rcg-feed-status-rows { display: none; }
    .rcg-feed-status.collapsed { padding: 0.3rem 0.5rem; min-width: 0; }
    .rcg-fs-toggle { cursor: pointer; user-select: none; }
    @media (max-width: 768px) {
        .rcg-feed-status { font-size: 0.6rem; max-width: 200px; top: 0.5rem; right: 0.5rem; }
    }
    `;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    // ─── Inject widget ───
    const widget = document.createElement("div");
    widget.className = "rcg-feed-status";
    widget.innerHTML = `
        <div class="rcg-feed-status-header rcg-fs-toggle">
            <span>Feeds</span>
            <span id="rcg-fs-summary">…</span>
        </div>
        <div class="rcg-feed-status-rows" id="rcg-fs-rows"></div>
    `;
    document.body.appendChild(widget);
    widget.querySelector(".rcg-fs-toggle").addEventListener("click", () => {
        widget.classList.toggle("collapsed");
    });

    // ─── Polling ───
    function fmtAge(min) {
        if (min == null) return "—";
        if (min < 1) return "<1m";
        if (min < 60) return `${Math.round(min)}m`;
        if (min < 24 * 60) return `${(min/60).toFixed(1)}h`;
        return `${Math.round(min/1440)}d`;
    }

    function classify(age, threshold) {
        if (age == null) return "rcg-fs-dead";
        if (age <= threshold)      return "rcg-fs-fresh";
        if (age <= threshold * 2)  return "rcg-fs-warm";
        return "rcg-fs-stale";
    }

    async function probeOne(feed) {
        try {
            const r = await fetch(feed.file, {
                method: "HEAD",
                cache: "no-store",
            });
            if (!r.ok) return { ...feed, ageMin: null, status: "rcg-fs-dead" };
            const lm = r.headers.get("Last-Modified");
            if (!lm) return { ...feed, ageMin: null, status: "rcg-fs-dead" };
            const ageMin = (Date.now() - new Date(lm).getTime()) / 60000;
            return { ...feed, ageMin, status: classify(ageMin, feed.t) };
        } catch (e) {
            return { ...feed, ageMin: null, status: "rcg-fs-dead", error: e.message };
        }
    }

    async function refresh() {
        const results = await Promise.all(FEEDS.map(probeOne));
        // Sort: dead first, then stale, warm, fresh (so problems are at top)
        const order = { "rcg-fs-dead": 0, "rcg-fs-stale": 1, "rcg-fs-warm": 2, "rcg-fs-fresh": 3 };
        results.sort((a, b) => order[a.status] - order[b.status]);
        // Render rows
        const rowsEl = document.getElementById("rcg-fs-rows");
        rowsEl.innerHTML = results.map(r =>
            `<div class="rcg-feed-status-row">
                <span class="rcg-feed-status-name">${r.label}</span>
                <span class="rcg-feed-status-age ${r.status}">${fmtAge(r.ageMin)}</span>
             </div>`
        ).join("");
        // Header summary: count by class
        const counts = results.reduce((acc, r) => {
            acc[r.status] = (acc[r.status] || 0) + 1; return acc;
        }, {});
        const totalBad = (counts["rcg-fs-dead"] || 0) + (counts["rcg-fs-stale"] || 0);
        const totalWarm = counts["rcg-fs-warm"] || 0;
        const sumEl = document.getElementById("rcg-fs-summary");
        if (totalBad > 0) {
            sumEl.textContent = `⚠ ${totalBad} stale`;
            sumEl.className = "rcg-fs-stale";
        } else if (totalWarm > 0) {
            sumEl.textContent = `${totalWarm} warm`;
            sumEl.className = "rcg-fs-warm";
        } else {
            sumEl.textContent = "all fresh";
            sumEl.className = "rcg-fs-fresh";
        }
    }

    refresh();
    setInterval(refresh, REFRESH_INTERVAL_MS);
})();
