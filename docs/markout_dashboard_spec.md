# Markout Dashboard Spec v1.0
**Date:** 2026-05-19
**Author:** RCG Quant Agent (under MM direction)
**Status:** AWAITING APPROVAL — `ship it` to build

---

## 1. Goal

A trade-first model-evaluation dashboard. Headline question:

> *"If I'd traded each tournament model's signals with a realistic execution
> rule, what would my P&L curve look like?"*

Eval metrics (IC, calibration, hit rate, correlation) appear as supporting
panels under the headline P&L curve.

Lives at `/markouts` as a new top-level page on the existing Flask
dashboard, alongside `/screener`.

---

## 2. Trade mechanics — the "Inflection Threshold Trader" rule

For each tournament entrant, simulate a trading strategy with these rules:

### 2.1 Entry
- **Long entry:** when fire-level score ≥ **+60**
- **Short entry:** when fire-level score ≤ **-60**
- No re-entry if already holding that name in the same direction.

### 2.2 Exit (hysteresis bands)
- **Close long:** when next fire's score for that ticker < **+35**
- **Close short:** when next fire's score for that ticker > **-35**
- The dead zone (-35 ≤ score ≤ +35) closes any open position.
- The intermediate band (±35 to ±60) **holds existing** positions but does
  **not open** new ones. Avoids whipsaw at the boundary.

### 2.3 Hold duration
- No max hold cap. Positions live indefinitely while score remains outside
  the exit band. If a name is parked at score +45 for two weeks, we hold
  it the whole time.

### 2.4 Direction
- Long/short symmetric. Internal eval purposes only. Per `rcg_policy.md`
  §10, live Inflection 2.0 is long-only — short side of this backtest is
  research, not production guidance.

---

## 3. Sizing

- **Equal weight** across active positions at any given moment.
- **Max 15 concurrent positions** (matches RCG policy §10 diversification
  floor — 15 names is policy-max-concentrated while still expressing edge).
- If more than 15 names exceed the entry threshold simultaneously, keep the
  15 with the strongest |score|. Rotate when a stronger candidate appears
  AND a weakest current position would be displaced. This is bookkeeping —
  no transaction costs for "model-mind-changes" within a fire.
- Notional: each position = (current portfolio value × 1 / N_active),
  rebalanced at every fire that triggers a trade.

---

## 4. Cost model

Two independent layers, both applied to the **net** curve:

### 4.1 Platform fee
- **5 bps/year flat drag** (constant deduction, regardless of turnover).
- Models IBKR Pro tiered or similar volume-discount commission structure.
- Applied as `daily_drag = 0.0005 / 252` deducted from the equity curve.

### 4.2 Slippage (per-trade)
- **Default 5 bps/side** (10 bps round-trip).
- User-toggleable: **0 / 5 / 10 bps/side**.
- Applied at every entry and every exit event.
- Models bid-ask spread + market impact. Doesn't go away on a
  zero-commission broker.

### 4.3 Display
- **Two curves on the headline P&L chart:**
  - "Gross alpha" — no costs applied
  - "Net of costs" — 5 bps/year + selected slippage
- Top strip shows both numbers: `Gross +14.3% · Net +11.8%`

---

## 5. What the page displays

### 5.1 Header strip (per selected model + selected horizon)

```
META_BLEND_60min — last 30d
─────────────────────────────────────────────────
Gross return:    +14.3%        Net return:   +11.8%
Sharpe (net):    1.6           Max DD:       -4.2%
Hit rate:        56.1%         IC dir:       +0.12
Avg trades/yr:   2,180         Total trades: 187
```

### 5.2 Top selectors
- **Model:** dropdown (family champions only by default; "show all variants"
  toggle expands to all ~40 entrants)
- **Horizon:** 30min / 60min / **4h** — default 60min
- **Slippage:** 0 / **5** / 10 bps/side — default 5
- **Window:** 7d / 30d / 90d / **All** — default All

### 5.3 Headline chart — cumulative P&L curve
- X-axis: trading days
- Y-axis: cumulative return %
- Lines:
  - Selected model — gross (light)
  - Selected model — net (bold)
  - Equal-weighted watchlist benchmark (grey dashed)
  - SPY (light grey dashed)
- Hover: per-day return, current position count, top contributors

### 5.4 Drawdown chart (shared X-axis underneath)
- Underwater plot of the net curve.
- Highlights drawdown periods on the main chart.

### 5.5 Four supporting panels (2×2 grid)

| Panel | Content |
|---|---|
| **Calibration** | Score-bucket buckets (60-70, 70-80, 80-90, 90-100, and mirror for shorts) → avg realized return at selected horizon. Bar chart. |
| **Rolling IC** | 30-day rolling directional IC, time-series line chart. |
| **Per-ticker contribution heatmap** | Rows = top-20 tickers by trade count, cells = cumulative P&L contribution. Sorted by contribution descending. |
| **Model correlation matrix** | Daily-P&L correlations across family champions (default 12×12). Toggle expands to all entrants. |

### 5.6 Footer filters
- **Regime filter:** All / Current regime / dropdown by regime label
- (Reused from existing leaderboard regime stratification logic)

---

## 6. Page architecture

### 6.1 URL
- `https://<host>/markouts` (new top-level page)

### 6.2 Refresh cadence
- **Nightly cron** at 02:00 ET: recomputes the JSON from full DB scan.
- **Manual refresh button** on the page: triggers a recompute on demand
  (rate-limited to once per 10min to avoid clobbering Postgres during
  market hours).

### 6.3 Mobile
- Responsive single-column layout below 768px width.
- Headline chart full-width, drawdown collapses below as a small strip,
  supporting panels stack vertically.
- Heatmap + correlation matrix: collapsed-by-default accordion on mobile
  (they don't read well at phone width).

---

## 7. File structure

### 7.1 New files

| Path | Purpose |
|---|---|
| `src/markout_eval.py` | Core trade-simulation engine. Reads from signals + runs tables, applies entry/exit/sizing logic per model+horizon, emits per-day P&L series. |
| `src/markout_eval_publish.py` | Top-level cron entry. Iterates all tournament models × 3 horizons, calls `markout_eval`, writes `outputs/markouts.json`. |
| `dashboard/templates/markouts.html` | Page template — header strip, chart container, panels. |
| `dashboard/static/markouts.js` | Plotly chart setup + selector wiring + manual-refresh button. |
| `dashboard/static/markouts.css` | Styles + responsive breakpoints. |
| `docs/markout_dashboard_spec.md` | This document. |

### 7.2 Modified files

| Path | Change |
|---|---|
| `dashboard/app.py` | Add Flask route `/markouts` + `/api/refresh-markouts` (manual refresh endpoint). |
| `systemd/markouts.timer` + `markouts.service` | Nightly cron unit (02:00 ET). |
| `ROADMAP.md` | Mark Phase D entry "Markout dashboard" as 🟢 shipped (v27). |

### 7.3 JSON schema (`outputs/markouts.json`)

```json
{
  "generated_at": "2026-05-19T02:00:00Z",
  "trade_rules": {
    "entry_threshold": 60,
    "exit_threshold": 35,
    "max_concurrent": 15,
    "weighting": "equal",
    "long_short": true
  },
  "cost_model": {
    "platform_fee_bps_per_year": 5,
    "default_slippage_bps_per_side": 5
  },
  "horizons": ["30min", "60min", "4h"],
  "benchmarks": {
    "ew_watchlist": { "daily_returns": [...], "cum": [...] },
    "spy":          { "daily_returns": [...], "cum": [...] }
  },
  "models": {
    "meta_blend_60min": {
      "family": "meta_blend",
      "horizon": "60min",
      "is_champion": true,
      "daily_returns_gross": [...],
      "daily_returns_net_5bps": [...],
      "daily_returns_net_10bps": [...],
      "n_trades": 187,
      "n_long": 132, "n_short": 55,
      "hit_rate": 0.561,
      "ic_dir": 0.12,
      "sharpe_net": 1.6,
      "max_dd_net": -0.042,
      "calibration": [
        {"bucket": "60_70", "avg_return": 0.014, "n": 42},
        ...
      ],
      "rolling_ic_30d": [...],
      "per_ticker_contribution": [
        {"ticker": "AAPL", "n_trades": 12, "cum_pnl": 0.034, ...},
        ...
      ]
    },
    ...
  },
  "model_correlation": {
    "labels": ["meta_blend_60min", "momentum_120_60min", ...],
    "matrix": [[1.0, 0.42, ...], [0.42, 1.0, ...], ...]
  }
}
```

---

## 8. Implementation plan (5 trading days)

| Day | Deliverable |
|---|---|
| 1 | `markout_eval.py` — trade-simulation engine. Unit tests on a single model. |
| 2 | P&L, Sharpe, drawdown, calibration computation. End-to-end JSON for one model. |
| 3 | `markout_eval_publish.py` cron script + Flask route + headline chart wired. |
| 4 | Supporting panels (calibration, rolling IC, heatmap, corr matrix) wired. |
| 5 | Mobile responsive CSS + manual-refresh endpoint + smoke test on live data. |

Each day's work gets a commit + brief Slack update in #quant-research.

---

## 9. Rollback path

- **Code-level:** all new files; modified files (`dashboard/app.py`,
  `ROADMAP.md`) are additive. `git revert` the v27 commit cleanly removes.
- **Cron-level:** `systemctl --user stop markouts.timer` halts refreshes.
- **Data-level:** `outputs/markouts.json` is regenerated each run; no
  destructive writes. Can delete the file and screener tab simply 404s
  until next cron run.
- **No DB schema changes.** Read-only from `signals` + `runs`.

---

## 10. Open items deferred to v2

- **Adaptive thresholds** (top-quartile of trailing 30d scores instead of
  absolute ±60). Interesting but adds a moving target.
- **Daily-horizon coverage** (1d/5d/20d) — depends on Phase B daily markouts
  shipping first. Will retrofit when that data exists.
- **Replay mode** — pick a date, see what each model would have bought
  that day, with realized outcome.
- **Capacity / market-impact modeling** — for now slippage is a flat bps
  number. v2 could scale slippage with position size relative to ADV.
- **Per-trade audit log** — JSON list of every entry/exit with timestamps,
  for compliance / decision_log integration.

---

## 11. Compliance notes

- All data internal (`rcg_policy.md` §6 — backtests internal-only). No
  external publication.
- No live order routing implied. This is research output.
- Long/short eval shown for analysis purposes only; live strategy remains
  long-only per `rcg_policy.md` §10.
- Sector concentration check: not enforced in this backtest. If a model's
  active set ever exceeds the policy §10 80% sector cap, the dashboard
  will flag it but won't filter — analyst's call whether to discount the
  model.

---

## 12. Approval checklist

- [ ] Trade rules match intent (entry ±60, exit ±35, hysteresis, max 15,
      equal-weight, no hold cap)
- [ ] Cost model correct (5 bps/year flat + 5 bps/side slippage default,
      toggleable)
- [ ] Two curves displayed (gross + net), top-strip shows both numbers
- [ ] Page lives at `/markouts`, nightly cron + manual refresh
- [ ] Desktop + mobile responsive
- [ ] Champions-only correlation matrix default
- [ ] 5-day build timeline acceptable
- [ ] Rollback path acceptable

**Reply `ship it` to begin Day 1. Reply `revise` with notes to iterate.**
