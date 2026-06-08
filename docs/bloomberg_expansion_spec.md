# Bloomberg Data Expansion — Spec

**Date:** 2026-05-28
**Status:** Spec — awaiting MM approval
**Author:** RCG Quant Agent under MM direction

---

## Why

Current Bloomberg pull is **120 tickers × hourly bars × 7 fires/day = ~840 API calls/day**. The Terminal license can deliver vastly more — both more tickers and finer-grained data. Bigger universe + faster data enables shorter-horizon alpha models that currently do not have the inputs they need.

User asks (paraphrased):
1. Maximize what we can pull from a Terminal we already pay for
2. Higher frequency than 30 min
3. Larger ticker count than 120

---

## Standard Bloomberg Terminal API caps (published, approximate)

These are the commonly-cited Desktop API (DAPI) limits under a standard Terminal license. **Worth confirming at the terminal via `WAPI<GO>` or `DAPI<GO>` before committing to numbers near the cap.**

| Channel | Cap | What it controls |
|---|---|---|
| Real-time subscription (`//blp/mktdata`) | **~3,500 concurrent fields** | Live streaming of LAST_PRICE, BID, ASK, BID_SIZE, ASK_SIZE, VOLUME, VWAP, etc. Pushed by BBG as they tick. Best path for high-freq data. |
| `ReferenceDataRequest` (`//blp/refdata`) | ~50K hits/day, ~500K/month | Static-ish reference fields (sector, mktcap, dividend, etc.). One hit = one (security, field) tuple. |
| `HistoricalDataRequest` | ~500K hits/month | EOD/daily historicals. One hit = one (security, field, date). |
| `IntradayBarRequest` | ~50K requests/day | Each request returns many bars. Right tool for backfill / training data. |
| `IntradayTickRequest` | Heaviest quota burn | Every tick. Use sparingly — for execution-quality studies, not normal alpha pulls. |

**Key insight:** today's hourly `IntradayBarRequest` pattern burns ~840 hits/day to get coarse data that the **streaming subscription** model would give us in real-time for **free** (subscription counts against concurrent-field cap, not daily-hit cap).

---

## Proposed architecture — hybrid streaming + request/reply

```
                          ┌────────────────────────────┐
                          │      WINDOWS BBG HOST      │
                          │   long-lived BLPAPI proc   │
                          │                            │
                          │  ┌───────────────────────┐ │
                          │  │ SubscriptionSession   │ │       Real-time push
                          │  │  - tier1 universe     │ │  ─────────────────────►
                          │  │  - 7 fields/ticker    │ │
                          │  └───────────────────────┘ │       1-min snapshots
                          │  ┌───────────────────────┐ │  ────►  to JSON dump
                          │  │ RequestSession        │ │       to Dropbox + SCP
                          │  │  - IntradayBar backfill│ │       to NixOS + GCS
                          │  │  - daily ref data     │ │
                          │  └───────────────────────┘ │
                          └─────────────┬──────────────┘
                                        │ SCP / GCS
                                        ▼
                          ┌────────────────────────────┐
                          │    NixOS predictions /     │
                          │    screener / dashboard    │
                          └────────────────────────────┘
```

### Three data channels

1. **Streaming subscription** — live data
   - Subscribe to a universe (Tier 0 + Tier 1, see below) for a tight field list
   - BBG pushes updates as they happen; we hold them in memory
   - Snapshot to disk every **1 min** during RTH (configurable down to 10s)
   - Does not consume the daily-hit budget

2. **IntradayBarRequest** — historical backfill
   - For new tickers entering the universe: pull last 30 days of 1-min bars once
   - For training: bulk backfill 6-12 months of bars on a fresh universe
   - Counts against daily 50K request cap — keep under 5K/day for headroom

3. **ReferenceDataRequest** — static fields, daily
   - Sector, industry, GICS, market cap, shares outstanding, dividend
   - Once-daily refresh for the full Tier 2 universe (~3,000 names × 20 fields = 60K hits)
   - Borderline-OK at 50K daily cap; chunk across two runs if needed, OR rotate fields

---

## Ticker universe — three tiers

| Tier | Size | Cadence | Use |
|---|---|---|---|
| **Tier 0 — Trade book** | 20-40 | Sub-second (subscription, no snapshotting limit) | Names we would actively trade. Best price visibility. |
| **Tier 1 — Hot watchlist** | **300-500** | **1-min snapshots** | Powers short-term alpha models + predictions_capture. Promoted from Tier 2 daily by screener. |
| **Tier 2 — Screener universe** | **1,500-3,000** | EOD ref data only | Long screener candidate pool. Today bounded by Sharadar + Finnhub. |

### Sizing math

- Tier 1 = 500 tickers × 7 fields (last, bid, ask, bid_size, ask_size, volume, vwap) = **3,500 fields** — exactly at the concurrent-subscription cap. Trim to 6 fields (drop one size) if cap is tighter than published.
- Tier 1 = 500 × 1-min snapshots × 7 fields × 6.5 hrs = **136,500 signal rows/day** (vs current 12K). Postgres can handle this trivially (~5 MB/day raw).
- Tier 2 daily refresh = 3,000 tickers × 20 ref fields = 60K hits/day — chunk into 2 runs or rotate fields.

---

## Frequency tiering

| Cadence | Source | Coverage | Models powered |
|---|---|---|---|
| Stream (push) | BBG subscription | Tier 0 + Tier 1 | Real-time bid/ask spread, depth-of-book signals |
| **1-min snapshots** | Subscription dump | Tier 1 | Short-term momentum, micro-mean-reversion, VWAP-deviation, surge detection |
| 5-min snapshots | Resampled from 1-min | Tier 1 | Existing tournament entrants if 1-min proves too noisy |
| **15-min snapshots** | Resampled or BBG IntradayBar | Tier 1 + Tier 2 | Replaces today's 30-min predictions_capture |
| EOD | Sharadar + BBG ReferenceData | Tier 2 | Fundamentals, screener filters, sector/industry tags |

Short-term alpha (Phase 2B in Signal Capture roadmap) was always going to need ≤5-min data — this is the foundation.

---

## Phased rollout

### Phase 0 — Confirm caps at the terminal (you, ~30 min)
- On Windows, open BBG terminal, run `DAPI<GO>` and `WAPI<GO>`. Note the actual published quotas for your license tier (Personal Terminal, Professional, etc.). Drop into a doc.
- Confirm whether your subscription level allows full streaming or has tier restrictions.

### Phase 1 — Build streaming module (~2 days)
- New `bloomberg_stream.py` on Windows, alongside the existing hourly puller
- Long-running blpapi `Session` in subscription mode, subscribed to Tier 1 universe
- 1-min snapshot writer → JSON → SCP/GCS (same downstream as today)
- Reconnect logic on session drop
- Watchdog: if subscription stalls > 60 s, alert + restart
- **Keeps the existing hourly puller running** — zero risk to current dashboard
- Run for 1 week shadow to confirm stability + quota stays clean

### Phase 2 — Universe tiering (~1 day)
- Update screener (`dynamic_factor_screener_v3.py`) to emit `watchlist.json` with three tiers, not one flat list
- `bloomberg_stream.py` subscribes to Tier 1; existing puller continues for Tier 0/macro
- Promotion rule: top-500 by screener composite score, refreshed daily 04:30 ET

### Phase 3 — Predictions capture upgrade (~1 day)
- `predictions_capture.py` reads 1-min snapshots instead of hourly JSON
- Fire cadence stays 30 min for now (matches model_score cadence); intraday models added in Phase 4
- All current dashboards continue working

### Phase 4 — Short-term alpha models (~1 week, modeling)
- Add tournament entrants designed for 1-min bars: micro-momentum, VWAP-deviation, intraday-mean-reversion, order-flow proxies
- Backfill 30 days of 1-min bars per Tier 1 ticker via IntradayBarRequest (chunked, ~3K requests/day for 2 days)
- New `forward_returns` joins at 1/5/15-min horizons

### Phase 5 — Optional: tick-level study (one-off, ~2 days, quota-bounded)
- IntradayTickRequest on Tier 0 active book for a single day each month
- Studies: slippage realism, fill quality, depth-imbalance signal
- One-off, not continuous, to stay under the heavy tick quota

---

## Risks & open questions

| Risk | Mitigation |
|---|---|
| BBG subscription cap < 3,500 fields on this license tier | Phase 0 confirms before commitment. If tighter, reduce Tier 1 from 500 → 300 or drop a field. |
| Windows BBG box needs to stay logged in 24/7 | Already the case (Task Scheduler logon=Interactive). Add UPS-on-failure restart if not present. |
| Streaming session can drop silently | Watchdog + reconnect; alert to Slack on > 60s stall |
| Tailscale bandwidth | 500 tkrs × 7 fields × 1-min snapshots × ~50 bytes = trivial (~25 KB/snapshot, 1.6 MB/hour) |
| Postgres write rate (~150K signals/day) | Trivial vs current 352 MB DB |
| Postgres growth long-term | Add monthly archival rotation to GCS Parquet once table > 10 GB |
| Concurrent-field caps shared with Excel terminal use? | If user runs heavy BLP Excel formulas simultaneously, may hit cap. Operational discipline. |

---

## Compliance

- Bloomberg is on the approved vendor list (`rcg_policy.md` standing reference) — no new-vendor escalation.
- Terminal license allows personal/internal use; this stays internal (NixOS + GCS).
- No external distribution; §17 (Advertising) does not trigger.
- Dashboard UI is pass-through.

---

## Effort summary

| Phase | Effort | Blocking? |
|---|---|---|
| 0 | 30 min user time at BBG terminal | Yes — sizes everything downstream |
| 1 | 2 days (build + 1 wk shadow) | Then Phase 2/3 |
| 2 | 1 day | After Phase 1 |
| 3 | 1 day | After Phase 2 |
| 4 | 1 week modeling | After Phase 3 |
| 5 | 2 days, optional | Independent |

**Phase 0–3 = ~4-5 working days + 1 week shadow.** That gives you 1-min data on a 500-ticker universe with no risk to the current dashboard. Phase 4 is where the alpha-research starts.

---

## What I would recommend committing to today

1. **You do Phase 0** at the BBG terminal: 30 min to confirm actual quotas + license tier
2. **I start building Phase 1** in parallel — the streaming module is independent of the cap numbers, just needs them for sizing
3. After Phase 1 ships and shadows clean for a week, decide on full Phase 2-3 rollout

If you want, I can also start Phase 4 modeling design (model spec + features list) in parallel — it does not need the data online to write the spec.
