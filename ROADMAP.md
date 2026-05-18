# RCG Alpha Engine — Roadmap

**Last updated:** 2026-05-12 (after v18 ship)
**How to read:** statuses are 🟢 done · 🟡 active / in flight · ⚪ pending · 🔵 blocked-on-data · 🔴 blocked-on-decision

---

## Right now (week of 2026-05-11)

| Item | Status | Note |
|---|---|---|
| Per-ticker growth assumptions + valuation report (v13–v15) | 🟢 | Shipped May 11 |
| SOFI-style price fallback fix + RCG-themed report (v15–v16) | 🟢 | Shipped May 11 |
| Tournament leaderboard tooltips + "How to read" guide (v16) | 🟢 | Shipped May 11 |
| Regime tagging (v17) — every fire writes `runs.config_json.regime` | 🟢 | Shipped May 12 |
| Hyperparameter sweep families (v18) — 31 entrants, 11 families | 🟢 | Shipped May 12 |
| ROADMAP.md (work calendar) | 🟢 | Shipped May 12 |
| INSUFFICIENT DATA rating (v19) | 🟢 | Shipped May 12 — clean grey banner instead of misleading SELL |
| Trailing-median fallback PT (v20) | 🟢 | Shipped May 12 — VISN-style names get a PT instead of $0 |
| Visible conviction score 0-100 + low-conviction filter (v21) | 🟢 | Shipped May 12 — color badge on Top 40, scale on report, 6-bucket filter chips |
| **PT consistency audit + fix (v22)** | 🟢 | Shipped May 18 — screener was using legacy PT function. Migrated to canonical engine. Aligned dimension (ARQ), history window (3y), sector lookup, shares_diluted. GDRX now matches between Top 40 and report. |
| **Phone PWA re-tune (v23)** | 🟢 | Shipped May 18 — mobile CSS updated for v22 layout (18-col Top 40, 5 macro chips, conviction filter, report-view) |
| **Tier 1+2 quant signals (v24)** | 🟢 | Shipped May 18 — Hurst, Kalman, AR(2), OU half-life, BB squeeze (single-name patterns) + cross-sectional rank, sector-relative momentum, **PCA residual mean-reversion** (universe-level). 39 entrants across 12 families. |
| Wait for new entrants + regime tags to accumulate 200+ samples each | 🔵 | ~5+ days since v17/v18 — per-family champions + regime IC should be rotating now |

---

## Phase A — Tournament infrastructure (model improvement axes)

Goal: every model improves over time **without ever being cut**. Three independent axes.

### Axis A — Regime awareness (✅ done)

| Item | Status | Notes |
|---|---|---|
| Compute `vol_regime` (VIX bucket) + `trend_regime` (SPY 5d) at every fire | 🟢 v17 | Stored in `runs.config_json` |
| Per-(model, horizon, regime) IC in `models_leaderboard.py` | 🟢 v17 | Emits `by_regime{}` sub-dict per row |
| Dashboard: REGIME chip + leaderboard regime filter (All / Current / dropdown) | 🟢 v17 | Updates from `current_regime` in payload |
| **Next:** widen regime axes — add rate-curve sign + credit spread bucket | ⚪ | After 2 weeks of base-regime data |
| **Next:** drift detection — alert when a model's IC drops 30%+ from trailing 90d mean | ⚪ | Belongs in `models_leaderboard.py`; emits to dashboard |

### Axis B — Hyperparameter sweep families (✅ first cut done)

| Item | Status | Notes |
|---|---|---|
| Convert fixed entrants into parameterized families | 🟢 v18 | 31 entrants, 11 families |
| Compute per-family champion (highest IC, n ≥ 50) | 🟢 v18 | Surfaces in collapsed UI |
| Dashboard: family rows collapse with champion + "▸ N more" badge | 🟢 v18 | Click to expand |
| **Next:** walk-forward champion auto-promotion (cron weekly) — write the promoted model to a `champions.json` consumed by downstream signals | ⚪ | Need 2 weeks of stable data per variant first |
| **Next:** statistical-significance gate — challenger must beat champion by IC ≥ 0.05 with p < 0.05 to promote | ⚪ | Belongs in the auto-promotion job |
| **Next:** add 2nd-tier sweeps — RSI threshold (20/80 vs 30/70 vs 35/65), Donchian/Bollinger lookback × multiplier crosses | ⚪ | Wait until 1st-tier sweeps prove signal |

### Axis C — Ensemble meta-model (next big ML step)

| Item | Status | Notes |
|---|---|---|
| Stage 1: Linear weight blending (`meta_signed_score`) — OLS on `realized_return ~ Σ wᵢ × model_iᵢ_score`, walk-forward refit weekly. New entrant in tournament. | 🔵 | Need ≥ 7 days of per-variant data. Target start: **May 19** |
| Stage 2: Logistic regression conviction — `P(sign(return)=+1)` from full feature vector; calibrated probability enables Kelly sizing | 🔵 | Need ≥ 4 weeks. Target start: **June 8** |
| Stage 3: Gradient-boost (LightGBM) with regime interactions — captures "model X works when VIX > 25" effects | 🔵 | Need ≥ 6 weeks. Target start: **June 22** |
| Stage 4: Regime-conditional weights — separate weight vector per regime, route at inference time | 🔵 | Need ≥ 8 weeks AND Stage 3 done. Target: **July 7** |
| Stage 5: RL (episodic backtest, reward = realized P&L net of costs) | 🔵 | Long horizon — 6+ months of data; only after Stages 1–4 prove out |

---

## Phase B — Data layers (markouts + new feeds)

| Item | Status | Notes |
|---|---|---|
| Intraday markouts (30m / 60m / 4h) | 🟢 | ~75K labeled pairs captured |
| Daily markouts (1d / 5d / 20d) — separate process joining predictions to EOD closes from SEP | ⚪ | One systemd timer + a join script. **Target: week of May 18** |
| Options chain data (IV, put-call ratio) — enables vol-regime classifier expansion | ⚪ | Source TBD (Tradier, Polygon, IBKR). **Target: June** |
| Rate curves beyond SPY/VIX/TLT — full FRED yield curve, 2s10s slope, credit spread | ⚪ | FRED API; free. **Target: June** |
| Economic data (ISM, PMI, unemployment, CPI surprise) — regime macro context | ⚪ | FRED + BEA. **Target: June** |
| Earnings calendar — flag tickers reporting this week for risk-off treatment | ⚪ | Finnhub has this; just need to ingest. **Target: late May** |

---

## Phase C — Price target engine

| Item | Status | Notes |
|---|---|---|
| Per-ticker growth assumptions (4 sliders, 6q LR baseline) | 🟢 v13 | Storage in `user_assumptions.json` |
| 1-page valuation report (📄 button, RCG navy/gold, Haiku 4.5 narration) | 🟢 v14–v15 | Print-to-PDF flow |
| Bull / Bear preset buttons for coherent multi-slider scenarios | 🟢 v15 | |
| Report extensions: EPS trend + news headlines + analyst coverage | 🟢 v15 | |
| Auto-generate daily reports for the top-10 highest-conviction names | ⚪ | Cron job, writes PDFs to `outputs/reports/YYYY-MM-DD/` |
| Multi-horizon PTs (12-month / 24-month / 36-month) — currently engine projects 4q only | ⚪ | Extend `PROJECTION_QUARTERS` to multiple horizons in parallel |
| DCF / WACC model added as 5th PT model (alongside EV/EBITDA, EV/Rev, FCF Yield, Emerging Growth) | ⚪ | Need WACC inputs (cost of equity, cost of debt) — pulls from rate curves above |

---

## Phase D — Dashboard / UX

| Item | Status | Notes |
|---|---|---|
| Top 40 with sortable cols + action labels | 🟢 | |
| Per-ticker drill-down (chart, stats, levels, catalysts, prediction history) | 🟢 | |
| Star / pin system + ad-hoc ticker entry | 🟢 v12 | |
| User-assumption sliders inline on detail row | 🟢 v13 | |
| Regime chip + leaderboard filter | 🟢 v17 | |
| Hyperparameter family collapse/expand | 🟢 v18 | |
| "How to read" interpretation guide | 🟢 v16 | |
| Mobile-responsive optimization | ⚪ | Sliders + leaderboard cramped on phone |
| Historical backtest UI — pick a date, see what the dashboard looked like then + realized P&L if you'd traded the top-N | ⚪ | After Phase B daily markouts |

---

## Phase E — Phase 2: PM-style agents

(Holds until quant-side exhaustion proves out — user's call to do that first)

| Item | Status | Notes |
|---|---|---|
| Define PM-agent input schema (style profile = factor tilts + regime prefs + concentration patterns) | ⚪ | Design doc first |
| Seed 3–5 PM-style agents from public 13F factor decompositions (Buffett-value, Tiger-growth, Renaissance-momentum) | ⚪ | After quant exhaustion |
| New `run_type='agent_score'` + `agent_type` column to differentiate quant vs pm_style vs discretionary | ⚪ | Schema migration |
| PM agents enter tournament alongside QR agents; same forward-return + IC pipeline | ⚪ | Reuses everything |
| **Phase 2.5:** structured deals with retired PMs for true alpha replication | 🔴 | Business dev, not engineering |

---

## Phase F — Capital allocator (the synthesis)

| Item | Status | Notes |
|---|---|---|
| LLM regime hypothesis layer — reads macro state + recent moves + headlines, outputs regime probability vector | ⚪ | Claude Haiku 4.5 already wired |
| Deterministic optimizer — takes (regime_probs × per-agent regime performance) → constrained mean-variance / risk-parity / Kelly weights | ⚪ | Once Phase A Axis C Stage 4 produces per-regime weights |
| Backtest harness — replay 6+ months of regime + agent scores, score the allocator's allocation choices | ⚪ | After daily markouts are flowing |
| Paper-trade wiring — IBKR or similar paper account, allocator output → orders | 🔴 | Wait until backtest validates |
| **Synthetic marketplace** — multi-agent simulation, anticipate flow + sentiment shifts | 🔴 | Multi-year R&D moonshot |

---

## Phase G — Operations / risk

| Item | Status | Notes |
|---|---|---|
| Sentiment-driven daily macro report (already exists at `market_sentiment_bbg.py`) | 🟢 | Could be enriched with regime breakdown |
| Postgres → GCS nightly backup with 30d lifecycle | 🟢 | Phase 2A Session 4 |
| Portfolio simulator (paper account) — track if you'd traded each day's top-3 names what would have happened | ⚪ | Needs Phase B daily markouts |
| Trade execution wiring (IBKR / Alpaca / etc.) | 🔴 | Only after paper backtest validates |
| P&L attribution by signal source — which model drove which winning/losing trade | ⚪ | Phase 2D pre-work |

---

## "When can we…?" timeline

A rough calendar based on data-accumulation gates. Each milestone has a min sample threshold before it's meaningful:

| Week | Data gate hit | Unlocks |
|---|---|---|
| **May 11** (now) | — | v13–v18 shipped |
| **May 14** | 2 days of v18 data (200+ samples per variant) | First meaningful per-family champion rotations visible |
| **May 18** | 1 week of v17 regime tags | Per-regime IC stratifications start to be statistically real |
| **May 19** | 7 days of per-variant data | **Stage 1 meta-model (linear blending)** becomes possible — kick off |
| **May 25** | 2 weeks of regime data | Walk-forward champion auto-promotion can ship |
| **June 1** | Phase B daily markouts live | Multi-horizon backtests + portfolio simulator |
| **June 8** | 4 weeks of data | **Stage 2 (logistic conviction)** kicks off |
| **June 22** | 6 weeks of data | **Stage 3 (gradient boost with regime interactions)** kicks off |
| **July 7** | 8 weeks of data | **Stage 4 (regime-conditional weights)** + start PM-style agent design |
| **August+** | Quant exhaustion complete | Phase E (PM-style agents) + Phase F (capital allocator) |

---

## How to update this file

When you ship something: change its status to 🟢 and tag the version (e.g. "v19").
When you decide a "next" item: change it to 🟡 and assign a target date.
When you discover something new should be on the list: append to the relevant Phase section as ⚪.
When you re-prioritize: just move items between Phases or rewrite this file. Git keeps history.

The CONTEXT files (`CONTEXT_signal_capture.md`, `CONTEXT_price_targets.md`) remain the source of truth for **state** — what's built and how it works. This file is the source of truth for **direction** — what's next and why.
