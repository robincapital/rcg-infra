"""
meta_model.py — Stage 1 OLS meta-blend of all tournament entrants.

Architecture (per the v1.0 spec approved 2026-05-18):
  · For each horizon (30min/60min/4h) train one OLS regression
  · Features: 39 family entrants + bbg composite + 5 BBG sub-signals = 45 total
  · Target: realized_return_<horizon>_pct
  · Train window: trailing 14 trading days
  · Missing values: drop rows where > 50% of features are NULL;
                    impute partial coverage with 0 ("no opinion")
  · Standardize via z-score within training window
  · Output: weights vector + (mu, sigma) scaling params + diagnostics
  · Publish gate: ≥ 1000 obs AND ≥ 7 trading days before first publish

Re-fit cadence: weekly Monday 06:00 ET (handled by meta_model_train.py).
Live scoring: every tournament fire (handled by meta_model_capture.py).

The meta-blend competes in the tournament as a new family `meta_blend`
with 3 entrants (one per horizon). Same forward-return capture, same per-
regime IC, same family-collapse leaderboard rendering.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import psycopg

# ────────────────────────────────────────────────────────────────────────
# Feature set — 45 inputs to the meta-blend
# ────────────────────────────────────────────────────────────────────────
# Family entrants (all model_*_score signals as they exist on disk in
# models_capture.py's MODELS list). New entrants added there are picked
# up here automatically on the next re-fit (the train script discovers
# them via SQL DISTINCT signal_name).
#
# BBG predictive composite + sub-signals are FIXED inputs (named below).

BBG_INPUTS = [
    "pred_signed_score",     # the BBG predictive composite
    "pred_surge",
    "pred_udv",
    "pred_accel",
    "pred_vwap_slope",
    "pred_range_exp",
]

HORIZONS = ["30min", "60min", "4h"]
HORIZON_TO_RETURN_SIGNAL = {h: f"realized_return_{h}_pct" for h in HORIZONS}

# Publish-gate thresholds
MIN_OBSERVATIONS = 1000      # samples needed before first publish
MIN_TRADING_DAYS = 7         # days of data needed before first publish

# Weights / diagnostics output paths
WEIGHTS_PATH      = Path("/home/nixos/Prod/V1/outputs/meta_model_weights.json")
DIAGNOSTICS_PATH  = Path("/home/nixos/Prod/V1/outputs/meta_model_diagnostics.json")
BACKTEST_PATH     = Path("/home/nixos/Prod/V1/outputs/meta_model_backtest.json")

DB_DSN = "host=/run/postgresql user=nixos dbname=rcg_signals"


# ────────────────────────────────────────────────────────────────────────
# Feature discovery
# ────────────────────────────────────────────────────────────────────────
def discover_model_signals() -> list[str]:
    """Return all distinct model_*_score signal_names in the DB. New family
    entrants added to models_capture.py become features on the next re-fit
    automatically."""
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT signal_name FROM signals
            WHERE signal_name LIKE 'model_%_score'
              AND signal_name NOT LIKE 'model_meta_blend_%_score'
            ORDER BY signal_name
        """)
        return [r[0] for r in cur.fetchall()]


def get_feature_names() -> list[str]:
    """Full ordered feature list = family entrants + BBG inputs."""
    return discover_model_signals() + BBG_INPUTS


# ────────────────────────────────────────────────────────────────────────
# Feature matrix builder
# ────────────────────────────────────────────────────────────────────────
def build_feature_matrix(
    horizon: str,
    cutoff_days: int = 14,
    cutoff_end: Optional[datetime] = None,
) -> tuple[np.ndarray, np.ndarray, list[str], list[tuple]]:
    """
    Pull observations for one horizon, pivot to a (n_obs × n_features) matrix.

    Observations are grouped by (ticker, fire_minute) where fire_minute is
    the run_timestamp truncated to the minute. Each fire has multiple model
    runs (one per entrant), so this groups them back into a single row with
    all entrants' scores as columns.

    Returns:
      X            : np.ndarray (n_obs × n_features) — feature scores (NaN imputed → 0)
      y            : np.ndarray (n_obs,) — realized return %
      feature_names: list[str] — column order
      obs_meta     : list[(fire_minute, ticker)] — for audit / backtest
    """
    feature_names = get_feature_names()
    ret_signal = HORIZON_TO_RETURN_SIGNAL[horizon]
    cutoff_end = cutoff_end or datetime.now(timezone.utc)
    cutoff_start = cutoff_end.timestamp() - cutoff_days * 86400

    # Build the (fire_minute, ticker) → {signal_name: value} pivot in Python.
    # SQL aggregation would also work but Python is more flexible when the
    # feature set is dynamic (new entrants discovered each fit).
    features_by_key: dict = defaultdict(dict)
    targets_by_key: dict = {}

    # All signals we care about: features + the target
    all_signals = feature_names + [ret_signal]

    with psycopg.connect(DB_DSN) as conn:
        # 10-minute buckets — predictions_capture fires at :05/:35 (BBG
        # composite + sub-signals) and models_capture fires at :08/:38
        # (model_score entrants). A 10-minute bucket groups both into one
        # observation while staying narrow enough not to bleed across
        # consecutive tournament cycles (which are 30 min apart).
        BUCKET_SECONDS = 600
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_timestamp(floor(EXTRACT(EPOCH FROM r.run_timestamp) / %s) * %s) AS fire_bucket,
                       s.ticker, s.signal_name, s.signal_value
                FROM signals s
                JOIN runs r ON s.run_id = r.run_id
                WHERE s.signal_name = ANY(%s)
                  AND s.signal_value IS NOT NULL
                  AND r.run_timestamp > to_timestamp(%s)
                  AND r.run_timestamp <= %s
                """,
                (BUCKET_SECONDS, BUCKET_SECONDS, all_signals, cutoff_start, cutoff_end),
            )
            for fire_bucket, ticker, sname, val in cur.fetchall():
                key = (fire_bucket, ticker)
                if sname == ret_signal:
                    targets_by_key[key] = float(val)
                else:
                    features_by_key[key][sname] = float(val)

    if not targets_by_key:
        return (np.zeros((0, len(feature_names))), np.zeros(0),
                feature_names, [])

    # Keep only observations that have BOTH a target AND at least one feature
    keys = sorted(k for k in targets_by_key if k in features_by_key)
    n = len(keys)
    f = len(feature_names)
    X = np.full((n, f), np.nan)
    y = np.zeros(n)
    obs_meta = []
    for i, key in enumerate(keys):
        fire_bucket, ticker = key
        feat = features_by_key[key]
        for j, name in enumerate(feature_names):
            if name in feat:
                X[i, j] = feat[name]
        y[i] = targets_by_key[key]
        obs_meta.append((str(fire_bucket), ticker, fire_bucket.isoformat()))

    # ─── Missingness handling ───
    # Drop rows where ALL features are NULL (no signal at all). Newer
    # entrants (added v18, v24) are still maturing their forward-return
    # joins — most observations only have 4-6 of the 32 entrants populated
    # at the moment. Imputing NaN → 0 ("no opinion") for the rest is the
    # honest treatment. OLS learns small weights for entrants with sparse
    # coverage; they grow as data accumulates.
    null_frac = np.isnan(X).mean(axis=1)
    keep_mask = null_frac < 1.0
    X = X[keep_mask]
    y = y[keep_mask]
    obs_meta = [m for m, k in zip(obs_meta, keep_mask) if k]

    # Remaining NaNs → 0 ("entrant had no opinion this fire")
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    return X, y, feature_names, obs_meta


# ────────────────────────────────────────────────────────────────────────
# Standardization
# ────────────────────────────────────────────────────────────────────────
def standardize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z-score each column. Returns (Xz, mu, sigma)."""
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma_safe = np.where(sigma < 1e-9, 1.0, sigma)   # avoid /0 for constant cols
    Xz = (X - mu) / sigma_safe
    return Xz, mu, sigma_safe


# ────────────────────────────────────────────────────────────────────────
# OLS fit
# ────────────────────────────────────────────────────────────────────────
def fit_ols(Xz: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Solve OLS: y = Xz @ w + intercept. Returns (weights, intercept, in_sample_r2)."""
    # Add intercept column
    X_with_intercept = np.column_stack([np.ones(Xz.shape[0]), Xz])
    coef, _, _, _ = np.linalg.lstsq(X_with_intercept, y, rcond=None)
    intercept = float(coef[0])
    weights = coef[1:]
    y_pred = X_with_intercept @ coef
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return weights, intercept, float(r2)


def fit_with_oos_check(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    obs_meta: list[tuple],
    oos_days: int = 3,
) -> dict:
    """
    Fit OLS using the trailing data, with the most-recent oos_days held out
    for out-of-sample R² calculation.

    Returns dict with weights + mu + sigma + diagnostics.
    """
    if len(y) < 10:
        return {"error": f"insufficient observations: {len(y)} < 10"}

    # Split: hold out the last oos_days worth of timestamps as OOS
    n = len(y)
    n_test = max(int(n * 0.15), 50)  # at least 50, or 15% of total
    n_test = min(n_test, n // 2)      # not more than half
    train_idx = np.arange(n - n_test)
    test_idx = np.arange(n - n_test, n)

    # Standardize on training portion only (no peeking)
    Xz_train, mu, sigma = standardize(X[train_idx])
    Xz_test = (X[test_idx] - mu) / sigma
    y_train = y[train_idx]
    y_test = y[test_idx]

    # Fit
    weights, intercept, r2_in = fit_ols(Xz_train, y_train)

    # OOS R²
    y_test_pred = Xz_test @ weights + intercept
    ss_res = ((y_test - y_test_pred) ** 2).sum()
    ss_tot = ((y_test - y_test.mean()) ** 2).sum()
    r2_oos = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Directional IC on OOS
    ic_dir = float(np.mean(np.sign(y_test_pred) * np.sign(y_test))) if len(y_test) else 0.0

    # Top weights for diagnostics
    weighted = [(feature_names[i], float(weights[i])) for i in range(len(weights))]
    weighted.sort(key=lambda x: -abs(x[1]))
    top10 = weighted[:10]

    return {
        "weights":       {feature_names[i]: float(weights[i]) for i in range(len(weights))},
        "intercept":     intercept,
        "mu":            {feature_names[i]: float(mu[i]) for i in range(len(mu))},
        "sigma":         {feature_names[i]: float(sigma[i]) for i in range(len(sigma))},
        "n_train":       int(len(train_idx)),
        "n_test":        int(len(test_idx)),
        "in_sample_r2":  float(r2_in),
        "oos_r2":        r2_oos,
        "oos_ic_dir":    ic_dir,
        "top_weights":   top10,
    }


# ────────────────────────────────────────────────────────────────────────
# Live scoring
# ────────────────────────────────────────────────────────────────────────
def score_observation(
    feature_values: dict[str, float],
    weights: dict[str, float],
    mu: dict[str, float],
    sigma: dict[str, float],
    intercept: float,
) -> float:
    """Compute the meta-blend score for one observation.

    feature_values: {signal_name: value} — partial coverage OK; missing = 0
    Returns the meta-blend score, clipped to ±100.
    """
    total = intercept
    for name, w in weights.items():
        v = feature_values.get(name, 0.0)
        if v is None: v = 0.0
        m = mu.get(name, 0.0)
        s = sigma.get(name, 1.0)
        if s < 1e-9: s = 1.0
        z = (v - m) / s
        total += w * z
    return float(np.clip(total, -100, 100))


# ────────────────────────────────────────────────────────────────────────
# Gate check + persistence
# ────────────────────────────────────────────────────────────────────────
def check_publish_gate(n_obs: int, n_days: int) -> tuple[bool, str]:
    """Returns (ok, reason)."""
    if n_obs < MIN_OBSERVATIONS:
        return False, f"only {n_obs} obs (need {MIN_OBSERVATIONS})"
    if n_days < MIN_TRADING_DAYS:
        return False, f"only {n_days} trading days (need {MIN_TRADING_DAYS})"
    return True, "gate met"


def load_weights() -> Optional[dict]:
    """Load the current weights file. Returns None if not yet written."""
    if not WEIGHTS_PATH.exists():
        return None
    try:
        return json.loads(WEIGHTS_PATH.read_text())
    except Exception:
        return None


def save_weights(payload: dict) -> None:
    """Persist current weights + recent history."""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Keep a rolling history of fits for monitoring
    existing = load_weights() or {"history": []}
    history = existing.get("history", [])
    # Add the current fit to history (keep last 12 weeks)
    for horizon, fit in (payload.get("fits") or {}).items():
        history.append({
            "fit_date":     payload["fit_date"],
            "horizon":      horizon,
            "n_train":      fit.get("n_train"),
            "in_sample_r2": fit.get("in_sample_r2"),
            "oos_r2":       fit.get("oos_r2"),
            "oos_ic_dir":   fit.get("oos_ic_dir"),
        })
    history = history[-36:]   # 12 weeks × 3 horizons
    payload["history"] = history
    WEIGHTS_PATH.write_text(json.dumps(payload, indent=2, default=str))


def save_diagnostics(entry: dict) -> None:
    """Append a diagnostics entry to the running log."""
    DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if DIAGNOSTICS_PATH.exists():
        try:
            existing = json.loads(DIAGNOSTICS_PATH.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    existing = existing[-100:]   # keep last 100 entries
    DIAGNOSTICS_PATH.write_text(json.dumps(existing, indent=2, default=str))


def cosine_similarity(a: dict, b: dict) -> float:
    """Cosine similarity between two weight dicts (for stability tracking)."""
    keys = set(a) | set(b)
    va = np.array([a.get(k, 0) for k in keys])
    vb = np.array([b.get(k, 0) for k in keys])
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na < 1e-9 or nb < 1e-9: return 0.0
    return float(np.dot(va, vb) / (na * nb))
