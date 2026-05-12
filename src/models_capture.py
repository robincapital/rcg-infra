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

Roster (10 entrants):
  momentum_5bar       — 5-bar return %
  mean_rev_20         — 20-bar z-score, sign-flipped (counter-trend)
  rsi_extreme_14      — RSI<30 bull, RSI>70 bear (reversion)
  sma_cross_5_20      — (sma5 − sma20) / sma20 × 100  (trend)
  ema_cross_12_26     — MACD-style EMA cross  (trend)
  bollinger_pos_20    — position within ±2σ band, sign-flipped (mean-rev)
  donchian_break_20   — 20-bar high/low breakout strength (trend)
  lr_slope_20         — 20-bar linear regression slope, normalized (trend)
  arima_1             — AR(1) one-step-ahead forecast vs current price
  combo_trend         — equal-weight blend of trend models (ensemble)

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
import regime_tag  # noqa: E402

PRICES_PATH = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")


# ─── Helpers ────────────────────────────────────────────────────────────────
def _closes(bars):
    return [b.get("close") for b in bars if b.get("close")]


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


# ─── Models — each takes a list of bars, returns a directional score ────────
def model_momentum(bars):
    """5-bar return × 100. Bull >0, bear <0."""
    closes = _closes(bars)
    if len(closes) < 6 or closes[-6] == 0:
        return None
    return ((closes[-1] - closes[-6]) / closes[-6]) * 100


def model_mean_reversion(bars):
    """20-bar z-score, sign-flipped. Stretched up → bear; stretched down → bull."""
    closes = _closes(bars)
    if len(closes) < 20:
        return None
    window = closes[-20:]
    mean = sum(window) / 20
    var  = sum((c - mean) ** 2 for c in window) / 20
    sd   = var ** 0.5
    if sd <= 0:
        return None
    z = (closes[-1] - mean) / sd
    return -z * 50


def model_rsi_extreme(bars, period=14):
    """RSI extreme reversion: RSI<30 → bull score, RSI>70 → bear score."""
    closes = _closes(bars)
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
    if rsi < 30:  return min(100, (30 - rsi) * 5)
    if rsi > 70:  return max(-100, -(rsi - 70) * 5)
    return 0.0


def model_sma_cross(bars):
    """SMA-5 vs SMA-20: positive when fast > slow (golden cross territory)."""
    closes = _closes(bars)
    if len(closes) < 20:
        return None
    sma5  = sum(closes[-5:])  / 5
    sma20 = sum(closes[-20:]) / 20
    if sma20 == 0: return None
    return max(-100.0, min(100.0, ((sma5 - sma20) / sma20) * 100))


def model_ema_cross(bars):
    """MACD-style: (EMA12 − EMA26) / EMA26 × 100, clipped to ±100."""
    closes = _closes(bars)
    if len(closes) < 26:
        return None
    ema12 = _ema(closes[-26:], 12)
    ema26 = _ema(closes[-26:], 26)
    if ema12 is None or ema26 is None or ema26 == 0:
        return None
    return max(-100.0, min(100.0, ((ema12 - ema26) / ema26) * 100))


def model_bollinger_position(bars, period=20, k=2.0):
    """
    Position within ±k·σ band, sign-flipped:
      price at upper band → -100 (overbought, bear)
      price at lower band → +100 (oversold, bull)
      price at midline    →    0
    """
    closes = _closes(bars)
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var  = sum((c - mean) ** 2 for c in window) / period
    sd   = var ** 0.5
    if sd <= 0: return None
    upper = mean + k * sd
    lower = mean - k * sd
    if upper == lower: return 0.0
    pos = (closes[-1] - mean) / (k * sd)   # ~ -1 .. +1 inside band
    return max(-100.0, min(100.0, -pos * 100))


def model_donchian_breakout(bars, period=20):
    """
    20-bar Donchian breakout strength:
      close above prior 20-bar high → bull (proportional to overshoot)
      close below prior 20-bar low  → bear
      inside channel → 0
    """
    closes = _closes(bars)
    if len(closes) < period + 1:
        return None
    prior = closes[-(period + 1):-1]
    hi = max(prior); lo = min(prior)
    if hi == lo: return 0.0
    last = closes[-1]
    if last > hi:
        # overshoot vs channel width
        return min(100.0, (last - hi) / (hi - lo) * 100)
    if last < lo:
        return max(-100.0, (last - lo) / (hi - lo) * 100)
    # inside channel — neutral
    return 0.0


def model_lr_slope(bars, period=20):
    """
    20-bar linear-regression slope on closes, normalized by mean price → %/bar.
    Positive = uptrend, negative = downtrend. Multiplied by period and clipped.
    """
    closes = _closes(bars)
    if len(closes) < period:
        return None
    y = closes[-period:]
    n = period
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = sum(y) / n
    num = sum((xs[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0 or mean_y == 0:
        return None
    slope = num / den               # price units per bar
    pct_per_bar = slope / mean_y * 100
    # Project over the period for a comparable scale, clip ±100
    return max(-100.0, min(100.0, pct_per_bar * period))


def model_arima_1(bars, period=30):
    """
    AR(1) on log-returns. Fit r_t = c + φ·r_{t-1} on last `period` bars,
    score = forecasted next-bar return × 100 (directional).
    """
    import math
    closes = _closes(bars)
    if len(closes) < period + 2:
        return None
    series = closes[-(period + 1):]
    rets = []
    for i in range(1, len(series)):
        if series[i - 1] <= 0 or series[i] <= 0:
            return None
        rets.append(math.log(series[i] / series[i - 1]))
    if len(rets) < 5:
        return None
    # OLS: r_t = c + φ · r_{t-1}
    x = rets[:-1]; y = rets[1:]
    n = len(x)
    mx = sum(x) / n; my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = sum((x[i] - mx) ** 2 for i in range(n))
    if den == 0: return None
    phi = num / den
    c   = my - phi * mx
    forecast = c + phi * rets[-1]      # next-bar log return
    return max(-100.0, min(100.0, forecast * 100 * 100))  # log-ret × 10,000 bps-ish, clipped


def model_combo_trend(bars):
    """Equal-weight ensemble of trend models (sma_cross, ema_cross, lr_slope, donchian)."""
    parts = [
        model_sma_cross(bars),
        model_ema_cross(bars),
        model_lr_slope(bars),
        model_donchian_breakout(bars),
    ]
    valid = [p for p in parts if p is not None]
    if len(valid) < 2:
        return None
    return sum(valid) / len(valid)


# ─── Tournament roster ──────────────────────────────────────────────────────
MODELS = [
    ("momentum_5bar",     model_momentum),
    ("mean_rev_20",       model_mean_reversion),
    ("rsi_extreme_14",    model_rsi_extreme),
    ("sma_cross_5_20",    model_sma_cross),
    ("ema_cross_12_26",   model_ema_cross),
    ("bollinger_pos_20",  model_bollinger_position),
    ("donchian_break_20", model_donchian_breakout),
    ("lr_slope_20",       model_lr_slope),
    ("arima_1",           model_arima_1),
    ("combo_trend",       model_combo_trend),
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

    # Tag every fire of this batch with the current market regime so the
    # leaderboard can compute IC stratified by regime later. Same regime is
    # written to every model's run in this batch (they all fire at the same
    # moment, so they share the regime).
    regime = regime_tag.compute_regime()
    print(f"[models] regime: {regime['regime_label']}  (vix={regime['vix']}, spy_5d={regime['spy_5d_pct']}%)")

    runs = {}
    n_signals = 0
    for model_name, fn in MODELS:
        run_id = sdb.record_run(
            run_type="model_score",
            config={"model": model_name, "bbg_age": bbg.get("generated_at"),
                    "n_watchlist": len(watchlist),
                    "regime": regime},
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
            try:
                score = fn(bars)
            except Exception as e:
                print(f"[models]  {model_name} {ticker}: {e}")
                continue
            if score is None:
                continue
            sdb.record_signal(run_id, ticker, f"model_{model_name}_score",
                              value=float(score))
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
