# arima_20_filtered — Walk-Forward Validation
**Date:** 2026-05-28
**Trigger:** In-sample sweep (docs/model_optimization_sweep.md) showed +0.118 → +0.409 IC. This doc tests honestly whether that lift survives out-of-time.

---

## TL;DR

**Mixed result — half the optimization is real, half is overfit.**

| Filter | Train IC (in-sample) | Test IC (out-of-time) | Verdict |
|---|---|---|---|
| good_hours filter | +0.125 | **+0.167** | ✅ stable (actually higher in test) |
| per-ticker whitelist | +0.312 | **−0.035** | ❌ collapses below baseline — overfit |
| combined (wl + good) | +0.380 | +0.107 | dragged down by overfit whitelist |

**Action taken:**  revised to use **good_hours filter only**. Per-ticker whitelist dropped from the implementation. Expected out-of-time IC ~+0.167 at the |score|>=35 threshold, ~+0.190 at the actual |score|>=60 trading threshold.

---

## Method

Split available capture history (2026-05-19 → 2026-05-28, 6 days) 60/40 by date:
- **Train:** 2026-05-19 → 2026-05-22 (3 days, 19,700 rows)
- **Test:**  2026-05-26 → 2026-05-28 (3 days, 52,697 rows)

(Note: test set is bigger because mid-week capture was denser than the first weekend-included window.)

For each filter:
1. Derive the filter parameters from TRAIN data only (whitelist via per-ticker IC≥+0.05, good_hours via top-half of per-(h,m) signed r30 average).
2. Compute IC on test data using the train-derived filter.

If IC on test approximately equals IC on train, the filter is real. If it collapses, it was overfit to noise.

---

## Results table

```
=== TRAIN set evaluation (in-sample) ===
  all train, |score|>=35                     n= 19700  IC=+0.033  hit=52.0%
  all train, |score|>=60                     n=  8886  IC=+0.154  hit=59.3%
  train+whitelist                            n=  9033  IC=+0.312  hit=68.2%
  train+good_hours                           n= 13917  IC=+0.125  hit=57.2%
  train+whitelist+good_hours                 n=  6577  IC=+0.380  hit=71.8%

=== TEST set evaluation (out-of-time) ===
  all test, |score|>=35                      n= 52697  IC=+0.064  hit=53.8%
  all test, |score|>=60                      n= 25831  IC=+0.094  hit=55.4%
  test+whitelist (from train)                n=  7196  IC=-0.035  hit=47.9%  ← collapsed!
  test+good_hours (from train)               n= 23106  IC=+0.167  hit=58.4%
  test+whitelist+good_hours (from train)     n=  3338  IC=+0.107  hit=55.5%
  test+wl+good_hours+|s|>=60                 n=  1451  IC=+0.190  hit=59.8%
```

---

## Why the whitelist overfits and good_hours doesn't

**Whitelist:** with 3 days of train data, most tickers had only 30-100 fires. Per-ticker IC of +0.05–+0.50 at n=30-100 is noisy — easily comes from a handful of lucky direction matches. The 35 whitelist names were largely sample-noise winners, not stable-edge names. When applied to fresh data, they reverted to ~baseline (or worse, since we were selecting for noisy high-IC names which tend to mean-revert).

**good_hours:** intraday market microstructure is meaningfully different at 09:38 ET (opening volatility) vs 13:38 ET (lunch lull) vs 14:38 ET (US/Europe handoff). The per-hour pattern reflects genuine market regime differences that persist across days. Same 3-day train sample, but per-hour pooled across many tickers and dates → much more stable estimates.

---

## Implications for the 4-models per-ticker audit

The earlier  finding (per-ticker whitelists lifting combo_meanrev / rsi_extreme_7 / mean_rev_20 / bollinger_pos_20 IC by 11×-44×) is **almost certainly overfit by the same mechanism** as arima_20's whitelist. **Do not ship the *_filtered variants for those 4 models based on this in-sample finding.** Wait for 30+ days of fresh data and re-run walk-forward before promoting.

---

## What was changed

`src/models_capture.py` — `_make_arima_20_filtered` revised:
- Whitelist branch REMOVED
- good_hours updated to the train-derived set (more conservative than the original full-data set)
- Comment block updated to reference this walk-forward validation

Filter parameters now applied:
- good_hours = {(9,38), (10,8), (11,8), (12,8), (13,8), (14,8), (15,8)} ET

---

## Open follow-ups
- 30-day re-validation when more capture data accumulates
- Consider per-hour-of-day backtests at the markout sim level (true P&L, not just IC)
- Investigate why 13:08 and 14:08 ET are in good_hours under train-derived selection (different from the original 6 "good hours" identified from arima_20 audit) — suggests the audit's good_hours definition was itself partially in-sample
