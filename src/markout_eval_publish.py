"""
markout_eval_publish.py — top-level publisher for the markout dashboard.

Iterates every tournament model × every horizon × every slippage assumption,
runs the trade simulation + supporting-panel computations from markout_eval,
and writes outputs/markouts.json in the schema declared in
docs/markout_dashboard_spec.md §7.3.

Run cadence: nightly cron (02:00 ET) + on-demand "refresh now" button on
the dashboard. See systemd/markouts.{timer,service} (Day 4 deliverable).

What gets written
─────────────────
For each model × horizon:
  - 3 cumulative P&L curves (gross + net at 0/5/10 bps slippage)
  - Summary stats (cum return, Sharpe, max DD, hit rate, trade count)
  - Calibration buckets
  - Rolling 30d IC time-series
  - Per-ticker P&L contribution (sorted desc)

Plus globally:
  - Equal-weighted watchlist benchmark daily returns
  - SPY benchmark daily returns
  - Champions-only model correlation matrix (~12 × 12 by default)

Benchmark gotcha: as of v27 Day 3 the EW-watchlist benchmark is a stub
that returns zeros — we don't yet have a clean daily-watchlist-return
series. v2 will compute it from SEP daily prices joined to the active
watchlist. For now the dashboard shows the gross/net curves alone; the
EW + SPY lines render as flat zero baselines until that data lands.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/home/nixos/Prod/V1/src")

import psycopg
from markout_eval import (
    simulate, compute_calibration, compute_rolling_ic, compute_model_correlation,
    ENTRY_THRESHOLD, EXIT_THRESHOLD, MAX_CONCURRENT,
    PLATFORM_FEE_BPS_YEAR, SimResult,
)
from models_leaderboard import family_from_model


OUTPUT_PATH    = Path("/home/nixos/Prod/V1/outputs/markouts.json")
HORIZONS       = ["30min", "60min", "4h"]
SLIPPAGE_TIERS = [0.0, 5.0, 10.0]      # bps/side options shown in UI
DEFAULT_SLIP   = 5.0
LOOKBACK_DAYS  = 90                     # trailing window for sim + supporting panels
DB_DSN         = "host=/run/postgresql user=nixos dbname=rcg_signals"


# ────────────────────────────────────────────────────────────────────────
# Model discovery — list all model_*_score signals currently in the DB
# ────────────────────────────────────────────────────────────────────────
def discover_all_models() -> list[str]:
    """Return all distinct model_*_score names in the signals table,
    stripped of 'model_' prefix and '_score' suffix.

    e.g. 'model_momentum_5bar_score' → 'momentum_5bar'.
    """
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT signal_name FROM signals
            WHERE signal_name LIKE 'model_%_score'
            ORDER BY signal_name
        """)
        names = []
        for (sname,) in cur.fetchall():
            stem = sname[len("model_"):-len("_score")]
            names.append(stem)
    return names


# ────────────────────────────────────────────────────────────────────────
# Per-model payload — what the dashboard reads for each model variant
# ────────────────────────────────────────────────────────────────────────
def build_model_payload(
    model_stem: str,
    horizon:    str,
    lookback:   int = LOOKBACK_DAYS,
) -> tuple[Optional[dict], Optional[SimResult]]:
    """
    Run simulation at default slippage + 0 + 10 bps, then bundle into the
    dashboard JSON schema. Returns (payload_dict, default_sim_result) so
    the caller can stash the SimResult for the cross-model correlation
    matrix.

    If the model has zero fires above threshold in the lookback window,
    we still emit a minimal record (the dashboard will show it as
    "no trades triggered" rather than 404).
    """
    # Run sim at default slippage first — this is the "headline" curve
    sim_default = simulate(model_stem, horizon, slippage_bps=DEFAULT_SLIP,
                           cutoff_days=lookback)

    # If no fires above threshold, return minimal record
    if sim_default.n_fires == 0:
        return ({
            "model":   model_stem,
            "horizon": horizon,
            "family":  family_from_model(model_stem),
            "n_fires": 0,
            "n_trades": 0,
            "note":    "no fires in lookback window",
        }, sim_default)

    # Also run at 0 and 10 bps for the slippage toggle in UI
    sim_zero = simulate(model_stem, horizon, slippage_bps=0.0,    cutoff_days=lookback)
    sim_high = simulate(model_stem, horizon, slippage_bps=10.0,   cutoff_days=lookback)

    # Per-ticker contribution sorted desc by |cum_pnl|
    pt = sorted(
        [
            {"ticker": t, **stats}
            for t, stats in sim_default.per_ticker_contribution.items()
        ],
        key=lambda x: -abs(x.get("cum_pnl", 0)),
    )

    # Calibration + rolling IC (computed from raw score/return pairs, not the sim)
    calibration = compute_calibration(model_stem, horizon, cutoff_days=lookback)
    rolling_ic  = compute_rolling_ic(model_stem, horizon,
                                      window_days=30, cutoff_days=lookback)

    # Daily series — emit dates as ISO strings for JSON
    def _dser(d: dict) -> list[dict]:
        return [{"date": str(k), "value": v} for k, v in sorted(d.items())]

    payload = {
        "model":     model_stem,
        "horizon":   horizon,
        "family":    family_from_model(model_stem),
        "n_fires":   sim_default.n_fires,
        "n_trades":  sim_default.n_trades,
        "n_open_at_end": sim_default.n_open_at_end,
        "n_long":    sim_default.n_long,
        "n_short":   sim_default.n_short,
        "avg_hold_trading_minutes": round(sim_default.avg_hold_trading_minutes, 1),
        "hit_rate":  round(sim_default.hit_rate, 4),

        # Headline numbers per slippage assumption
        "summary": {
            "0bps":  {"cum_return": round(sim_zero.cum_return_net,    6),
                       "sharpe":     round(sim_zero.sharpe_net,        2),
                       "max_dd":     round(sim_zero.max_dd_net,        6)},
            "5bps":  {"cum_return": round(sim_default.cum_return_net, 6),
                       "sharpe":     round(sim_default.sharpe_net,     2),
                       "max_dd":     round(sim_default.max_dd_net,     6)},
            "10bps": {"cum_return": round(sim_high.cum_return_net,    6),
                       "sharpe":     round(sim_high.sharpe_net,        2),
                       "max_dd":     round(sim_high.max_dd_net,        6)},
            "gross": {"cum_return": round(sim_default.cum_return_gross, 6)},
        },

        # Daily series for the headline chart
        "equity_gross":         _dser(sim_default.daily_equity_gross),
        "equity_net_0bps":      _dser(sim_zero.daily_equity_net),
        "equity_net_5bps":      _dser(sim_default.daily_equity_net),
        "equity_net_10bps":     _dser(sim_high.daily_equity_net),

        # Supporting-panel data
        "calibration":          calibration,
        "rolling_ic_30d":       rolling_ic,
        "per_ticker":           pt[:30],     # top 30 by |pnl| for the heatmap
    }
    return payload, sim_default


# ────────────────────────────────────────────────────────────────────────
# Benchmarks — equal-weighted watchlist + SPY
# ────────────────────────────────────────────────────────────────────────
def build_benchmark_payload(lookback: int = LOOKBACK_DAYS) -> dict:
    """
    v27 Day 3: emits stub zero-return series for both benchmarks. v2 will
    pull EW-watchlist daily returns from SEP closes joined to the watchlist
    + SPY daily returns from a separate feed.

    Schema kept stable so the dashboard binds against the same keys later.
    """
    # Just emit a placeholder note + empty arrays. Dashboard renders these
    # as flat zero baselines and shows a "benchmark data pending" tooltip.
    return {
        "ew_watchlist": {
            "daily_returns": [],
            "cum":            [],
            "note":           "benchmark data pending — Phase B daily markouts",
        },
        "spy": {
            "daily_returns": [],
            "cum":            [],
            "note":           "benchmark data pending — Phase B daily markouts",
        },
    }


# ────────────────────────────────────────────────────────────────────────
# Champion selection for correlation matrix
# ────────────────────────────────────────────────────────────────────────
def pick_champions(model_payloads: list[dict]) -> list[str]:
    """
    From the per-model payloads, pick one champion per (family, horizon).
    Champion = highest cum_return_net at 5 bps slippage among models with
    n_trades >= 3 (small-sample guard — we want at least 3 round-trips
    before calling something a champion).

    Returns a list of model labels in the format "family_stem|horizon".
    """
    by_family_horizon: dict = {}
    for p in model_payloads:
        if p.get("n_trades", 0) < 3:
            continue
        cum = p.get("summary", {}).get("5bps", {}).get("cum_return", 0)
        key = (p["family"], p["horizon"])
        existing = by_family_horizon.get(key)
        if existing is None or cum > existing[1]:
            by_family_horizon[key] = (p["model"], cum)
    return [f"{model}|{horizon}" for (_, horizon), (model, _) in by_family_horizon.items()]


# ────────────────────────────────────────────────────────────────────────
# Main entry — write outputs/markouts.json
# ────────────────────────────────────────────────────────────────────────
def main(lookback: int = LOOKBACK_DAYS) -> dict:
    t0 = time.time()
    print(f"[markout_publish] starting, lookback={lookback}d")

    all_models = discover_all_models()
    print(f"[markout_publish] discovered {len(all_models)} model stems in DB")

    model_payloads: list[dict] = []
    sim_results_by_label: dict[str, SimResult] = {}
    n_with_trades = 0

    # Most tournament entrants have ONE score per fire (no horizon variant).
    # Only meta_blend has separate per-horizon variants (meta_blend_30min,
    # meta_blend_60min, meta_blend_4h) — those are encoded in the stem
    # itself. So we emit one row per signal name. The "horizon" label
    # reflects which forward-return window the model was trained on
    # (only meta_blend cares; everything else gets "n/a").
    for stem in all_models:
        # Extract horizon from name for meta_blend variants
        horizon_label = "n/a"
        for h in HORIZONS:
            if stem.endswith(f"_{h}"):
                horizon_label = h
                break
        try:
            payload, sim = build_model_payload(stem, horizon_label, lookback)
            if payload is None: continue
            model_payloads.append(payload)
            label = f"{stem}|{horizon_label}"
            sim_results_by_label[label] = sim
            if payload.get("n_trades", 0) > 0:
                n_with_trades += 1
        except Exception as e:
            print(f"  ! {stem}: {type(e).__name__}: {e}")

    # Champions for the correlation matrix
    champions = pick_champions(model_payloads)
    champion_sims = [sim_results_by_label[lbl] for lbl in champions
                     if lbl in sim_results_by_label]
    correlation = compute_model_correlation(champion_sims)

    # Mark champion flag on payloads
    champion_set = set(champions)
    for p in model_payloads:
        lbl = f"{p['model']}|{p['horizon']}"
        p["is_champion"] = lbl in champion_set

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_rules": {
            "entry_threshold":   ENTRY_THRESHOLD,
            "exit_threshold":    EXIT_THRESHOLD,
            "max_concurrent":    MAX_CONCURRENT,
            "weighting":         "equal",
            "long_short":        True,
            "rth_only":          True,
            "marking_horizon":   "30min",
        },
        "cost_model": {
            "platform_fee_bps_per_year":    PLATFORM_FEE_BPS_YEAR,
            "default_slippage_bps_per_side": DEFAULT_SLIP,
            "slippage_tiers":               SLIPPAGE_TIERS,
        },
        "lookback_days":  lookback,
        "horizons":       HORIZONS,
        "benchmarks":     build_benchmark_payload(lookback),
        "champions":      champions,
        "correlation":    correlation,
        "models":         model_payloads,
        "summary": {
            "n_model_stems":         len(all_models),
            "n_model_horizon_rows":  len(model_payloads),
            "n_with_trades":         n_with_trades,
            "n_champions":           len(champions),
            "elapsed_seconds":       round(time.time() - t0, 1),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"[markout_publish] wrote {OUTPUT_PATH} "
          f"({OUTPUT_PATH.stat().st_size:,} bytes) "
          f"· {len(model_payloads)} rows · "
          f"{n_with_trades} with trades · "
          f"elapsed {out['summary']['elapsed_seconds']}s")

    return out


if __name__ == "__main__":
    main()
