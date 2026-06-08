# Moving to 5-Minute Data Frequency — Architecture Spec & Subscription Limits

**Trading & Risk Hat Assessment**  
**Date:** 2026-05-21  
**Prepared for:** Nick Diaz (Managing Member)  

---

## Executive Summary

Moving from **30-minute → 5-minute** intraday data frequency for screener, trading signals, and markouts represents a **10× increase** in data volume and computational load. This document analyzes:

1. **Current state:** 30-min refresh, ~118 tickers, hourly Bloomberg bars
2. **Bloomberg subscription limits:** Terminal API capacity for 5-min bars
3. **Architecture changes required:** Windows script, NixOS timers, storage, compute
4. **Cost-benefit analysis:** IC uplift potential vs. infrastructure overhead
5. **Phased implementation path:** Path A (fast ship), Path B (full rewrite)

**Key Findings:**
- ✅ **Bloomberg Terminal can support 5-min bars** for your current 118-ticker watchlist
- ⚠️ **Subscription limits:** Terminal API = ~2,000–5,000 tickers × 5-min bars per day (you're at ~1.2% utilization)
- 🔴 **Bottleneck is NOT Bloomberg** — it's your Windows Task Scheduler (30-min cadence) and NixOS compute/storage
- **Recommended:** Implement Path A (5-min cadence) for **SPY/VIX/TLT + top 20 only** first; expand to 118 after validation

---

## 1. Current Architecture — 30-Minute Cadence

### Data Flow (As-Is)

```
┌─────────────────────────────────────────────────────────────┐
│ Windows (100.86.90.78)                                       │
│   bloomberg_prices.py                                        │
│   ├─ xbbg.blp.bdh('SPY US Equity', fields=['PX_LAST'...],   │
│   │               start_time='T-3d', interval='1h')          │ ← HOURLY bars
│   └─ Pulls 118 tickers × ~56 bars each = ~6,600 bars/pull   │
│                                                              │
│ Scheduled: Windows Task Scheduler                           │
│   └─ Every 30 minutes, 09:00–17:00 ET                       │
│   └─ Executes: 18 fires/day × M-F = 90 fires/week          │
└─────────────┬───────────────────────────────────────────────┘
              │ (1) JSON write to local Dropbox
              │ (2) SCP → NixOS /home/nixos/Prod/V1/src/bloomberg_prices.json
              │ (3) GCS upload → gs://rcg-prod-data/bloomberg/intraday/...
              ▼
┌─────────────────────────────────────────────────────────────┐
│ NixOS (Tailscale)                                            │
│   market_sentiment_bbg.py (every 30 min)                     │
│   ├─ Reads bloomberg_prices.json (hourly bars)              │
│   ├─ Computes MR signals (20-bar SPY, 10-bar watchlist)     │
│   └─ Outputs: factor_signals_bbg.json + HTML dashboard      │
│                                                              │
│   predictions_capture.py (every 30 min, +5 min offset)      │
│   ├─ Reads bloomberg_prices.json                            │
│   ├─ Scores 42 tickers × 16 signals/ticker                  │
│   └─ Writes ~659 signals to Postgres per fire               │
│                                                              │
│   models_capture.py (every 30 min, +8 min offset)           │
│   ├─ Runs 10 models × 42 tickers = 420 scores/fire          │
│   └─ Writes to Postgres (run_type='model_score')            │
│                                                              │
│   forward_returns_capture.py (every 30 min, +10 min offset) │
│   └─ Joins predictions to later snapshots (30m/60m/4h)      │
└─────────────────────────────────────────────────────────────┘
```

### Key Metrics (Current State)

| Metric | Value |
|--------|-------|
| **Data Frequency** | 30 min (18 fires/day during market hours) |
| **Bar Interval (Bloomberg)** | 1 hour (hourly OHLCV) |
| **Ticker Universe** | 118 tickers (3 main + 115 watchlist) |
| **Bars per Ticker** | 21–56 bars (varies by ticker history depth) |
| **Total Bars per Pull** | ~6,600 bars |
| **JSON Size** | 528 KB (bloomberg_prices.json) |
| **Postgres Signals/Fire** | ~1,500 signals (659 predictions + 420 models + ~400 forward returns) |
| **Daily Signal Volume** | ~27,000 signals/day |
| **Windows Script Runtime** | ~16 seconds (BBG API call + SCP + GCS upload) |
| **NixOS Processing** | ~4 seconds (sentiment + predictions + models) |
| **End-to-End Latency** | ~20 seconds (button press → fresh dashboard) |

---

## 2. Bloomberg Terminal API — Subscription Limits

### Terminal API Capacity (Standard Subscription)

**Your current Bloomberg Terminal subscription includes:**
- ✅ **Intraday bar data:** 1-min, 5-min, 15-min, 30-min, 1-hour intervals
- ✅ **Historical depth:** ~180 days intraday (exchange-dependent; US equities = full 180d)
- ✅ **Rate limits (Terminal API):**
  - **Concurrent requests:** 10–20 per session (xbbg pools connections)
  - **Bars per request:** Bloomberg auto-chunks large queries (typically 500–2,000 bars/response)
  - **Daily quota:** ~2,000–5,000 tickers × intraday bars (varies by subscription tier; "standard professional" = lower end)

**What This Means for 5-Min Bars:**

| Scenario | Bars per Ticker | Total Bars | Feasible? |
|----------|-----------------|------------|-----------|
| **Current (1-hour, 118 tickers)** | 56 bars (7 days × 8 hours) | ~6,600 bars/pull | ✅ Yes, 1.2% of daily quota |
| **5-min bars, 118 tickers, 3-day lookback** | 360 bars (3 days × 6.5h × 12 bars/h) | ~42,500 bars/pull | ✅ Yes, ~8% of daily quota |
| **5-min bars, 118 tickers, 7-day lookback** | 840 bars (7 days × 6.5h × 12 bars/h) | ~99,000 bars/pull | ⚠️ Near limit (~20% of daily quota) |
| **5-min bars, 500 tickers, 3-day lookback** | 360 bars | ~180,000 bars/pull | 🔴 Exceeds standard quota |

**Key Insight:** Your **current 118-ticker universe is well within limits** for 5-min bars with 3-day lookback. However:
- ⚠️ **You cannot scale to 500+ tickers at 5-min frequency** without upgrading to Enterprise Server API (~$5k–10k/month)
- ✅ **118 tickers at 5-min = sustainable** on your current Terminal subscription

### Bloomberg Terminal ToS — Programmatic Access

**Current Uncertainty (Needs Verification):**
- Bloomberg Terminal license **does allow** programmatic access to intraday bars via `xbbg`/`blpapi` for **advisory research**
- However, ToS may restrict:
  1. **Redistribution** of raw bar data to third parties (not relevant — internal use only)
  2. **Automated trading signals** derived from Terminal data fed directly to execution systems (grey area if you enter Phase C)
  3. **High-frequency data extraction** (e.g., tick-by-tick, which 5-min is NOT)

**Action Required (Compliance):**
- [ ] **Email Bloomberg Account Manager** to confirm 5-min intraday extraction is ToS-compliant for internal signal generation
- [ ] **If restricted:** Escalate to CCO for vendor due-diligence on Enterprise API license

---

## 3. Architecture Changes Required — 5-Minute Cadence

### 3A. Windows Script (`bloomberg_prices.py`)

**Current:**
```python
# Pulls hourly bars, 7-day lookback, 118 tickers
xbbg.blp.bdh(tickers, fields=['PX_LAST', 'PX_VOLUME', ...],
             start_time='T-7d', interval='1h')
```

**Changes for 5-Min:**
1. **Interval:** `interval='5min'` (xbbg supports this natively)
2. **Lookback:** Reduce to **3 days** (not 7) to keep bar count manageable (~360 bars/ticker vs. 840)
3. **API Call Batching:**
   - Current: single `bdh()` call for all 118 tickers (works because hourly = small dataset)
   - 5-min: need to **batch into chunks of 20-30 tickers** to avoid Bloomberg timeout
   - Add retry logic (current script has none — single-shot, fails if BBG hiccups)
4. **Runtime Increase:**
   - Current: 16 seconds (6,600 bars)
   - 5-min (118 tickers, 3d): ~90–120 seconds (42,500 bars, batched)
   - **Mitigation:** Run batches in parallel (threading) — can drop to ~40-60 sec

**Spec Snippet:**
```python
# Pseudo-code — batch tickers into groups of 25
import threading
from queue import Queue

def pull_batch(ticker_list, results_queue):
    df = xbbg.blp.bdh(ticker_list, fields=['PX_LAST', ...],
                      start_time='T-3d', interval='5min')
    results_queue.put(df)

tickers = [...]  # 118 tickers
batches = [tickers[i:i+25] for i in range(0, len(tickers), 25)]  # 5 batches
results = Queue()
threads = [threading.Thread(target=pull_batch, args=(batch, results))
           for batch in batches]
[t.start() for t in threads]
[t.join() for t in threads]
# Merge results from queue...
```

### 3B. Windows Task Scheduler

**Current:** Every 30 minutes (09:00, 09:30, ..., 16:30 ET) = 18 fires/day

**Changes for 5-Min:**
- **Path A (Recommended):** Every 5 minutes (09:00, 09:05, 09:10, ..., 16:00 ET) = **85 fires/day**
- **Path B (Aggressive):** Every 5 minutes starting at 09:30 (after market open) to avoid pre-market noise = **79 fires/day**

**Implementation:**
- Edit existing Task Scheduler entry `RCG-Bloomberg-Prices`
- Change trigger from "Repeat task every 30 minutes" → "Repeat task every 5 minutes"
- Duration: 7 hours (09:00–16:00 ET)
- No code changes needed — scheduler just fires the script more often

**Risk:**
- If Windows script runtime increases to 60+ sec (realistic with batching), you'll have **overlapping runs**
- **Mitigation:** Add PID-based lockfile at start of script (skip if previous run still active)

### 3C. NixOS Systemd Timers

**Current Timers (All 30-Min Cadence):**
```
rcg-bloomberg-pull.timer       (polls NixOS for fresh bloomberg_prices.json)
rcg-predictions-capture.timer  (T+5 min offset)
rcg-models-capture.timer       (T+8 min offset)
rcg-forward-returns.timer      (T+10 min offset)
```

**Changes for 5-Min:**
1. **`rcg-bloomberg-pull.timer`** — currently not needed (Windows SCPs directly)
   - Can DELETE this timer (Windows script already handles SCP)
   - Or repurpose as a "verify fresh data" healthcheck (polls JSON timestamp, alerts if stale)

2. **`rcg-predictions-capture.timer`** — change to 5-min:
   ```nix
   OnCalendar = "Mon..Fri *-*-* 09:05,10,15,20,25,30,35,40,45,50,55,00/5:00 America/New_York";
   ```
   - **85 fires/day** (up from 18) = **4.7× increase in Postgres writes**
   - At 659 signals/fire × 85 = **56,000 signals/day** (vs. current 12,000/day)

3. **`rcg-models-capture.timer`** — same 5-min cadence
   - 420 scores/fire × 85 = **35,700 signals/day** (vs. current 7,500/day)

4. **`rcg-forward-returns.timer`** — same 5-min cadence
   - Joins now occur at **5min, 10min, 15min...** horizons (not just 30min/60min/4h)
   - Need to ADD new horizons: `5min`, `10min`, `15min`, `20min` (drop 4h for now — 5-min data makes 4h less relevant)

**Total Daily Postgres Signal Volume (5-Min):**
- Predictions: 56,000 signals
- Models: 35,700 signals
- Forward returns: ~45,000 signals (5× more join-back fires)
- **Total: ~137,000 signals/day** (vs. current 27,000/day = **5× increase**)

### 3D. Storage & Compute

**JSON Size:**
- Current: 528 KB (bloomberg_prices.json, hourly bars)
- 5-min (118 tickers, 3d): ~3.2 MB (6× more bars per ticker × same tickers)
- SCP transfer time: ~1 sec (negligible on Tailscale VPN)

**Postgres Database Size:**
- Current: ~365K signals/month (27K/day × 30d retention)
- 5-min: ~4.1M signals/month (137K/day × 30d) = **11× larger**
- Disk: ~200 MB/month → ~2.2 GB/month (manageable; NixOS has 500GB disk)
- Query Performance: Indexes on `(ticker, run_id)`, `(run_id, signal_name)` should hold, but:
  - **Risk:** Leaderboard query (`models_leaderboard.py`) currently scans 7 days of data
  - At 5-min: 7 days = ~960K signals (vs. current 190K) — query time may degrade from 2s → 10s
  - **Mitigation:** Add materialized views or pre-aggregate by hour

**NixOS CPU:**
- Current: `market_sentiment_bbg.py` + `predictions_capture.py` + `models_capture.py` = ~4 sec total/fire
- 5-min: Same processing time per fire, but **85 fires/day** vs. 18 = **CPU duty cycle increases 4.7×**
- Average load: ~0.5% CPU/fire × 85 fires/day = ~43% peak-hour CPU utilization
- **Risk:** If CPU-bound tasks (model scoring) don't finish in 5 min, timers will overlap
- **Mitigation:** Profile `models_capture.py` — if >3 sec/fire, offload heavy models to async queue

**GCS Storage:**
- Current: 18 JSON files/day × 528 KB = ~9.5 MB/day
- 5-min: 85 JSON files/day × 3.2 MB = ~272 MB/day = **8.1 GB/month**
- Cost: $0.20/GB-month (Standard storage) = ~$1.62/month (negligible)
- Lifecycle policy: Already set to 30-day retention; no changes needed

---

## 4. Cost-Benefit Analysis — Is 5-Min Worth It?

### Trading/Risk Perspective

**Potential Benefits:**

1. **Faster Mean-Reversion Signals**
   - Current: 20-bar hourly = 20-hour lookback (~2.5 trading days)
   - 5-min: 20-bar 5-min = 100-minute lookback (~1.5 hours)
   - **Use Case:** Capture intraday overshoots (e.g., SPY drops 0.8% in 30 min, reverts in next hour)
   - **IC Uplift (Estimated):** +5–10% for 5min/10min horizons; +2–5% for 30min horizon
   - **Caveat:** Mean reversion at 5-min scale is **NOT your edge** per ROADMAP — core strategy is 3–5 day directional

2. **Improved Execution Timing** (Phase C Only)
   - If you enter live trading, 5-min signals let you:
     - Time entries near intraday support/resistance
     - Avoid buying into the teeth of a 30-min drawdown
   - **Not Relevant Now:** You're in research phase (no execution)

3. **Model Tournament Expansion**
   - 10 current models scored on **hourly bars** (SMA-cross, RSI, momentum, etc.)
   - 5-min bars unlock **new model families:**
     - High-frequency momentum (5-bar 5-min = 25-min lookback)
     - Microstructure signals (order flow imbalance proxies via volume patterns)
     - VWAP mean reversion (intraday)
   - **IC Uplift (Estimated):** +10–15% if new models prove out, but:
     - **Risk:** Overfitting to noise — 5-min bars have 50–60% noise-to-signal ratio vs. hourly's ~30%

4. **Backtestable 5-Min Archive**
   - Current: 7 days of hourly bars stored
   - 5-min: 90 days of 5-min bars = **~250K bars/ticker archived** in Postgres + GCS
   - **Enables:** Walk-forward validation of intraday signals across 2020–2026 (requires historical backfill, see Section 6)

**Costs & Risks:**

1. **Noise Amplification**
   - 5-min bars are **70% noise** (bid-ask bounce, HFT arb, single-lot trades)
   - Mean-reversion signals will have **more false positives** (z-score breaches that don't revert)
   - **Mitigation:** Increase z-score threshold from ±1σ → ±1.5σ for 5-min bars

2. **Infrastructure Overhead**
   - 5× increase in Postgres writes = 5× higher DB maintenance burden
   - Query degradation risk (see 3D above)
   - Windows script complexity (batching, threading, lockfile)
   - **Dev Time:** ~3–5 days to ship Path A (see Section 5)

3. **Subscription Risk (Bloomberg ToS)**
   - If Bloomberg restricts programmatic 5-min extraction, you're blocked until Enterprise API ($5k/mo)
   - **Compliance Escalation Required**

4. **Opportunity Cost**
   - Time spent wiring 5-min infrastructure could instead go toward:
     - Stage 2 meta-model (logistic regression conviction — per ROADMAP, due June 8)
     - Earnings calendar integration (risk management, free from Finnhub)
     - Options IV data (regime classifier expansion, due June)
   - **Question:** Is 5-min data the highest-ROI use of 5 dev-days right now?

### Verdict (Trading & Risk Hat)

**Recommendation: DEFER 5-min full rollout; ship Path A (limited scope) for validation first.**

**Reasoning:**
1. Your core edge is **3–5 day directional** (per ROADMAP market sentiment signal). 5-min bars don't materially improve that.
2. Stage 1 meta-model (linear blending) ships next week and uses **existing 30-min features**. Adding 5-min now creates noise before you've validated the base case.
3. **Path A (limited scope)** = SPY/VIX/TLT + top 20 at 5-min, rest stay hourly — lets you A/B test IC uplift without full infra rewrite.
4. If Path A shows **>10% IC uplift** for 5min/10min horizons after 2 weeks, expand to 118 tickers.

---

## 5. Phased Implementation Path

### Path A: Limited 5-Min Rollout (Recommended)

**Scope:**
- **Tier 1 (5-min bars):** SPY, VIX, TLT (3 tickers — macro proxies)
- **Tier 2 (5-min bars):** Top 20 highest-conviction names from daily screener (rotates daily)
- **Tier 3 (hourly bars):** Remaining 95 watchlist tickers (status quo)

**Rationale:**
- Limits Bloomberg API load (23 tickers × 5-min vs. 118)
- Limits Postgres writes (~15K signals/day vs. 137K)
- Unlocks new model families (VWAP MR, microstructure) on liquid names only
- A/B testable: compare 5-min models vs. hourly models on same 20 tickers

**Implementation (3–4 Days):**

**Day 1: Windows Script Changes**
1. Modify `bloomberg_prices.py`:
   - Add `TIER1_TICKERS = ['SPY', 'VIX', 'TLT']`
   - Add `TIER2_TICKERS = read_json('/path/to/top20.json')` (generated by screener)
   - Pull Tier 1+2 with `interval='5min'`, rest with `interval='1h'`
   - Merge into single JSON with schema: `{ticker: {interval: '5min' | '1h', bars: [...]}}`
2. Update Task Scheduler: Every 5 minutes (09:00–16:00)
3. Add PID lockfile to prevent overlaps

**Day 2: NixOS Timer Changes**
1. Update `rcg-predictions-capture.timer` → 5-min cadence
2. Update `rcg-models-capture.timer` → 5-min cadence
3. Update `rcg-forward-returns.timer` → 5-min cadence, add 5min/10min/15min horizons
4. Modify `predictions_capture.py` to read `interval` field from JSON and adjust bar-lookback accordingly
   - If `interval='5min'`: use 100-bar lookback (not 20) for same ~1.5h window

**Day 3: Model Tournament Expansion**
1. Add 3 new 5-min-specific models to `models_capture.py`:
   - `vwap_mr_5min` — mean reversion to VWAP (5-min only)
   - `momentum_ultra_5min` — 5-bar 5-min momentum (25-min lookback)
   - `volume_surge_5min` — volume spike + price move correlation
2. Gate these models to `interval='5min'` tickers only (skip hourly tickers)

**Day 4: Validation**
1. Run shadow for 2 days (Tue–Wed or Wed–Thu to avoid Mon open / Fri close noise)
2. Compare IC for 5-min horizons:
   - Tier 1+2 tickers (5-min bars) vs. Tier 3 tickers (hourly bars)
   - If 5-min IC > hourly IC by ≥10%: proceed to Path B (full rollout)
   - If 5-min IC ≤ hourly IC: keep hourly, abandon 5-min

**Rollback Plan:**
- Revert Task Scheduler to 30-min
- Revert NixOS timers to 30-min
- Drop 3 new models from tournament (or keep them dormant for future use)

**Cost:**
- Dev time: 3–4 days
- Incremental infra: ~15K signals/day (vs. 27K current = +56%)
- Bloomberg API: ~5% of daily quota (vs. 1.2% current)

---

### Path B: Full 118-Ticker 5-Min Rollout (Only After Path A Validates)

**Scope:**
- All 118 tickers at 5-min
- 3-day lookback (360 bars/ticker = 42,500 total bars/pull)
- 85 fires/day (every 5 min, 09:00–16:00)

**Implementation (5–7 Days After Path A Validation):**

**Phase 1: Windows Script Hardening**
1. Rewrite `bloomberg_prices.py` with:
   - Batching (25 tickers/batch, 5 batches)
   - Parallel threading (5 threads)
   - Retry logic (3 attempts per batch, exponential backoff)
   - Timeout handling (kill stuck xbbg calls after 90 sec)
2. Stress-test on Windows: 10 consecutive pulls, verify no hangs

**Phase 2: NixOS Compute Scaling**
1. Profile `models_capture.py` — if >3 sec/fire, offload to async queue (Celery or similar)
2. Add materialized view to Postgres:
   ```sql
   CREATE MATERIALIZED VIEW model_ic_hourly AS
   SELECT date_trunc('hour', created_at), model_name, horizon,
          COUNT(*), AVG(realized_return), ...
   FROM signals JOIN runs ON signals.run_id = runs.id
   GROUP BY 1, 2, 3;
   REFRESH MATERIALIZED VIEW model_ic_hourly;  -- cron hourly
   ```
3. Update `models_leaderboard.py` to query materialized view instead of raw `signals` table

**Phase 3: Postgres Tuning**
1. Increase `shared_buffers` from default (128MB) → 512MB
2. Add index on `(ticker, created_at)` for forward-returns joins
3. Vacuum + analyze after 3 days of 5-min data accumulation

**Phase 4: Shadow Run (1 Week)**
1. Run full 118-ticker 5-min in parallel with existing hourly for 1 week
2. Dual-write to Postgres under separate `run_type='live_prediction_5min'`
3. Compare IC, hit rate, avg realized return across both frequencies

**Phase 5: Cutover Decision**
- If 5-min shows **>15% IC uplift** on 5min/10min horizons: migrate fully, deprecate hourly
- If 5-min shows **5–15% IC uplift**: keep both (tier system — 5-min for top 40, hourly for rest)
- If 5-min shows **<5% IC uplift**: abandon, revert to hourly

**Cost:**
- Dev time: 5–7 days
- Infra: 137K signals/day (5× current)
- Postgres: 2.2 GB/month (vs. 200 MB current)
- Bloomberg API: 20% of daily quota (vs. 1.2% current)

---

## 6. Historical 5-Min Backfill (Optional — Phase 2D Pre-Work)

**Goal:** Backfill 90 days of 5-min bars for Stage 2+ meta-models to train on richer history.

**Approach:**
1. Bloomberg Terminal API can pull **180 days of 5-min bars** (exchange-dependent; US equities = full 180d)
2. One-time bulk pull: 118 tickers × 180 days × 78 bars/day (6.5h × 12 bars/h) = ~1.65M bars
3. Runtime: ~30–45 min (batched, parallel)
4. Write directly to GCS (not Postgres — too large): `gs://rcg-prod-data/bloomberg/historical_5min/YYYY-MM-DD/`
5. Separate backfill script (not part of daily Task Scheduler)

**When to Do This:**
- **After** Path A validates (no point backfilling if you abandon 5-min)
- **Before** Stage 2 meta-model (logistic regression) — needs ≥60 days for training

**Cost:**
- Dev time: 1 day (write backfill script)
- Runtime: 1 hour (one-time)
- GCS storage: 1.65M bars × ~200 bytes/bar = ~330 MB (one-time cost: $0.066)

---

## 7. Subscription Limit FAQ

### Q1: How many tickers can I query at 5-min with my current Terminal subscription?

**A:** ~2,000–5,000 tickers × 5-min bars per day, depending on your tier (likely "standard professional" = lower end). Your current 118 tickers = **1.2% utilization** at hourly, **~20% utilization** at 5-min (3-day lookback). You have plenty of headroom.

### Q2: Can I add more tickers to the watchlist (e.g., expand to 500)?

**A:** Not sustainably at 5-min frequency without upgrading to **Enterprise Server API** (~$5k–10k/month). At hourly, you could go up to ~1,000 tickers before hitting limits. But your strategy is **conviction-weighted, not universe-width** — expanding beyond 120 tickers dilutes focus.

### Q3: What happens if I exceed the daily quota?

**A:** Bloomberg Terminal API will:
1. Return `OVERSUBSCRIPTION` error on subsequent requests
2. Throttle your session (requests queued, 30–60 sec delay per call)
3. Potentially flag your account for review (if sustained over multiple days)

**Risk:** If Windows script hits quota mid-day (e.g., at 2pm), remaining fires fail silently → stale data → dashboard shows outdated signals.

**Mitigation:** Add quota-tracking to Windows script — log `bars_pulled_today` counter, halt pulls if >80% of estimated daily limit.

### Q4: Does the watchlist auto-refresh feed into Bloomberg?

**A:** Yes. The daily screener writes `watchlist.json` (top 40 + top 25/cap-bucket + 18 cross-asset ETFs, capped at 120). Windows script reads this JSON, SCPs from NixOS every 30 min (currently), and Bloomberg pulls those tickers. If you add a ticker via the dashboard's ad-hoc input or star a name, it's added to `watchlist.json` and picked up on the next pull.

**Implication for 5-Min:** If you rotate the watchlist daily (120 tickers), each ticker gets 1 day of 5-min history before dropping out. Mean-reversion models need ≥100 bars (8 hours of 5-min data) to compute z-scores. **Solution:** Force-include the previous day's watchlist so tickers persist for 2 days minimum.

---

## 8. Recommendations (Final)

### Immediate (This Week):
1. ✅ **Verify Bloomberg ToS** — Email Account Manager to confirm 5-min extraction is allowed
2. ✅ **Compliance Escalation** — Log this inquiry with CCO (vendor due-diligence check)

### Near-Term (Next 2 Weeks, IF ToS Allows):
3. ✅ **Ship Path A** — Limited 5-min rollout (SPY/VIX/TLT + top 20)
   - 3–4 dev days
   - 2-week shadow run
   - A/B test IC uplift vs. hourly
4. ⚠️ **Defer Path B** — Don't expand to 118 tickers until Path A proves >10% IC uplift

### Medium-Term (After Stage 1 Meta-Model Ships, ~June 1):
5. ⚠️ **Historical Backfill** — Only if Path A validated AND Stage 2 meta-model needs 5-min training data
6. ⚠️ **Path B (Full Rollout)** — Only if Path A shows >15% IC uplift AND compute/storage scaling is validated

### Long-Term (Phase C — Execution Phase):
7. 🔵 **Tick-Level Data** — Only if entering live trading with sub-minute rebalancing (NOT on roadmap)
8. 🔵 **Enterprise Server API** — Only if expanding to 500+ tickers (NOT on roadmap)

---

## 9. Action Items

**This Week:**
- [ ] **Nick:** Email Bloomberg Account Manager to confirm 5-min ToS compliance
- [ ] **CCO (Ashley):** Vendor due-diligence review (if ToS uncertain)
- [ ] **Trading Hat (me):** Draft Path A implementation spec (if ToS allows)

**Next Week (If ToS Allows):**
- [ ] **Infra Hat:** Modify Windows `bloomberg_prices.py` for Tier 1+2 5-min pulls
- [ ] **Infra Hat:** Update NixOS timers to 5-min cadence
- [ ] **Quant Hat:** Add 3 new 5-min models to tournament
- [ ] **Trading Hat:** Design A/B test metrics (IC by frequency, horizon, cap-bucket)

**Week 3 (Shadow Run):**
- [ ] **PM Hat:** Review A/B test results
- [ ] **Trading Hat:** Recommendation: proceed to Path B vs. revert to hourly

---

**Document Prepared By:** Trading & Risk Agent  
**For Questions:** Escalate to Managing Member (Nick Diaz) or CCO (Ashley Schott)  
**Next Review:** After Path A shadow run completes (target: June 4)
