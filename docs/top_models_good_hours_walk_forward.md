# Top-4 Models — Per-Hour Walk-Forward Audit
**Date:** 2026-05-28
**Models:** combo_meanrev, rsi_extreme_7, mean_rev_20, bollinger_pos_20
**Method:** 60/40 train/test split by date, derive good_hours on train, validate on test

---

## TL;DR

**All 4 models fail walk-forward on the good_hours filter.** Train shows +0.07 to +0.10 IC lift; test shows zero or negative lift on every one. **No `*_filtered` variants shipped.**

This is the same overfitting pattern that killed the per-ticker whitelists (see `docs/arima_20_filtered_walk_forward.md` and `docs/top_models_per_ticker_audit.md`).

| Model | Train base IC | Train good_hrs | Test base | Test good_hrs | Out-of-time lift | Verdict |
|---|---|---|---|---|---|---|
| combo_meanrev | +0.040 | +0.105 | +0.008 | **−0.026** | **−0.034** | ❌ overfit |
| rsi_extreme_7 | +0.038 | +0.076 | −0.001 | **−0.041** | **−0.039** | ❌ overfit |
| mean_rev_20 | +0.005 | +0.098 | +0.004 | −0.008 | −0.012 | ❌ overfit |
| bollinger_pos_20 | +0.004 | +0.098 | +0.004 | −0.008 | −0.012 | ❌ overfit |

---

## Why arima_20's good_hours worked but these don't

arima_20 in walk-forward: train good_hours IC +0.125 → test +0.167 (stable, real edge).
These 4 models: train good_hours IC +0.07-+0.10 → test goes NEGATIVE.

Two possible explanations:
1. **arima_20 has a genuine session-aware edge.** ARIMA forecasts depend on bar structure that differs by session (opening volatility, intraday drift, close jockeying). Mean-reversion models compute purely from recent price levels and lack a time-of-day-specific mechanism.
2. **arima_20's good_hours might also be partially overfit.** It generalized once across 3 train / 3 test days; that's a small sample. The 30-day re-validation harness will test this on more data.

---

## Mechanical explanation

When train data is small (~5 days), per-hour aggregates have wide confidence intervals. A hour with avg signed r30 of +0.20 on n=200 train samples might have a true population mean of −0.05; we're just observing the upper tail. Walk-forward catches this because the noise is uncorrelated across train and test.

Identical failure mode to the per-ticker whitelists.

---

## What ships from this work

**Nothing new in production.** The audit's value is documenting that the in-sample lift findings on these 4 models are overfit, so we don't ship `*_filtered` variants on inflated numbers.

Recommendation: re-run all these audits in 30 days when we have enough data per (model, hour) cell for stable estimation. Right now most cells have n ~100-1000 train samples — too thin.

---

## Open follow-ups
- 30-day re-validation harness (task #40) automates weekly re-runs
- Markout-sim backtest of arima_20_filtered (task #39) measures whether IC translates to P&L
- AB-test filtered variants in paper-trading before live commitment
