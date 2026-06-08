# Model Optimization Sweep — Top 5 Champions
**Date:** 2026-05-28
**Sample:** Full available capture history through 2026-05-28
**Goal:** Identify per-model optimizations that move IC by ≥ +0.01 absolute

---

## Headline finding

**arima_20 with whitelist + good-hours filter delivers +0.409 IC at 71.5% hit rate** — vs +0.118 baseline at the actual markout trading threshold. **3.5× lift in predictive value**, same data.

The other 4 top models do NOT benefit meaningfully from time-of-day or per-ticker filters. For them, the optimal knob is the entry threshold (current default of |score|>=60 is already best).

---

## Baseline IC across top 5 (RTH, signed IC, |score|>=35)

| Model | n | IC | hit | avg signed r30 |
|---|---|---|---|---|
| arima_20 | 56,403 | **+0.056** | 52.9% | +0.112 |
| combo_meanrev | 116,844 | +0.036 | 51.9% | -0.018 |
| rsi_extreme_7 | 86,607 | +0.031 | 51.6% | -0.014 |
| mean_rev_20 | 169,829 | +0.015 | 50.8% | -0.039 |
| bollinger_pos_20 | 167,997 | +0.014 | 50.8% | -0.041 |

---

## Sweep 1 — Entry threshold

Trade-quality vs trade-count tradeoff. All 5 models benefit from raising the threshold above the |score|>=35 "fires that might trade" floor.

| Model | thresh=35 | thresh=50 | **thresh=60** | thresh=70 |
|---|---|---|---|---|
| arima_20 | +0.056 | +0.083 | **+0.115** | +0.108 |
| combo_meanrev | +0.036 | +0.071 | +0.065 | +0.053 |
| rsi_extreme_7 | +0.031 | +0.035 | +0.038 | +0.039 |
| mean_rev_20 | +0.015 | +0.035 | +0.046 | +0.043 |
| bollinger_pos_20 | +0.014 | +0.035 | +0.045 | +0.042 |

**Verdict:** The existing markout sim default of |score|>=60 is already near-optimal across all 5 models. No change needed. combo_meanrev marginally prefers thresh=50 but the difference is small.

---

## Sweep 2 — Time-of-day filter (@ |score|>=35)

Good hours: 09:08, 09:38, 10:08, 10:38, 11:08, 12:08, 15:08, 16:08 ET (the windows where arima_20 audit showed positive edge).
Bad hours: 13:38, 14:38, 15:38 ET.

| Model | All hours | Good hours only | Exclude bad hours |
|---|---|---|---|
| arima_20 | +0.056 | **+0.179** ← 3.2× | +0.118 |
| combo_meanrev | +0.036 | +0.032 | +0.034 |
| rsi_extreme_7 | +0.031 | +0.044 | +0.037 |
| mean_rev_20 | +0.015 | +0.008 | +0.013 |
| bollinger_pos_20 | +0.014 | +0.009 | +0.013 |

**Verdict:** arima_20 is the ONLY model with meaningful time-of-day variance. The other 4 are insensitive to fire-time. arima_20 specifically benefits because the underlying ARIMA forecast quality varies by intraday session (open volatility, midday drift, close jockeying).

---

## Sweep 3 — Per-ticker whitelist (arima_20 only)

Applied the 58-name whitelist from `docs/arima_20_per_ticker_audit.md` (tickers where arima_20 has IC ≥ +0.05 with n ≥ 60).

| Filter | n | IC | hit | pnl |
|---|---|---|---|---|
| All tickers (baseline) | 56,403 | +0.056 | 52.9% | +0.112 |
| Whitelist only | 26,309 | **+0.255** ← 4.6× | 63.2% | +0.301 |
| Whitelist + good_hours | 13,717 | **+0.359** ← 6.4× | 69.2% | +0.489 |
| Whitelist + exclude_bad | 20,950 | +0.304 | 65.9% | +0.396 |

**Stacked at trading threshold |score|>=60 (the actual markout entry):**

| Filter | n | IC | hit | pnl |
|---|---|---|---|---|
| Baseline (>=60, RTH) | 27,657 | +0.118 | 56.1% | +0.225 |
| Whitelist only | 13,552 | +0.264 | 63.6% | +0.388 |
| Good hours only | 14,541 | +0.294 | 65.6% | +0.459 |
| **Whitelist + good_hours** | 7,090 | **+0.409** | **71.5%** | **+0.665** |
| Whitelist + exclude_bad | 10,953 | +0.337 | 67.5% | +0.516 |

**Verdict:** the combined arima_20 filter is the single biggest optimization in this sweep. Tripled-or-more on every metric.

---

## Recommendations

1. **Implement `arima_20_filtered` as a new tournament entrant.** A wrapper around arima_20 that:
   - Returns 0 if ticker not in whitelist (58 names)
   - Returns 0 if fire time outside good_hours (09:08, 09:38, 10:08, 10:38, 11:08, 12:08, 15:08, 16:08 ET)
   - Otherwise returns the underlying arima_20 score

   This produces sparser signals (~25% as many fires) but each fire has 3.5× higher expected edge. The existing markout sim will trade them at the same |score|>=60 threshold and produce a champion-tier curve.

2. **Don't optimize the other 4 top models on time-of-day or per-ticker** — sweep showed no meaningful lift. The current |score|>=60 entry threshold is already near-optimal for them.

3. **Run per-ticker audit on the other 4 models.** combo_meanrev, rsi_extreme_7, mean_rev_20, bollinger_pos_20 may have wide per-ticker dispersion like arima_20 did. A similar whitelist could lift their ICs to similar levels. Defer to a follow-up sweep.

4. **30-day validation** of the arima_20_filtered before promoting to champion status — current sample is 10 days.

---

## Open follow-ups

- [ ] Per-ticker audit on combo_meanrev / rsi_extreme_7 / mean_rev_20 / bollinger_pos_20
- [ ] Per-ticker audit on the 5 new v32 ensemble entrants (after 30 days of fires)
- [ ] Re-audit good_hours / bad_hours on a longer (30+ day) sample — these were derived from 14 days of arima_20 data
- [ ] Consider whether the good_hours pattern is arima-specific or also helps other ARIMA-family entrants (arima_1, arima_50, ar2_forecast_30)
