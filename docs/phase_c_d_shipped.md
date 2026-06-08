# Phase C + Phase D — Shipped 2026-05-28

Closes the deferred items from `docs/bloomberg_expansion_spec_v2_note.md`. Both align with the MM-accepted DAPI override scope.

---

## Phase D — Watchlist expanded from 120 → 272 tickers

### Changes (`src/dynamic_factor_screener_v3.py`)
- `cap_picks` bumped from top-25/bucket → top-80/bucket (240 candidates across large/mid/small)
- New `wide_top_n` pool: top-200 by composite_score from the full screened universe
- `final_watchlist` cap raised from 120 → 350

### Result
- `outputs/watchlist.json` now has **272 tickers** (would be 350 but candidate pool exhausts at 272 with current Sharadar coverage + screener filters)
- 18 macros + screener top-40 + 3×80 cap_picks + 200 wide_top_n (heavy overlap) → 272 unique

### Downstream verification
- Windows `watchlist.json` synced via Dropbox at 13:43 ET
- `bloomberg_stream.py` watchdog (`WATCHLIST_REFRESH_S = 1800`) picked up the change; force-restarted for immediate verification
- Stream log confirms: `subscribed to 272 securities × 7 fields = 1904 concurrent fields`
- 1,904 is 54% of the published ~3,500 concurrent-field cap — comfortable headroom for further expansion to ~350-400 if candidate pool grows

### Universe composition (sample)
- First 10 (macros): `SPY VIX TLT QQQ IWM GLD SLV USO DBC UUP`
- Last 10 (tail of wide_top_n): `BBAI PD FSBC SHBI TRS OFG VBNK CIVB DCOM IIPR`

---

## Phase C — Trigger-only persistence in predictions_capture

### Why
Per `docs/bbg_dapi_override.md` (MM override on Bloomberg DAPI terms): we accepted "store only the parts being used on trade/markout when signals trigger" as the persistence policy. Previously, `predictions_capture.py` wrote ~16 BBG-derived signals per ticker every 30 min regardless of whether anything fired.

### Changes (`src/predictions_capture.py`)
- New `get_active_tickers(lookback_min, score_thresh)` queries Postgres for tickers with any `model_*_score` fire ≥ 60 absolute in the last 35 min
- New `ALWAYS_PERSIST` set: 18 macros (SPY, VIX, TLT, sector ETFs, etc.) bypass the gate so dashboards + regime tracking aren't starved
- Main loop now skips watchlist tickers not in `(active ∪ macros)`
- Run config logs `persistence_mode: "trigger_only_v32"`, `n_active_fires`, `n_persistent` for audit

### Result (test run at 17:52 UTC)
- Watchlist: 121 tickers (still 121 at the time of the test; will be 272 after next predictions_capture fire)
- Active fires (≥60): 92
- Macros: 18 (mostly overlap with active)
- **Persisted: 97** (was 121 = -19.8%)
- Skipped: 23
- Signals written: 1,380 (was 1,699 = -18.8%)

### Selectivity vs threshold (sample diagnostic)

| `|score|>=` | Active tickers |
|---|---|
| 35 | 119 |
| 50 | 110 |
| 60 | **92** ← chosen, matches `markout_eval.ENTRY_THRESHOLD` |
| 70 | 75 |
| 80 | 56 |

Chose 60 because it matches the simulator's trade threshold — tickers below 60 can't trade, so persisting their inputs is pure overhead.

### Expected behavior with 272-ticker watchlist

The active-fires count is dominated by tournament breadth (62 models). With more watchlist tickers but the same model count, expect:
- More candidates in the watchlist → more candidates the tournament can score
- Active count grows sub-linearly (some new names rarely fire above 60)
- Skipped count grows roughly linearly
- Estimated next-cycle persistence ratio: ~60-70% (was ~80% before this change)

---

## Risk + rollback

### Risk
- If `model_score` capture is delayed/broken and `get_active_tickers()` returns empty, predictions_capture falls back to macros-only (18 tickers). Wrapped in try/except so DB failure doesn't crash the capture.
- New ticker added to watchlist via screener that has NEVER fired yet would not be persisted at first run. Resolves itself on first model fire.

### Rollback
- Phase D: revert `dynamic_factor_screener_v3.py` `[:350]` → `[:120]` and bucket count `:80` → `:25`. BBG stream will resize on next watchdog cycle.
- Phase C: revert the active-tickers gate in `predictions_capture.py:main()` (one if-statement). All watchlist tickers persist again.

---

## Closes
- Task #22 — Phase D expand watchlist to 350 ✓ (achieved 272, candidate pool was the limiter not the cap)
- Task #26 — Phase C trigger-only persistence ✓
- `bloomberg_expansion_spec_v2_note.md` deferred items list
