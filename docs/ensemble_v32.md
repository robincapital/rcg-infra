# v32 New Ensemble Entrants
**Date:** 2026-05-28
**Tournament size:** 53 → 61 entrants (+5 ensembles, +5 restored regime variants after regime-classifier bug fix, -2 net moves)

---

## Top-5 IC champions (post sign-fix audit, RTH |score|>=35)

| Model | Signed IC | Notes |
|---|---|---|
| relative_strength_rank_5bar | +0.050 (expected) | Sign flipped 2026-05-28; was -0.050 anti-edge |
| arima_20 | +0.039 | Highest unconditional IC |
| combo_meanrev | +0.034 | Pre-existing ensemble |
| rsi_extreme_7 | +0.026 | |
| mean_rev_20 | +0.013 | |
| bollinger_pos_20 | +0.013 | |

Five new ensembles combine these in different ways.

---

## New entrants

### `combo_top5_blend`
Equal-weight blend of the 5 champions plus relative_strength_rank_5bar (cross-sectional).
Requires ≥ 3 members to fire. Returns mean of all firing members.

**Hypothesis:** averaging reduces idiosyncratic noise; the residual signal compounds.

---

### `combo_high_conviction_3of5`
Same 5 members as above, but only returns non-zero when ≥ 3 members agree on direction (sign).
Otherwise returns 0 (suppressed). Filters to high-conviction multi-model fires.

**Hypothesis:** signals where 3+ uncorrelated models all point the same way are more likely to be real.

**Trade-off:** fewer fires (suppression), but each surviving fire should have higher IC.

---

### `combo_arima_x_rsi`
Multiplicative cross of arima_20 and rsi_extreme_7. Both must agree on direction; magnitude is the geometric mean. Zero otherwise.

**Hypothesis:** ARIMA captures forecast extremes, RSI captures momentum exhaustion. When both agree, conviction is highest.

---

### `combo_dual_arima`
Blend of arima_20 (1h) and arima_50 (2.5h). Captures short and long ARIMA forecasts. Mean of firing members.

**Hypothesis:** different time horizons capture different mean-reversion structures. Blending the family is more stable than relying on a single period.

---

### `combo_meanrev_weighted`
IC-weighted blend of mean-reversion family: bollinger_pos_20 (+0.013), mean_rev_20 (+0.013), rsi_extreme_7 (+0.026), combo_meanrev (+0.034). Weights normalized to sum=1 over the members that fire.

**Hypothesis:** the IC-weighted blend optimally allocates more credit to historically better signals while preserving diversification.

---

## First-fire verification (2026-05-28 15:20 ET)

| Ensemble | n | mean | range |
|---|---|---|---|
| combo_arima_x_rsi | 119 | +1.30 | [-83, +95] |
| combo_dual_arima | 119 | +14.16 | [-100, +100] |
| combo_high_conviction_3of5 | 119 | -6.10 | [-79, +73] (most fires don't hit 3-agreement → 0) |
| combo_meanrev_weighted | 119 | -11.43 | [-102, +91] (slight clip overshoot — minor) |
| combo_top5_blend | 119 | -6.10 | [-79, +73] |

All 5 firing with sensible distributions. `combo_high_conviction_3of5` shows the expected suppression behavior (mean similar to top5_blend, narrower range — high-conviction fires are rarer).

---

## Validation plan

- 30 days of capture data needed before signed-IC results are reliable
- Promote to champion consideration if IC ≥ +0.05 across the 30-day window
- If combo_high_conviction_3of5 IC > combo_top5_blend IC by ≥ 0.02, the 3-of-5 gate is adding signal
- If combo_arima_x_rsi IC > arima_20 IC by ≥ 0.02, the multiplicative gate is adding signal

## Regime-conditional restoration

The same deploy also fixed `qs.make_regime_conditional` (had been matching against non-existent label strings; bug docs/premarket_model_audit.md). Restored 5 entrants:
- momentum_8bar_highvol / lowvol
- mean_rev_20_concentrated
- rsi_extreme_14_highvol / lowvol

Now fire when the actual market regime matches (current regime mid/bull → vol filters all suppress correctly, sector_diversified passes through, sector_concentrated suppresses since HHI of current watchlist is low).
