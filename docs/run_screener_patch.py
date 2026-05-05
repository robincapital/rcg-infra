"""
run_screener_patch.py  —  Integration Patch for run_screener.py
================================================================
This file documents the exact changes to make to run_screener.py to wire in
price_targets.py behind a feature flag. Two options:

  OPTION A (recommended):  Add the patch block below directly into run_screener.py
                           after the existing screener.screen_stocks monkeypatch.

  OPTION B:                Apply this file via `exec(open(...).read(), globals())`
                           from run_screener.py if you'd prefer to keep the patch
                           file separate.

NEW CONTROL VARIABLES (add to run_screener.py CONFIG section):

    USE_NEW_PRICE_TARGETS   = True       # master switch
    PT_APPLY_ENVELOPE       = True       # Gate B: clip model PT to analyst band
    PT_R2_FLOOR             = 0.20       # Gate A: drop models below this R²
    PT_R2_FULL              = 0.40       # full conviction at and above this R²
    BLOOMBERG_INTRADAY_MAX  = 50         # cap on intraday tickers (Phase 5 prep)
"""

# ============================================================
# PASTE INTO run_screener.py AFTER THE screen_stocks MONKEYPATCH
# (i.e., right after the `screener.screen_stocks = _patched_screen` block)
# ============================================================

# ── Price target engine swap ──────────────────────────────────────
# Replace screener.compute_target_price_and_upside and screener.compute_blended_target
# with the new shared engine in price_targets.py. Behind feature flag.
if USE_NEW_PRICE_TARGETS:
    try:
        import price_targets as pt_engine
        # Configure floor/full from config
        pt_engine.R2_HARD_FLOOR  = PT_R2_FLOOR
        pt_engine.R2_FULL_WEIGHT = PT_R2_FULL

        # Capture analyst targets at the moment apply_blended_targets runs.
        # The screener fetches these into a dict and passes them to
        # apply_blended_targets; we want to forward them into the new engine
        # so Gate B can engage. The simplest hook is wrapping
        # apply_blended_targets — that function already iterates per-row and
        # has access to analyst_data.
        _orig_apply_blended = screener.apply_blended_targets
        def _patched_apply_blended(screened, analyst_price_targets):
            if screened.height == 0:
                return _orig_apply_blended(screened, analyst_price_targets)
            import json as _json_pt
            import polars as _pl

            blended_pts, upside_pcts, upside_scs = [], [], []
            analyst_means, div_flags, sources = [], [], []
            new_pt_details = []

            for i in range(screened.height):
                row = screened.row(i, named=True)
                ticker     = row["ticker"]
                last_price = row.get("last_price")
                marketcap  = row.get("marketcap")

                # Pull the original fundamental series from the row's pt_detail_json
                # (the screener already computed PT once — we recompute fresh here
                # against the new engine using the same series, which is faster than
                # re-loading SF1).  However the existing screen_stocks doesn't store
                # raw series in the row, only the breakdown.  So fall back to a
                # SHALLOW path: re-run the new engine using the saved internal_target
                # if it exists, then apply ENVELOPE only.
                #
                # For the FULL repair (recompute models with the new R² floor + quality
                # haircut), call the new engine end-to-end. We need raw series.
                # The cleanest path is to re-pull from sf1, but sf1 isn't in scope here.
                # So we pass the saved internal_target through Gate B (envelope) only.
                # The full recompute happens inside screen_stocks via the
                # compute_target_price_and_upside swap below.

                internal_tp = row.get("internal_target")
                pt_detail   = _json_pt.loads(row.get("pt_detail_json", "{}"))
                analyst_d   = analyst_price_targets.get(ticker, {})
                atarget     = analyst_d.get("target_mean")
                # n_analysts comes from the sentiment fetch; fall back to per-row fields
                n_analysts  = int(row.get("analyst_count") or 0)

                if PT_APPLY_ENVELOPE and internal_tp and atarget and last_price:
                    final_pt, src, flagged = pt_engine.envelope_to_consensus(
                        internal_pt   = float(internal_tp),
                        analyst_target= float(atarget),
                        n_analysts    = n_analysts,
                        last_price    = float(last_price),
                    )
                    if src == "M⚠clip":
                        # update breakdown so HTML reflects the clip
                        gates = pt_detail.get("gates_fired", []) or []
                        if "ENVELOPE_CLIPPED_TO_CONSENSUS" not in gates:
                            gates.append("ENVELOPE_CLIPPED_TO_CONSENSUS")
                        pt_detail["gates_fired"] = gates
                        pt_detail["pt_source"]   = src
                        pt_detail["raw_pt"]      = float(internal_tp)
                else:
                    final_pt = internal_tp
                    src      = "M" if internal_tp else "N/A"
                    flagged  = False

                if final_pt and last_price and last_price > 0:
                    upside_pct = (final_pt / last_price) - 1.0
                    upside_sc  = float(min(max((upside_pct ** 0.5) * 0.7
                                                if upside_pct > 0 else upside_pct,
                                                -1.0), 2.0))
                else:
                    upside_pct, upside_sc = 0.0, 0.0

                blended_pts.append(round(final_pt, 2) if final_pt else None)
                upside_pcts.append(round(upside_pct, 4))
                upside_scs.append(round(upside_sc, 4))
                analyst_means.append(atarget)
                div_flags.append(flagged)
                sources.append(src)
                new_pt_details.append(_json_pt.dumps(pt_detail))

            drop_cols = [c for c in ["target_price", "upside_pct", "upside_score",
                                       "analyst_target_mean", "analyst_divergence_flag",
                                       "pt_source"]
                         if c in screened.columns]
            if drop_cols:
                screened = screened.drop(drop_cols)

            screened = screened.with_columns([
                _pl.Series("target_price",            blended_pts),
                _pl.Series("upside_pct",              upside_pcts),
                _pl.Series("upside_score",            upside_scs),
                _pl.Series("analyst_target_mean",     analyst_means),
                _pl.Series("analyst_divergence_flag", div_flags),
                _pl.Series("pt_source",               sources),
                _pl.Series("pt_detail_json",          new_pt_details),
            ])
            return screened
        screener.apply_blended_targets = _patched_apply_blended

        # Replace the per-ticker model engine. This is what kills bad models
        # via the R² floor and applies quality haircut. screen_stocks calls
        # compute_target_price_and_upside per ticker — swap that function.
        _orig_compute_pt = screener.compute_target_price_and_upside
        def _patched_compute_pt(ebitda_series, debt_series, fcf_series,
                                  marketcap, last_price, cash_on_hand=0.0,
                                  shares_diluted=None, revenue_series=None,
                                  sector=None):
            return pt_engine.screener_compat(
                ebitda_series   = ebitda_series,
                debt_series     = debt_series,
                fcf_series      = fcf_series,
                marketcap       = marketcap,
                last_price      = last_price,
                cash_on_hand    = cash_on_hand,
                shares_diluted  = shares_diluted,
                revenue_series  = revenue_series,
                sector          = sector,
                fed_target_rate = FED_TARGET_RATE,
                fed_neutral_rate= FED_NEUTRAL_RATE,
                # No analyst data at this stage (per-ticker pre-fetch); envelope
                # is applied in apply_blended_targets above where analyst_data exists.
                analyst_target  = None,
                n_analysts      = 0,
                apply_envelope  = False,
            )
        screener.compute_target_price_and_upside = _patched_compute_pt

        print(f"[PT-ENGINE] price_targets.py active "
              f"(R²-floor={PT_R2_FLOOR}, full={PT_R2_FULL}, "
              f"envelope={'ON' if PT_APPLY_ENVELOPE else 'OFF'})")

    except ImportError as e:
        print(f"[PT-ENGINE] price_targets.py not found — using legacy engine. ({e})")
else:
    print("[PT-ENGINE] USE_NEW_PRICE_TARGETS=False — using legacy engine")
