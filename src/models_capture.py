"""
models_capture.py — run every prediction model in the tournament + capture scores

Runs every 30 min during market hours. Each model implements a single
score(ticker, bars, eod) -> float method that returns a directional score
(typically -100 to +100, but anything works since we'll rank by IC).

The "tournament" is just every model writing to the signals table with
run_type='model_score' and signal_name=f"model_{model_name}_score". Forward-
return capture already joins them with realized returns automatically.
The leaderboard is just a SQL query over (signal_name, realized_return)
asking "which models had the highest IC over the last N days".

Initial entrants:
  momentum     — 5-bar return (simple trend)
  mean_rev     — 20-bar z-score (counter-trend)

To add a new model: write a function that takes (bars) and returns a float;
add to MODELS list. No other changes needed — forward returns and IC
computation pick it up automatically.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402

PRICES_PATH = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")


# ─── Models — each takes a list of bars, returns a directional score ────────
def model_momentum(bars):
    """5-bar return × 100. Bull >0, bear <0."""
    if not bars or len(bars) < 6:
        return None
    closes = [b.get("close") for b in bars if b.get("close")]
    if len(closes) < 6 or closes[-6] == 0:
        return None
    return ((closes[-1] - closes[-6]) / closes[-6]) * 100


def model_mean_reversion(bars):
    """
    20-bar z-score, sign-flipped (z>0 = stretched UP = expects reversion DOWN
    = bear score). Score in roughly [-2σ × 50, +2σ × 50] ~ [-100, +100].
    """
    if not bars or len(bars) < 20:
        return None
    closes = [b.get("close") for b in bars if b.get("close")]
    if len(closes) < 20:
        return None
    window = closes[-20:]
    mean = sum(window) / 20
    var  = sum((c - mean) ** 2 for c in window) / 20
    sd   = var ** 0.5
    if sd <= 0:
        return None
    z = (closes[-1] - mean) / sd
    return -z * 50   # invert: stretched up → expect reversion → bear signal


def model_rsi_extreme(bars, period=14):
    """RSI extreme reversion: RSI<30 → bull score, RSI>70 → bear score."""
    if not bars or len(bars) < period + 1:
        return None
    closes = [b.get("close") for b in bars if b.get("close")]
    if len(closes) < period + 1:
        return None
    g = l = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0: g += d
        else:     l += -d
    if l == 0: return -50.0
    rs  = (g / period) / (l / period)
    rsi = 100 - (100 / (1 + rs))
    # Map RSI → reversion score: 30 → +70 (oversold = buy), 70 → -70 (overbought = sell)
    if rsi < 30:  return min(100, (30 - rsi) * 5)      # +50 at RSI 20, +100 at RSI 10
    if rsi > 70:  return max(-100, -(rsi - 70) * 5)    # -50 at RSI 80, -100 at RSI 90
    return 0.0  # neutral zone


def model_sma_cross(bars):
    """SMA-5 vs SMA-20: positive when fast SMA > slow SMA (golden cross territory)."""
    if not bars or len(bars) < 20:
        return None
    closes = [b.get("close") for b in bars if b.get("close")]
    if len(closes) < 20:
        return None
    sma5  = sum(closes[-5:])  / 5
    sma20 = sum(closes[-20:]) / 20
    if sma20 == 0: return None
    # Score = (sma5 - sma20) / sma20 * 100, clipped to ±100
    return max(-100.0, min(100.0, ((sma5 - sma20) / sma20) * 100))


# ─── Tournament roster ──────────────────────────────────────────────────────
MODELS = [
    ("momentum_5bar",  model_momentum),
    ("mean_rev_20",    model_mean_reversion),
    ("rsi_extreme_14", model_rsi_extreme),
    ("sma_cross_5_20", model_sma_cross),
]


def main():
    if not PRICES_PATH.exists():
        print(f"[models] no bloomberg_prices.json at {PRICES_PATH}")
        return
    bbg = json.loads(PRICES_PATH.read_text())
    watchlist = bbg.get("watchlist") or {}
    if not watchlist:
        print("[models] empty watchlist")
        return

    # Open one run per model so the leaderboard query can group cleanly
    runs = {}
    n_signals = 0
    for model_name, fn in MODELS:
        run_id = sdb.record_run(
            run_type=f"model_score",
            config={"model": model_name, "bbg_age": bbg.get("generated_at"),
                    "n_watchlist": len(watchlist)},
        )
        if not run_id:
            print(f"[models] DB unavailable — skipping {model_name}")
            continue
        runs[model_name] = run_id

        for ticker, w in watchlist.items():
            if not w or w.get("error"):
                continue
            bars = w.get("bars") or []
            if not bars:
                continue
            score = fn(bars)
            if score is None:
                continue
            sdb.record_signal(run_id, ticker, f"model_{model_name}_score",
                              value=float(score))
            # Also record entry price (live) so forward_returns can match
            live = w.get("price")
            if live is not None:
                sdb.record_signal(run_id, ticker, "live_price", value=float(live))
            n_signals += 1

        sdb.finalize_run(run_id, n_out=len([t for t, w in watchlist.items()
                                            if w and not w.get("error") and w.get("bars")]))

    print(f"[models] {len(runs)} models scored · {n_signals} signal rows captured")
    for name, rid in runs.items():
        print(f"  {name:20s} run_id={rid}")


if __name__ == "__main__":
    main()
