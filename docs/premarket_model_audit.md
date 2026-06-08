# Pre-market & After-Hours Model Audit
**Date:** 2026-05-28
**Status:** First-pass complete
**Scope:** 53 tournament model entrants in `model_*_score`, last 14 days of capture
**Triggered by:** MM question on extending capture timers to extended hours

---

## TL;DR

**Distribution similarity is misleading.** 32 of 53 models produce similar-looking *score distributions* in pre-market vs RTH, BUT when you check **predictive value (signed IC)**, most LOSE their edge or reverse direction outside RTH.

**Headline IC comparison** for top-traded models (signed IC, |score| ≥ 35 fires only):

| Model | Pre-market IC | RTH IC | After-hours IC | Verdict |
|---|---|---|---|---|
| **arima_20** | **−0.082** ❌ | +0.039 ✅ | **+0.033 ✅ 67% hit** | After-hours edge real; pre-market reversed |
| bollinger_pos_20 | −0.004 ≈0 | +0.013 small ✅ | −0.012 ≈0 | RTH only |
| mean_rev_20 | −0.004 | +0.013 | −0.012 | RTH only |
| rsi_extreme_7 | −0.023 ❌ | +0.026 ✅ | −0.015 ❌ | RTH only |
| combo_meanrev | −0.003 | +0.034 ✅ | −0.005 | RTH only |
| relative_strength_rank_5bar | −0.020 | −0.050 | −0.004 | Broken — investigate separately |

**Implication for the timer-extension question (MM ask 2026-05-28):**

Extending capture timers to 4 AM – 8 PM ET would store pre-market fires that the existing models cannot reliably trade on. The data is real, but at most 1-2 models retain positive edge outside RTH.

---

## Recommendation

1. **Keep capture timers RTH-only for now** (MM's existing decision is correct).
2. **Approve narrow exception for after-hours arima_20** (IC +0.033, 67.2% hit rate, large sample) — worth a manual extension once we understand why.
3. **Don't extend the other 31 "distribution-safe" models** to pre-market until each has been retrained or re-validated on extended-hours data.
4. **Decommission the 5 dead models** (separate cleanup).
5. **Investigate `relative_strength_rank_5bar`** — IC is negative across all sessions including RTH, which suggests a bug.

---

## Methodology

Pulled all `model_*_score` signals from the last 14 days, bucketed by session:
- **pre_open**: ET hour < 9, OR (hour == 9 AND minute < 30)
- **rth**: 9:30 ET – 16:00 ET
- **after_hours**: 16:00 ET onwards

For each (model, session):
1. Score distribution: n, mean, std, fire-rate (|score| ≥ 60), zero-rate
2. Signed IC: pair score with realized 30-min forward return at the same 10-min bucket, on the same ticker, restricted to |score| ≥ 35 (the actual trading universe per the markout sim rule). IC = mean(sign(score) × sign(return)).
3. Hit-rate: fraction of (score, return) pairs where the signs match (only counting non-zero returns).

Source: `signals` + `runs` tables, JOIN on `run_id`, bucketed via floor(epoch / 600) for cross-signal alignment.

---

## Classification results (distribution-only — see IC caveat above)

| Bucket | Count | Models |
|---|---|---|
| **extend_safe** (distribution) | 32 | arima_20, bollinger_pos_20, bollinger_pos_20_k25, combo_meanrev, combo_trend, donchian_break_10, donchian_break_20, ema_cross_8_21, ema_cross_12_26, kalman_trend_20, lr_slope_10, lr_slope_20, mean_rev_10, mean_rev_20, mean_rev_20_diversified, mean_rev_bb_pct_20, mean_rev_rsi_divergence, meta_blend_30min, meta_blend_60min, momentum_3bar, momentum_5bar, momentum_8bar, momentum_13bar, momentum_21bar, momentum_acceleration_8bar, momentum_multi_timeframe_blend, pca_residual_mr, relative_strength_rank_5bar, rsi_extreme_7, rsi_extreme_14, rsi_extreme_21, sma_cross_5_20 |
| **extend_with_calibration** | 4 | mean_rev_volume_spike_fade (zero std in pre), momentum_vol_confirmed_5bar (zero-rate 32%→94% pre→RTH), momentum_vol_confirmed_13bar (same), sector_relative_momentum (mean sign-flips) |
| **no_pre_data** | 3 | ar2_forecast_30, arima_1, ou_halflife_30 |
| **dead** (never fire) | 5 | mean_rev_20_concentrated, momentum_8bar_highvol, momentum_8bar_lowvol, rsi_extreme_14_highvol, rsi_extreme_14_lowvol |
| **insufficient_data** (< 50 RTH samples) | 9 | arima_50, bb_squeeze_breakout_20, bollinger_pos_50, donchian_break_55, ema_cross_20_50, hurst_20, lr_slope_40, mean_rev_40, sma_cross_10_50 |

**See the IC table above to override the `extend_safe` verdict on a per-model basis.**

---

## Why distribution similarity fails as a proxy for tradability

A model that produces uniformly-shaped score distributions in both sessions may still encode:
- A regime that is only valid during RTH (e.g., a momentum signal trained on RTH mean-reversion behavior fails when applied to thin pre-market order books)
- A timing pattern that misaligns with realized returns outside RTH (e.g., the r30 forward window during pre-market is dominated by the open-at-9:30 jump, which doesn't have the same structure as mid-day r30)
- Random noise that happens to fall in a similar histogram

Predictive IC is the only honest test. Distribution similarity tells you the model **doesn't crash** in pre-market; it doesn't tell you the signal is **useful**.

---

## What we already have in the DB (corrects my earlier overstatement)

I told you earlier that pre-market backtesting wasn't possible because we don't capture pre-market data. That was wrong. Confirmed by query:

| Session | r30 returns captured | r60 returns | live_price |
|---|---|---|---|
| pre_open | 19,874 | 14,280 | 22,722 |
| rth | 242,749 | 241,077 | 250,937 |
| after_hours | 67,003 | 45,062 | 89,176 |

We have ~22K pre-market and ~89K after-hours observations, coming from the 09:08 ET and 17:08/17:38 ET fires that happen to fall outside RTH. **Limited coverage** (1-2 fires per ticker per day in pre-market) but enough to do the IC analysis above.

Extending the timers to 4 AM – 8 PM ET would give us ~10× more pre-market data points per ticker per day. That density increase only matters if there's signal worth catching.

---

## Open items

- [ ] Investigate `relative_strength_rank_5bar` negative-IC bug across all sessions
- [ ] Decommission or fix the 5 "dead" models
- [ ] Look at why `arima_20` works after-hours (IC +0.033, 67% hit) — if real, this is the one extension worth doing now
- [ ] Re-run this audit with 60+ days of data once we have it
- [ ] Per-model regime-aware IC: split by VIX bucket / SPY direction to see if there's a tradable pre-market regime
- [ ] Consider training a separate model class for pre-market mechanics (different bar conventions, gap risk, thin order books)
