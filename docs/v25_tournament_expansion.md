# v25 Tournament Expansion — May 20 2026

## Overview

This expansion takes the tournament from 39 entrants (post-v24) to **56 entrants** across **15 distinct families** (3 new regime families added).

New additions:
- **5 enhanced momentum variants** (volume-confirmed, 52-week position, acceleration, multi-timeframe)
- **3 enhanced mean-reversion variants** (Bollinger %, RSI divergence, volume-spike fade)
- **1 PCA top-10 price-target basket** (daily rebalanced cross-sectional)
- **2 sector ETF PCA signals** (PC1 = macro regime, PC2 = growth/value rotation)
- **6 regime-conditional variants** (momentum/MR/RSI × high/low volatility or sector concentration)

### Daily Markouts

Added **4 new daily-horizon forward returns** (1d, 5d, 14d, 30d business days) computed via `forward_returns_daily.py`.

This script:
- Runs daily at 8 AM ET (after Sharadar morning pull)
- Joins model scores with Sharadar SEP EOD closes
- Computes realized returns N business days forward
- Records as `realized_return_<horizon>d_pct` signals
- Backfills 90 days on first run

### Leaderboard Enhancements

- **7 horizons tracked**: 30min, 60min, 4h, 1d, 5d, 14d, 30d
- **Sharpe ratio** added to metrics per model × horizon × regime
- **3 new signal families** tagged: `momentum_regime`, `mean_reversion_regime`, `rsi_extreme_regime`

---

## New Signals Detail

### Enhanced Momentum Variants

#### 1. `momentum_vol_confirmed_5bar` & `momentum_vol_confirmed_13bar`
- **Hypothesis**: Volume confirmation reduces whipsaw / increases hit rate on breakouts
- **Mechanism**: Only fires when current volume > 1.5× trailing 20-bar avg
- **Signal**: price return over lookback period (5 or 13 bars), clipped ±100
- **Family**: `momentum`

#### 2. `momentum_52wk_range_position`
- **Hypothesis**: Position within 52-week range predicts continuation (upper = strong, lower = weak)
- **Mechanism**: `(price - 52wk_low) / (52wk_high - 52wk_low) × 200 - 100`
  - +100 = at 52wk high
  - -100 = at 52wk low
  - 0 = mid-range
- **Family**: `momentum`

#### 3. `momentum_acceleration_8bar`
- **Hypothesis**: 2nd derivative (acceleration) captures inflection points better than raw slope
- **Mechanism**: `(recent 3-bar slope) - (prior 5-bar slope)`
  - Positive = accelerating up
  - Negative = decelerating
- **Family**: `momentum`

#### 4. `momentum_multi_timeframe_blend`
- **Hypothesis**: Nested confirmation across timeframes reduces false signals
- **Mechanism**: Weighted blend of 3bar (50%), 8bar (30%), 21bar (20%) returns
  - Full signal if all three agree in sign
  - Dampened 50% if signs don't align
- **Family**: `momentum`

---

### Enhanced Mean Reversion Variants

#### 5. `mean_rev_bb_pct_20`
- **Hypothesis**: Bollinger Band position is a better stretch measure than raw z-score
- **Mechanism**: `-(price - BB_mid) / (BB_upper - BB_lower) × 100`
  - Stretched up (near upper band) → negative (bearish MR)
  - Stretched down (near lower band) → positive (bullish MR)
- **Period**: 20 bars, 2.0 stddev bands
- **Family**: `mean_reversion`

#### 6. `mean_rev_rsi_divergence`
- **Hypothesis**: RSI divergences are high-conviction entry points for MR
- **Mechanism**: Detects bullish/bearish divergences over 5-bar lookback
  - Bullish: price lower low + RSI higher low → +50
  - Bearish: price higher high + RSI lower high → -50
  - Else → 0
- **Family**: `mean_reversion`

#### 7. `mean_rev_volume_spike_fade`
- **Hypothesis**: Volume spikes on extreme moves are exhaustion signals → fade
- **Mechanism**:
  - Trigger: volume > 2.0× avg AND price move > 2.0× stddev
  - Signal: `-sign(price_move) × spike_intensity` (clipped ±100)
- **Family**: `mean_reversion`

---

### PCA Top-10 Price Target Basket

#### 8. `pca_top10_pt_basket`
- **Hypothesis**: PCA residual on high-conviction (by price target) stocks isolates alpha
- **Universe**: Top-10 tickers by `internal_target / last_price`, filtered by `composite_score > 2.5`
- **Rebalance**: Daily (universe is dynamic)
- **Mechanism**:
  1. Compute 5-bar log returns for top-10
  2. PCA decomposition → PC1 = market component
  3. Residual = total return - PC1 explained
  4. z-score residuals
  5. Signal = `-z_residual × 30` (mean-reversion on idiosyncratic component)
- **Family**: `cross_sectional`

---

### Sector ETF PCA Signals

#### 9. `sector_etf_pc1`
- **Hypothesis**: Sector ETF basket PC1 loading captures macro regime (risk-on/risk-off)
- **Universe**: 11 sector ETFs (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLRE, XLB, XLU, XLC)
- **Mechanism**:
  1. PCA on 5-bar returns of sector ETFs
  2. PC1 loading × singular value × 1000 = macro score
  3. Map to all tickers via their sector
- **Family**: `cross_sectional`

#### 10. `sector_etf_pc2`
- **Hypothesis**: PC2 captures growth vs value rotation (orthogonal to market beta)
- **Mechanism**: Same as PC1 but uses PC2 loading
- **Family**: `cross_sectional`

---

### Regime-Conditional Variants

These wrap existing signals and **dampen or zero out** when regime doesn't match.

Regime filters:
- `high_vol`: regime in {`high_volatility`, `crisis`}
- `low_vol`: regime in {`low_volatility`, `grind_up`}
- `sector_concentrated`: top sector > 70% of watchlist
- `sector_diversified`: top sector ≤ 70% of watchlist

#### 11–16. Six Regime-Conditional Entrants

| Entrant | Base Signal | Regime Filter | Family |
|---------|-------------|---------------|--------|
| `momentum_8bar_highvol` | momentum_8bar | high_vol | `momentum_regime` |
| `momentum_8bar_lowvol` | momentum_8bar | low_vol | `momentum_regime` |
| `mean_rev_20_concentrated` | mean_rev_20bar | sector_concentrated | `mean_reversion_regime` |
| `mean_rev_20_diversified` | mean_rev_20bar | sector_diversified | `mean_reversion_regime` |
| `rsi_extreme_14_highvol` | rsi_extreme_14bar | high_vol | `rsi_extreme_regime` |
| `rsi_extreme_14_lowvol` | rsi_extreme_14bar | low_vol | `rsi_extreme_regime` |

**Hypothesis**: Signals work better (higher IC, lower variance) when constrained to their natural regime

---

## Files Modified

### `src/quant_signals.py`
- Added 10 new signal functions (momentum_vol_confirmed, momentum_52wk_range_position, momentum_acceleration, momentum_multi_timeframe_blend, mean_rev_bb_pct, mean_rev_rsi_divergence, mean_rev_volume_spike_fade, pca_top10_pt_basket, sector_etf_pc1, sector_etf_pc2)
- Added `make_regime_conditional()` wrapper factory
- Enhanced `build_universe_context()` to compute:
  - PCA top-10 residuals (daily rebalanced)
  - Sector ETF PC1/PC2 scores (mapped to all tickers)

### `src/models_capture.py`
- Added 17 new entrants to `MODELS` list (5 momentum + 3 MR + 1 PCA + 2 sector ETF PCA + 6 regime-conditional)
- Updated context building to pass `regime_label` and `regime_meta` for regime-conditional wrappers

### `src/forward_returns_daily.py` (NEW)
- Standalone script to compute daily-horizon forward returns (1d, 5d, 14d, 30d)
- Joins model scores with Sharadar SEP EOD closes
- Accounts for business-day calendar (skips weekends/holidays)
- Backfills 90 days on first run
- Idempotent (skips already-computed returns)

### `src/models_leaderboard.py`
- Updated `HORIZONS` to include daily horizons: `["30min", "60min", "4h", "1d", "5d", "14d", "30d"]`
- Added **Sharpe ratio** computation to `_metrics()` function
- Enhanced `family_from_model()` to recognize 3 new families:
  - `momentum_regime`
  - `mean_reversion_regime`
  - `rsi_extreme_regime`

### `systemd/rcg-forward-returns-daily.timer` (NEW)
- Systemd timer to run daily at 8:00 AM ET

### `systemd/rcg-forward-returns-daily.service` (NEW)
- Systemd service definition for daily markout script

---

## Deployment Checklist

1. **Code review** ✓ (this doc)
2. **Syntax verification** ✓ (`py_compile` all modified files)
3. **Database readiness**:
   - Ensure Sharadar SEP table is available and populated
   - Verify `sep` table has columns: `ticker`, `date`, `close`
   - If not, fallback to `signals` table `eod_close` signal (already implemented)
4. **Backfill daily returns**:
   ```bash
   python3 /home/nixos/Prod/V1/src/forward_returns_daily.py
   ```
5. **Enable systemd timer** (INFRA HAT — after Nick's "deploy" approval):
   ```bash
   systemctl --user enable rcg-forward-returns-daily.timer
   systemctl --user start rcg-forward-returns-daily.timer
   ```
6. **Models capture fire** (generates new signals):
   - Manual first run or wait for next scheduled fire
   - Check logs for new entrants in leaderboard output
7. **Leaderboard refresh**:
   ```bash
   python3 /home/nixos/Prod/V1/src/models_leaderboard.py
   ```
8. **Dashboard verification**:
   - Confirm 39 total entrants appear
   - Verify 7 horizons in leaderboard JSON
   - Check Sharpe ratio column is populated
   - Verify regime-conditional variants show `0.0` scores in wrong regimes

---

## Tournament Summary

### Pre-v25 (39 entrants after v24)
- 12 families including pattern (3), cross_sectional (3) from v24

### Post-v25 (56 entrants across 15 families)

| Family | Count | Notes |
|--------|-------|-------|
| momentum | 10 | +5 enhanced (vol-confirmed, 52wk, accel, MTF) |
| mean_reversion | 6 | +3 enhanced (BB%, RSI div, vol-spike fade) |
| rsi_extreme | 3 | Base (unchanged) |
| cross_sectional | 6 | +3 new (PCA top10, sector ETF PC1/PC2) |
| **momentum_regime** | **2** | **NEW** — high/low vol conditional |
| **mean_reversion_regime** | **2** | **NEW** — sector concentration conditional |
| **rsi_extreme_regime** | **2** | **NEW** — high/low vol conditional |
| sma_cross | 3 | Base (unchanged) |
| ema_cross | 3 | Base (unchanged) |
| bollinger_pos | 4 | +1 from v24 (BB squeeze) |
| donchian_break | 3 | Base (unchanged) |
| lr_slope | 3 | Base (unchanged) |
| arima | 4 | +1 from v24 (AR2 forecast) |
| ensemble | 2 | Base (unchanged) |
| pattern | 3 | From v24 (Hurst, Kalman, OU) |

### Key Metrics Growth
- **Entrants**: 39 → 56 (+44%)
- **Families**: 12 → 15 (+25%)
- **Horizons**: 3 → 7 (+133%)
- **Cross-sectional signals**: 3 → 6 (+100%)
- **Regime-aware signals**: 0 → 6 (NEW capability)

---

## Expected IC Impact

### Strong Hypotheses
1. **Volume-confirmed momentum** — should improve hit rate in liquid names (AAPL, MSFT, NVDA)
2. **Sector ETF PCA** — macro regime signal should stabilize during high-VIX periods
3. **Regime-conditional variants** — IC should spike during regime match, collapse during mismatch (proof of specialization)

### Moderate Hypotheses
4. **52-week range position** — likely correlated with existing momentum_13bar, may not add much orthogonal alpha
5. **BB % mean-reversion** — should outperform zscore_20 in sideways markets

### Speculative
6. **PCA top-10 PT basket** — high variance expected due to daily rebalancing; needs 60+ days to stabilize
7. **RSI divergence** — sparse signal (only fires on divergence setup); may have <50 samples in first 30 days

---

## Rollback Path

If any new signal crashes or degrades IC:

1. **Immediate**: comment out the entrant in `models_capture.py MODELS` list
2. **Rerun capture** → bad signal stops generating predictions
3. **Leaderboard** will still show historical data for the signal (allows post-mortem analysis)
4. **Optional**: add `WHERE signal_name != 'model_<bad>_score'` filter to leaderboard query to hide it

No database migration needed — new signals are additive only.

---

## Next Steps (Stage 1 Meta-Model)

Once the 56 entrants have 60+ days of daily markouts (mid-July 2026):

1. **IC matrix analysis** — compute pairwise correlations across all entrants
2. **Regime stratification** — verify regime-conditional variants spike IC in their target regimes
3. **Stage-1 meta-blend** — OLS on top-N orthogonal signals (see `v26_stage1_spec.md`)

This v25 expansion lays the groundwork for meta-learning by ensuring signal diversity and sufficient sample depth across all horizons.

---

**Status**: Ready for Nick's review → awaiting "ship it" or "revise" verb.
