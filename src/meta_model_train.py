"""
meta_model_train.py — weekly re-fit of the OLS meta-blend.

Designed to run Monday ~06:00 ET (after weekly screener regen, before market open).
For each horizon:
  · Pulls trailing 14 days of (feature_vector, realized_return) observations
  · Checks publish gate (≥ 1000 obs, ≥ 7 trading days)
  · Fits OLS with held-out OOS evaluation
  · Computes stability vs prior week's weights
  · Writes weights + diagnostics

If the gate isn't met, logs "waiting" and leaves the existing weights
file untouched (so capture continues using last week's weights).

Manual invocation:
  /home/nixos/Prod/V1/var/agent_venv/bin/python -m meta_model_train
  # or directly
  /nix/store/.../python3 /home/nixos/Prod/V1/src/meta_model_train.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import numpy as np

from meta_model import (
    HORIZONS,
    build_feature_matrix,
    check_publish_gate,
    cosine_similarity,
    fit_with_oos_check,
    get_feature_names,
    load_weights,
    save_diagnostics,
    save_weights,
)


def main() -> None:
    fit_date = datetime.now(timezone.utc).isoformat()
    print(f"[meta-train] starting fit @ {fit_date}")

    feature_names = get_feature_names()
    print(f"[meta-train] {len(feature_names)} features discovered")

    prior = load_weights() or {}
    prior_fits = (prior.get("fits") or {})

    out = {
        "fit_date":      fit_date,
        "feature_names": feature_names,
        "fits":          {},
        "gate_state":    {},
    }

    any_published = False

    for h in HORIZONS:
        print(f"\n[meta-train] === horizon {h} ===")

        X, y, fnames, obs_meta = build_feature_matrix(horizon=h, cutoff_days=14)
        n_obs = len(y)

        # Count distinct trading days
        days = set()
        for _, _, ts in obs_meta:
            try:
                days.add(ts[:10])    # YYYY-MM-DD
            except Exception:
                pass
        n_days = len(days)

        gate_ok, gate_reason = check_publish_gate(n_obs, n_days)
        out["gate_state"][h] = {
            "n_obs":      n_obs,
            "n_days":     n_days,
            "ok":         gate_ok,
            "reason":     gate_reason,
        }
        print(f"  obs={n_obs}  days={n_days}  gate={'✓' if gate_ok else '✗'} ({gate_reason})")

        if not gate_ok:
            # Carry forward prior weights if they exist for this horizon
            if h in prior_fits:
                out["fits"][h] = prior_fits[h]
                print(f"  keeping prior weights (last fit: {prior_fits[h].get('fit_date','?')})")
            continue

        fit = fit_with_oos_check(X, y, fnames, obs_meta, oos_days=3)
        if "error" in fit:
            print(f"  fit failed: {fit['error']}")
            continue

        # Stability vs prior week
        prior_w = (prior_fits.get(h) or {}).get("weights") or {}
        cos_sim = cosine_similarity(fit["weights"], prior_w)
        fit["stability_cosine_sim"] = cos_sim
        fit["fit_date"] = fit_date

        out["fits"][h] = fit
        any_published = True

        # Print summary
        print(f"  in-sample R² = {fit['in_sample_r2']:+.4f}")
        print(f"  OOS R²       = {fit['oos_r2']:+.4f}")
        print(f"  OOS IC dir   = {fit['oos_ic_dir']:+.4f}")
        print(f"  stability    = {cos_sim:+.3f}  (cosine sim vs prior week)")
        print(f"  top weights (by |w|):")
        for name, w in fit["top_weights"]:
            print(f"    {name:50s}  {w:+.4f}")

        # Diagnostics entry
        save_diagnostics({
            "fit_date":              fit_date,
            "horizon":               h,
            "n_train":               fit["n_train"],
            "n_test":                fit["n_test"],
            "in_sample_r2":          fit["in_sample_r2"],
            "oos_r2":                fit["oos_r2"],
            "oos_ic_dir":            fit["oos_ic_dir"],
            "stability_cosine_sim":  cos_sim,
            "top_positive_weights":  [(n, w) for n, w in fit["top_weights"] if w > 0][:5],
            "top_negative_weights":  [(n, w) for n, w in fit["top_weights"] if w < 0][:5],
            "n_features_used":       len(fit["weights"]),
        })

    save_weights(out)
    print(f"\n[meta-train] {'published' if any_published else 'NO published fits'} → {len(out['fits'])} horizons in weights file")


if __name__ == "__main__":
    main()
