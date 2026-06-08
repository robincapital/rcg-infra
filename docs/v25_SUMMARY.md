# v25 Tournament Expansion — EXEC SUMMARY

## The Ask
Expand tournament from **39 → 56 entrants** across **15 families** (3 new regime families added).

Add **daily markouts** (1d, 5d, 14d, 30d business-day horizons) to complement intraday 30min/60min/4h.

## What's New

### 17 New Entrants
1. **Enhanced Momentum (5)**:
   - Volume-confirmed (5bar, 13bar) — only fires when vol > 1.5× avg
   - 52-week range position — scale from 52wk low to high
   - Acceleration (2nd derivative) — captures inflection points
   - Multi-timeframe blend (3/8/21bar) — nested confirmation

2. **Enhanced Mean-Reversion (3)**:
   - Bollinger Band % — stretch measure vs raw z-score
   - RSI divergence — price/RSI divergence setups
   - Volume-spike fade — fade exhaustion moves

3. **Cross-Sectional PCA (3)**:
   - **PCA top-10 PT basket** — daily rebalanced on highest price-target upside, residual alpha extraction
   - **Sector ETF PC1** — macro regime signal from sector basket
   - **Sector ETF PC2** — growth/value rotation factor

4. **Regime-Conditional (6)**:
   - Momentum/MR/RSI variants that **zero out** in wrong regime
   - Proof of specialization → IC spikes during regime match

### Daily Markouts
- New script: `forward_returns_daily.py`
- Joins model scores with **Sharadar SEP EOD closes**
- Computes realized returns N business days forward (accounts for weekends/holidays)
- Backfills 90 days on first run
- **Runs daily at 8 AM ET** (systemd timer)

### Leaderboard Enhancements
- **7 horizons tracked**: 30min, 60min, 4h, **1d, 5d, 14d, 30d**
- **Sharpe ratio** added to metrics
- **3 new families**: `momentum_regime`, `mean_reversion_regime`, `rsi_extreme_regime`

## Files Changed
- `src/quant_signals.py` — +10 signal functions, +1 wrapper factory
- `src/models_capture.py` — +17 entrants in MODELS list
- `src/forward_returns_daily.py` — NEW (daily markout script)
- `src/models_leaderboard.py` — +4 horizons, +Sharpe, +3 families
- `systemd/rcg-forward-returns-daily.*` — NEW (timer + service)
- `docs/v25_tournament_expansion.md` — NEW (full spec)

## Rollout Plan

### Phase 1: Code Deploy (today)
1. Merge to main (after your "deploy" verb)
2. Backfill daily returns: `python3 src/forward_returns_daily.py`
3. Enable systemd timer

### Phase 2: Signal Warmup (30 days)
- New entrants generate predictions daily
- Daily markouts accumulate sample depth
- Leaderboard shows live IC as samples grow

### Phase 3: IC Analysis (60 days)
- Regime-conditional variants should show **IC divergence** by regime
- Volume-confirmed momentum should beat base momentum_5bar
- Cross-sectional PCA signals stabilize after universe rebalancing settles

## Risk Mitigations
1. **Syntax verified** — all files compile cleanly
2. **Idempotent** — daily markout script skips already-computed returns
3. **Fallback** — if Sharadar SEP unavailable, falls back to signals table `eod_close`
4. **Rollback** — comment out bad entrant in MODELS list, rerun capture

## Next: Stage-1 Meta-Model (July 2026)
Once 39 entrants have 60+ days of daily data:
- IC matrix → identify orthogonal signal clusters
- OLS meta-blend on top-N uncorrelated entrants
- Per-regime weighting (regime-conditional signals get boosted during their target regime)

---

**Decision Point**: Does this expansion align with your vision for the tournament?

Ready to proceed on your "ship it" / awaiting "revise" feedback.
