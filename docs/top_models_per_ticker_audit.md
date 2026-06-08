# Top-4 Models — Per-Ticker IC Audit (with overfitting caveat)
**Date:** 2026-05-28
**Models analyzed:** combo_meanrev, rsi_extreme_7, mean_rev_20, bollinger_pos_20
**Sample:** Full available capture history, |score| >= 35

---

## TL;DR

In-sample per-ticker whitelists show **massive IC lifts (11×-44×)** on all four models. But walk-forward validation on the analogous arima_20 finding (see `docs/arima_20_filtered_walk_forward.md`) showed per-ticker whitelists **collapse from +0.312 train IC to −0.035 test IC** — overfit to sample noise.

**Implication: the per-ticker lifts below are almost certainly overfit. Do NOT ship *_filtered variants for these 4 models on this finding.** Wait for 30+ days of fresh data and re-run walk-forward before any promotion.

---

## In-sample results (KNOWN OVERFIT)

| Model | Baseline IC | Whitelist size | Whitelist IC | Lift | Blacklist size | Blacklist IC |
|---|---|---|---|---|---|---|
| combo_meanrev | +0.019 | 92 names | +0.224 | 11.6× | 62 names | −0.128 |
| rsi_extreme_7 | +0.013 | 95 names | +0.187 | 14.1× | 58 names | −0.139 |
| mean_rev_20 | +0.004 | 98 names | +0.172 | 41.2× | 66 names | −0.127 |
| bollinger_pos_20 | +0.004 | 93 names | +0.171 | 43.9× | 63 names | −0.127 |

Per-model whitelist + blacklist JSONs saved to `outputs/{model}_per_ticker.json` for reference, NOT for live use until validated.

---

## Why we believe this is overfit

The arima_20 walk-forward result is direct evidence:
- arima_20 whitelist (58 names): train IC +0.312, test IC −0.035
- arima_20 good_hours filter: train IC +0.125, test IC +0.167 (stable)

The per-ticker whitelist completely failed to generalize. The good_hours filter (a structural per-hour effect, pooled across all tickers and dates) generalized cleanly. We expect the same pattern here: the 11×-44× lifts on combo_meanrev/rsi/mean_rev_20/bollinger_pos are mostly sample-noise selection on small per-ticker n.

A ticker with n=60-200 fires across 10 days has too few samples for stable per-ticker IC estimation. The 90-95 names labeled "whitelist" are largely the high-IC tail of a noisy distribution that should regress toward the mean out-of-sample.

---

## What WOULD be tradeable

Two cleaner signals from this analysis:

1. **Avoid the blacklist names where IC is consistently negative across many fires.** Models that score these names should be down-weighted in the markout sim. (Same risk of overfitting but the downside is smaller — at worst we miss some trades.)

2. **Run a TIME-OF-DAY analysis on the other 4 models** analogous to the arima_20 hour audit. The good_hours filter is the optimization that survived walk-forward; check if the other 4 models have similar structure. This is the next concrete step worth taking.

---

## Recommendation

1. **DO NOT** ship `combo_meanrev_filtered`, `rsi_extreme_7_filtered`, `mean_rev_20_filtered`, `bollinger_pos_20_filtered` on this audit. Same overfitting risk as arima_20's dropped whitelist.

2. **DO** run per-hour analysis on these 4 models (next follow-up). If any of them show stable good_hours/bad_hours patterns (replicated train→test), the time-of-day filter is a candidate for production.

3. **Re-run this audit in 30 days** with substantially more data per ticker. Per-ticker IC stability requires n ≥ 200-500 per ticker — current sample averages ~100. By then we'll have enough to do honest walk-forward per-ticker validation.

---

## Saved artifacts (reference, not for live use)

- `outputs/combo_meanrev_per_ticker.json`
- `outputs/rsi_extreme_7_per_ticker.json`
- `outputs/mean_rev_20_per_ticker.json`
- `outputs/bollinger_pos_20_per_ticker.json`
- `outputs/arima_20_per_ticker.json` (from earlier — also overfit; superseded by good_hours-only filter)
