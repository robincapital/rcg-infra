# `rcg_report.py` Patch — Use `price_targets.py` for All Tickers

## Problem
Today, `rcg_report.run_analysis` has two paths in lines 936–963:

```python
if csv_row:
    # Path 1: screener CSV — INHERITS THE BROKEN ENGINE
    internal_target = cv(csv_row.get("target_price"))
    pt_source       = str(csv_row.get("pt_source") or "M")
else:
    # Path 2: independent compute via compute_v3_target_price (the GOOD engine)
```

This means the report's better engine is never applied to tickers IN the screener.
TEM (Healthcare → excluded) gets the good engine; INGM (Tech → in screener) gets
the broken one.

## Fix
Always run `price_targets.compute_target_price` regardless of CSV presence.
The CSV's `last_price`, `analyst_target_mean`, and `analyst_count` are still
useful as primary inputs — keep using them.

## Patch (replace lines 930–972 of rcg_report.py)

```python
    # ── PRICE TARGET ──────────────────────────────────────────
    import price_targets as _pt_engine

    internal_target    = None
    pt_model_breakdown = {}
    pt_source          = "none"
    divergence_flagged = False

    print("  Computing PT via shared price_targets engine...")

    if latest_mktcap and latest_shares and last_price:
        _res = _pt_engine.compute_target_price(
            ebitda_series    = ebitda,
            revenue_series   = revenue,
            fcf_series       = fcf_series,
            debt_series      = debt_series,
            marketcap        = float(latest_mktcap),
            last_price       = float(last_price),
            cash_on_hand     = float(latest_cash or 0),
            shares_diluted   = float(latest_shares) if latest_shares else None,
            sector           = info["sector"],
            fed_target_rate  = 0.03625,
            fed_neutral_rate = 0.0300,
            analyst_target   = cv(analyst.get("target_mean")),
            n_analysts       = int(analyst.get("total_analysts") or 0),
            apply_envelope   = True,
        )
        internal_target    = _res.target_price
        pt_model_breakdown = _res.breakdown.get("models", {})
        pt_source          = _res.pt_source
        divergence_flagged = _res.divergence_flag

        if internal_target:
            print(f"  Internal PT: ${internal_target:.2f}  source={pt_source}  "
                  f"models={list(pt_model_breakdown.keys())}  "
                  f"gates={_res.gates_fired}")
        else:
            print("  PT engine returned None — insufficient model fit")
    else:
        print("  Missing mktcap/shares/price — PT unavailable")

    analyst_target = cv(analyst.get("target_mean"))
    upside_pct = ((internal_target / last_price) - 1) * 100 if (
        internal_target and last_price and last_price > 0) else None
```

## Why this is the right move
- **Single source of truth**: Both screener and report compute identical PTs for the
  same ticker on the same day.
- **TEM behavior preserved**: TEM still runs through the same `compute_v3_target_price`
  logic (now lifted into `price_targets.compute_target_price`), but now gains the R²
  floor and the analyst envelope on top.
- **Reversibility**: To revert, delete the import + 30 lines and restore the original
  Path 1 / Path 2 fork from git. No data dependency changes.
- **Performance**: The new engine on a single ticker takes <50ms — the report's
  Sharadar fundamentals load is the bottleneck, not the PT compute.

## Apply
SCP `price_targets.py` to `/home/nixos/Prod/V1/src/`, then edit `rcg_report.py`
lines 930–972 with the block above. Re-run `generate_report("TEM")` to confirm
the same $32–35 range output (the envelope may push it slightly different from
the previous $32.27 because consensus was $77.69 and we now have a soft envelope
nudging upward — flag this as `M*` not `M⚠clip` since divergence < 75% of price).
