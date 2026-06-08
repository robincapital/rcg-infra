# relative_strength_rank — Sign Fix

**Date:** 2026-05-28
**File:** src/quant_signals.py:376
**Triggered by:** Pre-market audit finding (docs/premarket_model_audit.md), negative signed IC across all sessions

---

## The bug

The function computed percentile rank of a ticker's 5-bar return vs the universe, and returned **+100 for top of pack, -100 for bottom**. The docstring described it as a momentum signal: top performers continue.

In production, that interpretation has been wrong since deployment. The signed IC against realized 30-min forward returns:

| Session | n | signed IC | hit rate |
|---|---|---|---|
| RTH         | 158,922 | **−0.050** | **47.3%** |
| pre_open    | 12,887  | −0.020 | 48.7% |
| after_hours | 44,642  | −0.004 | 46.7% |

Hit rates consistently below 50% across 216K|score|≥35 fires confirms the sign is flipped, not just noise. The score correctly identifies the percentile, but on a 30-min forward horizon equities exhibit **short-term reversal** (Jegadeesh 1990, Lehmann 1990) — top recent performers fade, bottom performers bounce.

---

## The fix

Single-line change in src/quant_signals.py:

```python
# before
return float(np.clip((percentile - 0.5) * 200, -100, 100))

# after
return float(np.clip((0.5 - percentile) * 200, -100, 100))
```

Sign of the score is now flipped. New convention:
- **+100** → ticker was in BOTTOM percentile of recent 5-bar returns (= due to bounce → bullish)
- **−100** → ticker was in TOP percentile of recent 5-bar returns (= due to fade → bearish)

Aligns with tournament convention (positive = bullish).

---

## Expected impact

If the fix is correct, all-session IC inverts from negative to positive:

| Session | IC before | IC after (expected) |
|---|---|---|
| RTH         | −0.050 | **+0.050** |
| pre_open    | −0.020 | +0.020 |
| after_hours | −0.004 | +0.004 |

RTH +0.050 with 52.7% hit rate would put this in the upper tier of tournament entrants (most are in the 0.01-0.05 IC range). Worth keeping.

---

## Caveat

This is a sign-flip, not a model improvement. We did not improve information content; we corrected the interpretation. The model has been trading anti-edge since deployment — historical PnL contribution from this signal in the markout sim is unfavorable. The going-forward fix is correct; historical attribution is unchanged.

---

## Validation plan

- Re-audit IC after 1 week of new fires (next 200+ trading hours)
- Confirm hit rate crosses 50%
- If IC stays positive ≥ +0.030 after 30 days, promote model into champion consideration
- If IC stays near zero or flips back, the model is noise — decommission

---

## Backward compatibility

- Historical signal_values in the Postgres signals table are **NOT modified**. Old rows retain the inverted sign convention.
- The markout simulator joins on signal_name and uses the sign as-is, so the historical markouts.json continues to reflect the buggy behavior. New fires going forward use the corrected sign.
- If we ever need to backfill historical sign-corrected scores, run: `UPDATE signals SET signal_value = -signal_value WHERE signal_name = 'model_relative_strength_rank_5bar_score' AND asof_date < '2026-05-28';` — flagged but NOT run automatically (irreversible).
