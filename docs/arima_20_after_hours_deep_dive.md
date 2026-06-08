# arima_20 — After-Hours Edge Deep Dive (and bigger time-of-day finding)
**Date:** 2026-05-28
**Triggered by:** Pre-market audit (docs/premarket_model_audit.md) flagging arima_20 as the only model with positive after-hours signed IC
**Sample:** 67,684 paired score+r30 observations, 2026-05-19 → 2026-05-28

---

## TL;DR

The headline "arima_20 has after-hours edge" was correct but **overstated**. The signed IC of +0.033 across the full after-hours window collapses to:

- **16:08 ET (early post-close): +0.077 IC, 77.6% hit rate, n=4,463** — real edge
- **16:38 ET, 17:08 ET: zero r30 movement** (markets closed, no realizable forward return). 5,846 of 10,309 after-hours fires fall here and contribute zero P&L. They inflated the sample size but added no information.

**The post-close edge is already being captured** (the 16:08 ET fire is inside the existing 09..17 timer window). No timer extension needed for this finding.

---

## The bigger surprise — intraday time-of-day matters MORE

Computing average signed r30 (P&L proxy) at the actual trading threshold (|score| ≥ 60) by fire-time:

| Fire (ET) | n | avg signed r30 (bps-ish) | hit rate |
|---|---|---|---|
| 09:08 (pre-market) | 1,183 | **+90.2** | 43.0% |
| 09:38 | 2,347 | **+97.0** | 60.4% |
| 10:08 | 3,630 | +15.7 | 62.2% |
| 10:38 | 1,766 | +57.3 | 66.1% |
| 11:08 | 1,844 | +33.3 | 63.2% |
| 11:38 | 1,136 | +4.1 | 40.6% |
| 12:08 | 1,849 | +68.5 | **76.1%** |
| 12:38 | 1,054 | +22.2 | 46.1% |
| 13:08 | 2,869 | +7.7 | 50.6% |
| 13:38 | 2,114 | −5.8 | 41.3% |
| 14:08 | 2,821 | +22.0 | 60.1% |
| **14:38** | 1,819 | **−70.2** | **24.7%** ← worst |
| 15:08 | 1,817 | +46.6 | 75.8% |
| 15:38 | 1,223 | −17.7 | 46.9% |
| **16:08 (post-close)** | 2,116 | +1.3 | **77.6%** |
| 16:38 / 17:08 | dead (no r30) | 0 | — |

**Pattern**: arima_20 is consistently profitable at session-open and mid-morning windows (09:08–11:08), again at the lunch reversal (12:08) and the 14:08–15:08 push, but consistently LOSES at 13:38, 14:38 (worst), and 15:38. The 16:08 post-close window has the highest hit rate but tiny edge.

This isn't an after-hours story. It's a **time-of-day conditioning** story.

---

## Why the after-hours "edge" appeared larger than it is

The simple breakdown averaged across all post-close fire windows:

| Window | Has live data flowing? | r30 realizable? | Counts |
|---|---|---|---|
| 16:08 ET | yes (last minute of regular session + early after-hours quotes) | yes | 4,463 |
| 16:38 ET | thin / after-hours only | mostly zero | 2,923 |
| 17:08 ET | after-hours only | zero | 2,923 |

Of 10,309 after-hours sample rows, only 4,463 (43%) carry usable r30 data. The +0.033 IC headline was diluted by 5,846 zero-r30 rows that hit-rate counted as misses. The 16:08 ET window in isolation is +0.077 IC, 77.6% hit — that's the real edge.

---

## Per-day after-hours consistency

| Day | n (post-close) | IC | hit |
|---|---|---|---|
| 2026-05-19 | 75 | +0.333 | 100% ← small sample |
| 2026-05-21 | 868 | +0.043 | 59.4% |
| 2026-05-22 | 672 | +0.235 | 100% ← suspicious |
| 2026-05-26 | 4,170 | +0.029 | 63.4% |
| 2026-05-27 | 4,524 | +0.000 | 50.6% |

Most-recent day (5/27) shows zero edge. The earlier days with 100% hit rates likely had very few non-zero r30 cases driving the metric. 5 days of data is too thin to conclude the post-close edge is stable.

---

## Per-ticker concentration (post-close)

| Ticker | n | IC | hit |
|---|---|---|---|
| INFQ | 318 | +0.245 | 74.7% |
| QBTS | 318 | +0.371 | 100% ← suspicious denominator |
| WDC  | 286 | +0.143 | 100% |
| DELL | 252 | +0.333 | 100% |
| WULF | 279 | +0.143 | 100% |
| BB   | 326 | **−0.123** | **33.6%** ← negative |
| GDRX | 246 | −0.167 | 0% |
| TTMI | 246 | −0.167 | 0% |
| ONDS | 246 | −0.167 | 0% |
| PTON / VSAT / INOD / SEZL / VELO / LIND | 240-285 | 0 | 0% (no movement) |

Several tickers anti-predict. Whatever post-close edge exists is concentrated in maybe 5-10 names and disappears across the others.

---

## Recommendation

1. **Do not extend capture timers to extended hours for this finding.** The 16:08 ET post-close fire is already inside the existing capture window. The 16:38 and 17:08 ET fires would add data but no signal.

2. **The actionable opportunity is intraday time-of-day conditioning**, not after-hours expansion. arima_20 has consistent edge in 6 intraday windows (09:08, 09:38, 10:38, 11:08, 12:08, 15:08) and consistent anti-edge in 3 (13:38, 14:38, 15:38). A time-of-day filter on the markout sim's arima_20 entries would likely improve realized P&L meaningfully. Worth a separate work item.

3. **Wait for ≥ 30 days of post-close data before drawing conclusions.** 5 days is too thin to validate the 16:08 ET edge as stable. The 100%-hit days are likely artifacts of small denominators.

4. **Investigate per-ticker filtering on arima_20** — at minimum exclude BB from arima_20 trading (consistent anti-edge across the sample).

---

## Open follow-ups

- [ ] Time-of-day-conditioned arima_20 P&L sim (markout backtest) to quantify the gain from filtering out the 13:38–15:38 anti-edge windows
- [ ] Per-ticker arima_20 audit — which names show stable edge vs noise vs anti-edge
- [ ] Resample after-hours edge after 30 days of fresh data
- [ ] Compare arima_20 16:08 ET behavior to arima_1 and arima_50 (same family, different parameters) to see if the edge is parameter-specific
