# arima_20 — Per-Ticker IC Audit
**Date:** 2026-05-28
**Sample:** All paired (score, r30) observations at |score| >= 35, n=67,684
**Lookback:** 2026-05-19 → 2026-05-28 (10 days)

---

## TL;DR

arima_20's overall +0.039 RTH IC masks **wide per-ticker dispersion**. Of 120 tickers in the watchlist, 58 show strong edge (IC ≥ +0.05) and 41 show anti-edge (IC ≤ -0.05). The overall metric is a blend that masks names where the model is actively losing.

**Recommendation: whitelist arima_20 to the 58 names with positive per-ticker IC** (n ≥ 60 samples each). Expected to lift overall arima_20 IC from +0.04 to +0.15–0.25 range based on the dispersion.

---

## Whitelist — 58 names (IC ≥ +0.05, n ≥ 60)

`PAY GLD HST SLV BW ADTN CDE HUBS RDDT CLOV HL KGC TFPM RBLX RBRK CARG FLEX IRMD M BE RMBS PVH ONDS DELL FRPT STX UMAC SNDK NWPX MDB GDRX WULF SYM OSCR LIND IOT MRVL QNST SANM POWI RKLB NUTX HIVE ANET CRDO DOCN NSP USO VIX FLOC FIVN ALAB INFQ SEZL VELO PTON LUNR BLFS`

Top 5 by IC × sample size:
| Ticker | n | IC | hit | avg signed r30 |
|---|---|---|---|---|
| RDDT | 348 | +0.664 | 87.4% | +0.66 |
| HUBS | 240 | +0.667 | 83.3% | +0.64 |
| CLOV | 321 | +0.502 | 75.1% | +0.99 |
| RBRK | 243 | +0.490 | 79.3% | +0.38 |
| CARG | 195 | +0.400 | 75.0% | +0.19 |

## Blacklist — 41 names (IC ≤ -0.05, n ≥ 60)

`PL DAVE MXL INV ALGT XNDU AIP PENG ARX USAR ZETA IONQ PRG APEI APLD INOD TGT GPOR AFRM VISN TTMI FLY HCSG VICR ASST IDR VLGEA CART ASTH EVER OKTA LITE ORLA SII OPLN MYRG PANW CHRD ASIC XLE SNEX`

Worst 5 by IC × sample size:
| Ticker | n | IC | hit | avg signed r30 |
|---|---|---|---|---|
| LITE | 764 | -0.421 | 27.8% | -0.34 |
| PANW | 358 | -0.665 | 12.6% | -0.37 |
| CHRD | 323 | -0.746 | 12.7% | -0.07 |
| EVER | 322 | -0.382 | 28.3% | +0.17 |
| OKTA | 202 | -0.396 | 25.3% | -0.22 |

LITE is the largest blacklist case (764 fires, consistent -0.42 IC). PANW and CHRD show extreme anti-edge but smaller samples.

---

## Caveats

- The very top of the whitelist (IC = +1.000 names like HTB, OMDA, PAY at n=40-80) are likely small-sample artifacts; treat them with caution until more data accumulates.
- The blacklist names with very negative IC at small n (S, NEM, NFGC, AMBQ, PGC, SNEX, XLE at n=40-80, IC=-1.000) are similarly noisy — could be genuine anti-edge OR sample size artifact.
- The reliable signal comes from names with n >= 200 AND |IC| >= 0.20. That's a smaller set: `LITE, PANW, CHRD, RDDT, HUBS, CLOV, RBRK, EVER, OKTA`.

---

## How to apply the whitelist

The markout simulator currently sims arima_20 across the full universe. To apply the whitelist:

```python
# in markout_eval.py simulate(), after scores_by_bucket is loaded:
WHITELIST_ARIMA_20 = set(["PAY", "GLD", "HST", ...])  # the 58 names above
if model_name == "arima_20":
    scores_by_bucket = {
        bt: {t: s for t, s in tk.items() if t in WHITELIST_ARIMA_20}
        for bt, tk in scores_by_bucket.items()
    }
```

Better: load whitelist from JSON config so it can be updated without code change. See `/home/nixos/Prod/V1/outputs/arima_20_whitelist.json` (TODO — not created yet).

---

## Validation plan

- Re-run markout sim with whitelist filter, compare to unfiltered
- Expected: arima_20 cumulative return improves, Sharpe up, trade count down by ~50% (the blacklist + neutral names are dropped)
- Re-audit per-ticker IC after 30 days of fresh data to confirm whitelist stability
