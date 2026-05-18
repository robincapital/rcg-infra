"""
meta_model_capture.py — write meta_blend_<horizon>_score signals at tournament fire-time.

Called by models_capture.py at the END of each tournament fire, after all
individual entrants have written their scores. Reads the most-recent scores
from the in-memory `current_scores` dict (passed in), applies the current
weights from outputs/meta_model_weights.json, writes 3 new runs (one per
horizon) into the signals table.

If no weights file exists yet (publish gate not met), this is a silent no-op.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402

from meta_model import (
    HORIZONS,
    WEIGHTS_PATH,
    score_observation,
)


def load_current_weights() -> Optional[dict]:
    """Return the current weights payload, or None if not yet trained."""
    if not WEIGHTS_PATH.exists():
        return None
    try:
        payload = json.loads(WEIGHTS_PATH.read_text())
        if not payload.get("fits"):
            return None
        return payload
    except Exception:
        return None


def capture_meta_scores(
    watchlist: dict,
    current_scores: dict[str, dict[str, float]],
    regime: dict,
    bbg_age: Optional[str] = None,
) -> int:
    """
    Compute + write meta-blend scores.

    Args:
      watchlist:       the BBG watchlist dict (for live prices)
      current_scores:  {ticker: {signal_name: value}} pulled from the just-written
                       entrant scores in models_capture
      regime:          regime_tag dict for this fire
      bbg_age:         optional BBG generated_at timestamp for the run config

    Returns the number of signals written.
    """
    weights_payload = load_current_weights()
    if not weights_payload:
        print(f"[meta-capture] no weights file yet — skipping (publish gate not met)")
        return 0

    fits = weights_payload.get("fits", {})
    if not fits:
        print(f"[meta-capture] empty fits — skipping")
        return 0

    total_signals = 0
    for horizon in HORIZONS:
        fit = fits.get(horizon)
        if not fit or "weights" not in fit:
            # No fit for this horizon yet (probably gate not met for this h)
            continue

        weights = fit["weights"]
        mu = fit["mu"]
        sigma = fit["sigma"]
        intercept = fit.get("intercept", 0.0)

        # Each horizon = 1 run with N ticker signals
        signal_name = f"model_meta_blend_{horizon}"
        run_id = sdb.record_run(
            run_type="model_score",
            config={"model":       signal_name,
                    "family":      "meta_blend",
                    "horizon":     horizon,
                    "bbg_age":     bbg_age,
                    "n_watchlist": len(watchlist),
                    "regime":      regime,
                    "fit_date":    fit.get("fit_date"),
                    "n_train":     fit.get("n_train"),
                    "oos_r2":      fit.get("oos_r2"),
                    "oos_ic_dir":  fit.get("oos_ic_dir")},
        )
        if not run_id:
            print(f"[meta-capture] DB unavailable — skipping {signal_name}")
            continue

        n_written = 0
        for ticker, w in watchlist.items():
            if not w or w.get("error"): continue
            feats = current_scores.get(ticker)
            if not feats: continue

            score = score_observation(
                feature_values=feats,
                weights=weights,
                mu=mu,
                sigma=sigma,
                intercept=intercept,
            )
            sdb.record_signal(run_id, ticker, f"{signal_name}_score", value=float(score))
            # Also record live_price so forward-returns capture can match
            live = w.get("price")
            if live is not None:
                sdb.record_signal(run_id, ticker, "live_price", value=float(live))
            n_written += 1

        sdb.finalize_run(run_id, n_out=n_written)
        print(f"[meta-capture] {signal_name}: {n_written} signals  "
              f"(weights from {fit.get('fit_date','?')[:10]})")
        total_signals += n_written

    return total_signals
