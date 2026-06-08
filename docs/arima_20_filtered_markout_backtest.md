# arima_20_filtered — Markout Backtest
**Date:** 2026-05-28
**Method:** Ran the production `markout_eval.simulate()` head-to-head on arima_20 unfiltered vs arima_20 with good_hours filter applied. Same 21-day lookback, same 5 bps/side slippage.

---

## TL;DR

The IC lift from good_hours filtering (+0.040 absolute on walk-forward test) translates to **essentially zero P&L improvement** in the trading sim. Cumulative net return drops 0.63pp; per-trade return is unchanged at +0.085%; Sharpe drops 2.5. Hit rate improves marginally (+1pp).

**Recommendation:** Keep `arima_20_filtered` in the tournament for ongoing measurement, but do **not** promote it over baseline arima_20 yet. The filter prunes signal without compensating downside.

---

## Results

| Metric | Baseline arima_20 | arima_20 + good_hours | Delta |
|---|---|---|---|
| n_fires | 80 | 40 | −50% |
| n_trades (round-trips) | 71 | 63 | −11% |
| hit_rate | 57.7% | 58.7% | +1.0pp |
| cum_return_gross | +6.50% | +5.81% | −0.69pp |
| cum_return_net | +5.98% | +5.35% | −0.63pp |
| sharpe_net | +13.83 | +11.34 | −2.49 |
| max_dd_net | −0.05% | −0.62% | worse |
| return per trade | +0.084% | +0.085% | ≈ flat |
| days in sim | 5 | 5 | — |

Filter kept 2,959 ticker-fires across 40 good-hour buckets; dropped 2,011 fires across 40 bad-hour buckets. Even bucket-count split despite uneven fire counts (good hours fire on more tickers per bucket).

---

## Why the IC lift doesn't translate to P&L

1. **The sim's entry threshold (|score| ≥ 60) is much higher than the IC measurement threshold (|score| ≥ 35).** High-conviction fires (|score| ≥ 60) likely already have similar hit-rates across all hours; the IC lift is concentrated in the 35-60 band that the sim doesn't trade on.
2. **The sim uses equal-weight 1/N position sizing.** Reducing fire density shrinks the active book proportionally; per-trade return is preserved but Sharpe drops because fewer concurrent positions = higher daily variance.
3. **Sample is thin** — 5 trading days of equity history. Both Sharpe estimates are noisy; the −2.49 delta is within typical 5-day Sharpe variance.
4. **The filter primarily reshapes entry timing**; exit logic is unchanged. r30-based marking holds positions through bad hours anyway.

---

## What this means for the tournament

`arima_20_filtered` was added to MODELS earlier today on the basis of the IC walk-forward result. This backtest shows the IC improvement is real but doesn't yet translate to a tradeable P&L edge.

Two paths forward:
- **Patient**: keep the entrant; wait 30 days of fresh data, re-run backtest, see if the cumulative effect emerges with larger sample
- **Skeptical**: decommission `arima_20_filtered` until we have evidence of P&L lift, not just IC lift

**Default action: patient.** The cost of keeping it is one extra row in the tournament; the cost of decommissioning + restoring later is friction. We'll re-evaluate in 30 days via the re-validation harness.

---

## Caveats

- 5 days of trading equity history is too short for statistical significance on Sharpe or max DD.
- The good_hours definition is itself derived from a small sample (10 days train). The walk-forward result that justified it (+0.125 → +0.167 IC) was the strongest single piece of evidence, but a single train/test cycle is not a robust validation.
- The sim was run on the existing arima_20 score history; this is what `arima_20_filtered` would produce post-filter. Once `arima_20_filtered` accumulates its own native fires (over the next 30 days), re-run this backtest on those.

---

## Open follow-ups
- Re-run this backtest in 30 days on native `arima_20_filtered` fires
- Sweep alternative good_hours definitions (e.g., top quartile vs top half, exclude only bad hours vs include only good)
- Run the same backtest at different slippage assumptions (0 bps, 10 bps) — current 5 bps may be masking the filter benefit
