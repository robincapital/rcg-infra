# Change D — Explicit TAM Penetration Model (v28.8 spec)
**Status:** AWAITING APPROVAL — reply `ship D` to build, `revise` with notes to iterate
**Author:** RCG Quant Agent under MM direction
**Date:** 2026-05-19

---

## Goal

Add a **5th valuation model** to `price_targets.py` that explicitly anchors PT to TAM × penetration × mature FCF margin × exit multiple. Replaces nothing — sits alongside the existing 4 (EV/EBITDA, EV/Rev, FCF Yield, Emerging Growth). Fires only when a TAM number is set for the ticker, so the MM opts in per name.

Closes the gap on names like RKLB where the market is pricing 5-10 year future revenue and our 3-year forward model can't keep up.

---

## The math

```
mature_revenue        = TAM × mature_penetration_pct
mature_fcf            = mature_revenue × mature_fcf_margin
mature_equity_value   = mature_fcf × exit_fcf_multiple
present_value_equity  = mature_equity_value / (1 + discount_rate) ^ years_to_mature
pt_per_share          = present_value_equity / shares_diluted
```

Five inputs per ticker (all user-set, all with conservative defaults):

| Input | Default | Cap | Notes |
|---|---:|---:|---|
| **TAM** ($B) | none — manual entry required | $10T | The market the company is competing in, today. Not future-projected. |
| **Mature penetration** (%) | 5% | 20% | The terminal market share the company captures at maturity. 20% cap because no name owns more than a fifth of any major TAM at maturity. |
| **Mature FCF margin** (%) | 20% | 40% | Free cash flow margin at scale. 20% = mid-SaaS-like. 40% = ceiling (NVDA/Visa territory). |
| **Exit FCF multiple** (×) | 15× | 25× | Terminal multiple applied to mature FCF. 15× = mature SaaS. 25× = ceiling. |
| **Years to maturity** | 7 | floor 5 / cap 15 | Discount horizon. Defaults to 7. Floor of 5 so we don't pretend hypergrowth is 2 years from terminal. |
| Discount rate | 12% (fixed) | n/a | Not user-set in v1. Rate above WACC for execution risk + dilution + competitive risk. Can expose as 6th slider in v2 if you want. |

### Conservative bias is built into the **caps**, not the multipliers

The earlier Emerging Growth model tried to be conservative by under-multiplying. The TAM model is conservative by **capping penetration + multiple**. Every input below the cap is explicit and arguable.

---

## When the model fires

- **Always when `tam_usd_billions` is set** for that ticker in `user_assumptions.json`
- **Never automatically.** No name auto-gets TAM treatment.
- **User opts in** by entering the TAM number for a name they think is mispriced on a 5-10yr view.

### Blend with existing 4 models

Two options — your call:

| Option | Description | When best |
|---|---|---|
| **D1: TAM dominates (100% weight when fired)** | If TAM is set, that's the only model contributing to PT. Other 4 still computed but zero weight. | When you've decided the name should be valued on TAM, full stop |
| **D2: TAM blends at 70%, others share 30%** | TAM gets a strong vote but other models still influence. Helps keep PT anchored when one model is way off. | When you want a "second opinion" from existing models |

**My recommend: D1 (100% when fired).** Rationale: if you've gone to the trouble of setting TAM inputs, you've made an explicit call that this name needs TAM-based valuation. Don't dilute it with models that you already know are wrong (negative EBITDA, etc.). User can manually edit the TAM number to widen/narrow — that's the more honest way to express uncertainty than a fixed blend ratio.

---

## What the user sees

### Report (PDF)

New section in the right column, under "Risk Factors", before "Technical Snapshot":

```
TAM-BASED VALUATION
─────────────────────────────────────────────
Total Addressable Market:   $50B
Mature Penetration:          5.0%
Mature FCF Margin:          20.0%
Exit FCF Multiple:            15×
Years to Maturity:             7
Discount Rate:              12.0%

→ Mature Revenue:         $2.5B
→ Mature FCF:             $500M
→ Mature Equity Value:     $7.5B
→ PV @ 7 years:            $3.4B
→ PT per share:             $5.65
```

### Dashboard

Existing assumption-sliders flow (the one on the per-ticker detail row) gets 5 new sliders below the existing 4 growth sliders:

| Slider | Range | Default |
|---|---|---|
| TAM ($B) | 0 - 10000 | (must be set) |
| Mature Penetration (%) | 1 - 20 | 5 |
| Mature FCF Margin (%) | 5 - 40 | 20 |
| Exit FCF Multiple | 5 - 25 | 15 |
| Years to Maturity | 5 - 15 | 7 |

When the TAM slider is at 0 / unset → model doesn't fire. When > 0 → fires with the other sliders' values.

A small "TAM model" indicator chip appears next to the PT when this model is active.

---

## Files touched

| File | Change |
|---|---|
| `src/price_targets.py` | Add `compute_tam_model(tam, penetration, margin, exit_mult, years, discount, debt, cash, shares)` function. Add the 5th model branch inside `compute_target_price()`. Blend logic per D1. |
| `src/sentiment_refresh_server.py` | Extend `/assumptions/<TICKER>` endpoint to accept the 5 new fields. Extend `compute_pt_payload()` to pass them through. |
| `src/user_assumptions.json` | Schema gets 5 new fields per ticker (all optional / null when not set) |
| `src/rcg_report.py` | New "TAM-Based Valuation" section in `draw_report()` when model fired. Surface the math line-by-line. |
| `src/trade.html` | Add 5 new sliders to the per-ticker detail row. Add the "TAM model" indicator chip on the Top 40. |
| `docs/CONTEXT_price_targets.md` | Document the new model + when to use it |

Total ~600 LoC across all files.

---

## Sample numbers — what RKLB would look like with TAM

Using realistic inputs for Rocket Lab (small-lift launch market):

| Input | Value | Reasoning |
|---|---:|---|
| TAM | $80B | Global commercial space launch + satellites — conservative; some analysts use $300B+ |
| Mature penetration | 10% | They're a top-3 player; 10% at maturity is reasonable |
| Mature FCF margin | 22% | Capital intensive but high-margin at scale; below SaaS ceiling |
| Exit FCF multiple | 15× | Mature growth-aerospace ceiling |
| Years to maturity | 8 | Space industry takes time |
| Discount rate | 12% | Fixed |

Math:
- Mature revenue: $80B × 10% = $8B
- Mature FCF: $8B × 22% = $1.76B
- Mature equity: $1.76B × 15 = $26.4B
- PV @ 8 yrs / 12%: $26.4B / 2.48 = $10.6B
- PT: $10.6B / 605M shares = **$17.60** (vs $79 market price — still under, but anchored on real assumptions)

**To get to market price $79:** you'd need $80 × 605M = $48.4B equity, meaning either:
- TAM = $360B (4.5× higher)
- Penetration = 18% (close to cap)
- Or shorten years-to-mature to 4 (aggressive)
- Or combine all three modestly

This is now **a conversation you can have explicitly**. The model surfaces each assumption; you can dial each one and see what implied path the market is pricing. That's the analytical insight.

---

## Observability (per the new standard)

**Track:** New section on the report shows when TAM model fires. Dashboard chip surfaces names with active TAM models.

**Monitor:** `user_assumptions.json` is read on every report generation; mtime exposed via `feed_status.js` widget. Slack alert if it grows >1MB (sign of corruption or runaway writes).

**Repair:** File is plain JSON. Manual override = edit the file directly. No persistent state coupling — deleting any ticker's TAM block reverts to default 4-model blend.

---

## Open decisions — need your sign-off

1. **D1 vs D2** blend strategy (100% TAM when fired vs 70/30)?
2. **Default penetration**: 5% or 10%? (Higher default = more aggressive valuations out of the box. 5% is more conservative; users dial up for high-conviction names.)
3. **Default exit multiple**: 15× or 20×? (15× = mature growth ceiling; 20× = leaves more room to dial down for skeptical view)
4. **Years to maturity floor**: 5 or 7? (Lower = allows more aggressive timelines for true hypergrowth)
5. **TAM cap**: $10T? Or no cap? (Cap catches typos but limits e.g. AI/healthcare megamarkets)
6. **Make discount rate a 6th slider, or keep fixed at 12%?**

---

## Build timeline if approved

- **Day 1:** `compute_tam_model()` in price_targets.py + blend logic + smoke test on RKLB/PLTR/NVDA with sample TAM values
- **Day 2:** Wire to sentiment_refresh_server endpoints + extend user_assumptions schema + write a few sample TAM entries
- **Day 3:** Report renderer (TAM section) + dashboard sliders + indicator chip
- **Day 4:** End-to-end verification + commit + observability wiring + docs

Total: **~4 days**. Could compress to 2-3 if I skip the dashboard slider UI and only expose via JSON file edits — but you'd lose the click-to-tune flow.

---

## What I want next

1. Approve / revise the open decisions above
2. Choose dashboard UI scope: full sliders (4-day build) vs JSON-only (2-day build)
3. List the first 3-5 tickers you want to enter TAM values for once it's live — so we can validate against your actual analytical view

Reply `ship D` (+ your defaults) to begin Day 1.
