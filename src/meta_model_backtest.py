"""
meta_model_backtest.py — historical sliding-window backtest of the meta-blend.

Per the spec: before publishing the meta-blend live, validate that it would
have outperformed the best individual entrant in recent history.

Method:
  For each day D in the trailing 21 days:
    1. Build a feature matrix from days [D-14, D-1] (training)
    2. Build a feature matrix from day D (out-of-sample test)
    3. Fit OLS on training
    4. Predict on test, compute meta-blend IC
    5. Compute IC of each individual entrant on the same test set
  Aggregate:
    · Meta-blend IC (mean across days)
    · Best individual entrant IC (mean across days)
    · Was meta > best individual? How often?
    · By-horizon breakdown

Writes outputs/meta_model_backtest.json. Run once before deploying.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import numpy as np

from meta_model import (
    BACKTEST_PATH,
    HORIZONS,
    build_feature_matrix,
    fit_with_oos_check,
    get_feature_names,
    standardize,
    fit_ols,
)


N_BACKTEST_DAYS = 21      # last 21 days of slides
TRAIN_WINDOW = 14         # match production cadence


def directional_ic(y_pred: np.ndarray, y_true: np.ndarray) -> float:
    if len(y_true) == 0: return 0.0
    return float(np.mean(np.sign(y_pred) * np.sign(y_true)))


def main() -> None:
    print(f"[meta-backtest] starting — {N_BACKTEST_DAYS} sliding days, {TRAIN_WINDOW}d train window")
    feature_names = get_feature_names()
    print(f"[meta-backtest] {len(feature_names)} features")

    results = {
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "n_features":   len(feature_names),
        "train_window": TRAIN_WINDOW,
        "by_horizon":   {},
    }

    for h in HORIZONS:
        print(f"\n[meta-backtest] === horizon {h} ===")

        # Pull a wider window once, then slide test days through it
        wide_X, wide_y, fnames, wide_meta = build_feature_matrix(
            horizon=h, cutoff_days=TRAIN_WINDOW + N_BACKTEST_DAYS + 2,
        )
        if len(wide_y) < 100:
            print(f"  skipped — only {len(wide_y)} total observations")
            results["by_horizon"][h] = {"error": "insufficient data", "n_total": len(wide_y)}
            continue

        # Group observations by date (date = trading day)
        dates = []
        for _, _, ts in wide_meta:
            try:
                dates.append(ts[:10])
            except Exception:
                dates.append(None)
        dates_arr = np.array(dates)
        unique_days = sorted({d for d in dates if d})

        if len(unique_days) < TRAIN_WINDOW + 5:
            print(f"  skipped — only {len(unique_days)} distinct days")
            results["by_horizon"][h] = {"error": "insufficient days",
                                         "n_days": len(unique_days)}
            continue

        # For each test day D in the LAST N_BACKTEST_DAYS days,
        # train on the prior TRAIN_WINDOW days, test on day D
        meta_ics = []
        best_individual_ics = []
        per_day_records = []

        days_to_test = unique_days[-N_BACKTEST_DAYS:]
        for test_day in days_to_test:
            # Training mask: dates BEFORE test_day
            train_mask = dates_arr < test_day
            test_mask  = dates_arr == test_day
            n_train = int(train_mask.sum())
            n_test  = int(test_mask.sum())

            if n_train < 200 or n_test < 5:
                continue

            # Limit train to most recent TRAIN_WINDOW days
            train_dates_in_mask = sorted({dates_arr[i] for i in range(len(dates_arr))
                                          if train_mask[i]})
            if len(train_dates_in_mask) > TRAIN_WINDOW:
                cutoff_train_date = train_dates_in_mask[-TRAIN_WINDOW]
                train_mask = train_mask & (dates_arr >= cutoff_train_date)
                n_train = int(train_mask.sum())

            X_train = wide_X[train_mask]
            y_train = wide_y[train_mask]
            X_test  = wide_X[test_mask]
            y_test  = wide_y[test_mask]

            # Standardize on train, apply to test
            Xz_train, mu, sigma = standardize(X_train)
            Xz_test = (X_test - mu) / sigma

            # Fit OLS
            weights, intercept, _ = fit_ols(Xz_train, y_train)

            # Predict on test
            y_meta_pred = Xz_test @ weights + intercept
            meta_ic = directional_ic(y_meta_pred, y_test)
            meta_ics.append(meta_ic)

            # IC of each individual entrant on the same test set
            individual_ics = {}
            for j, name in enumerate(fnames):
                col = X_test[:, j]
                if np.all(col == 0): continue   # entrant didn't fire
                ic = directional_ic(col, y_test)
                individual_ics[name] = ic
            if individual_ics:
                best_name = max(individual_ics, key=lambda k: abs(individual_ics[k]))
                best_ic = individual_ics[best_name]
                best_individual_ics.append(best_ic)
                per_day_records.append({
                    "date":           test_day,
                    "n_test":         n_test,
                    "meta_ic":        round(meta_ic, 4),
                    "best_indiv":     best_name,
                    "best_indiv_ic":  round(best_ic, 4),
                    "meta_beat":      abs(meta_ic) > abs(best_ic),
                })

        if not meta_ics:
            results["by_horizon"][h] = {"error": "no usable test days"}
            continue

        n_meta_beat = sum(1 for r in per_day_records if r["meta_beat"])
        summary = {
            "n_test_days":             len(per_day_records),
            "meta_ic_mean":            round(float(np.mean(meta_ics)), 4),
            "meta_ic_std":             round(float(np.std(meta_ics)), 4),
            "best_individual_ic_mean": round(float(np.mean(best_individual_ics)), 4),
            "meta_beat_pct":           round(100 * n_meta_beat / len(per_day_records), 1),
            "per_day_records":         per_day_records[-10:],  # last 10 days for inspection
        }
        results["by_horizon"][h] = summary

        print(f"  test days:          {summary['n_test_days']}")
        print(f"  meta IC (mean):     {summary['meta_ic_mean']:+.4f}")
        print(f"  best indiv (mean):  {summary['best_individual_ic_mean']:+.4f}")
        print(f"  meta beat indiv:    {summary['meta_beat_pct']}% of days")
        verdict = ("✓ meta-blend appears to outperform" if summary['meta_ic_mean'] > summary['best_individual_ic_mean']
                   else "⚠ meta-blend NOT outperforming individual best")
        print(f"  verdict:            {verdict}")

    BACKTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n[meta-backtest] wrote {BACKTEST_PATH}")


if __name__ == "__main__":
    main()
