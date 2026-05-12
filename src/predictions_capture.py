"""
predictions_capture.py — snapshot live trading predictions to rcg_signals DB

Runs on systemd timer every 30 min during market hours. Mirrors the JS
predictiveComposite from trade.html so the dashboard view and the
captured-for-later-analysis view stay in sync.

Each snapshot writes one rcg_signals 'run' with run_type='live_prediction'
and one row per (ticker, signal_name) for every name in bloomberg_prices.json's
watchlist. Schema fields used:
  - signed_score  (-100..+100)  → stored as numeric value
  - magnitude     (0..100)      → stored as numeric value
  - action        (BUY/SELL/etc)→ stored as string
  - per-signal    surge, udv, accel, vwap_slope, range_exp
  - context       live_price, eod_close, intraday_move, intraday_rsi, vol_now,
                  adv, vol_adv_ratio

Phase 2D will join these against forward returns (T+1, T+5, T+30 hourly bars
or T+1d daily bars) to compute IC per sub-signal — i.e., which predictive
component is actually predictive of forward returns.

Source of truth for the JS port at trade.html lines:
  - predictiveComposite()
  - actionLabel()
  - intradayRsi()
  - todayVolume()
  - avgDailyVolume()
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402
import regime_tag  # noqa: E402

PRICES_PATH       = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")
SCREENER_CSV_PATH = Path("/home/nixos/Prod/V1/outputs/long_screener_results.csv")


# ─── helpers (Python port of JS bar utilities) ────────────────────────────────
def _bar_day(b: dict) -> str:
    return (b.get("time") or "").split(" ")[0]


def _today_bars(bars):
    if not bars:
        return []
    last = _bar_day(bars[-1])
    return [b for b in bars if _bar_day(b) == last]


def _prior_day_bars(bars):
    if not bars:
        return []
    last = _bar_day(bars[-1])
    return [b for b in bars if _bar_day(b) != last]


def _bars_by_day(bars):
    out: dict[str, list] = {}
    for b in bars or []:
        d = _bar_day(b)
        if not d:
            continue
        out.setdefault(d, []).append(b)
    return out


# ─── predictive sub-signals (port of JS) ──────────────────────────────────────
def volume_surge(bars):
    if not bars or len(bars) < 6:
        return None
    cur = bars[-1].get("volume", 0)
    prior5 = [b.get("volume", 0) for b in bars[-6:-1]]
    avg = sum(prior5) / len(prior5)
    return (cur / avg) if avg else None


def up_down_vol_ratio(bars, n=10):
    if not bars or len(bars) < n + 1:
        return None
    up = dn = 0
    for i in range(-n, 0):
        b = bars[i]
        prev_close = bars[i - 1].get("close", b.get("open", 0))
        v = b.get("volume", 0)
        if b.get("close", 0) >= prev_close:
            up += v
        else:
            dn += v
    tot = up + dn
    return (up / tot) if tot else None


def acceleration(bars):
    if not bars or len(bars) < 7:
        return None
    c = [b.get("close", 0) for b in bars]
    if c[-4] == 0 or c[-7] == 0:
        return None
    recent = (c[-1] - c[-4]) / c[-4]
    prior  = (c[-4] - c[-7]) / c[-7]
    return recent - prior


def vwap_slope(bars):
    today = _today_bars(bars)
    if len(today) < 3:
        return None
    cum_pv = cum_v = 0.0
    vw = []
    for b in today:
        tp = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3.0
        v  = b.get("volume", 0)
        cum_pv += tp * v
        cum_v  += v
        if cum_v:
            vw.append(cum_pv / cum_v)
    if len(vw) < 2 or vw[0] == 0:
        return None
    return (vw[-1] - vw[0]) / vw[0]


def range_expansion(bars):
    if not bars or len(bars) < 14:
        return None
    today = _today_bars(bars)
    yest  = _prior_day_bars(bars)[-7:]
    if not today or not yest:
        return None
    y_hi = max(b.get("high", 0) for b in yest)
    y_lo = min(b.get("low",  0) for b in yest)
    c = today[-1].get("close", 0)
    if c > y_hi and y_hi:
        return (c - y_hi) / y_hi
    if c < y_lo and y_lo:
        return (c - y_lo) / y_lo
    return 0.0


def intraday_rsi(bars, period=14):
    if not bars or len(bars) < period + 1:
        return None
    c = [b.get("close", 0) for b in bars]
    g = l = 0.0
    for i in range(len(c) - period, len(c)):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            l += -d
    if l == 0:
        return 100.0
    rs = (g / period) / (l / period)
    return 100 - (100 / (1 + rs))


def today_volume(bars):
    t = _today_bars(bars)
    if not t:
        return None
    return sum(b.get("volume", 0) for b in t)


def avg_daily_volume(bars):
    by_day = _bars_by_day(bars)
    days = sorted(by_day.keys())
    if len(days) < 2:
        return None
    completed = days[:-1]  # exclude latest (partial)
    if not completed:
        return None
    daily = [sum(b.get("volume", 0) for b in by_day[d]) for d in completed]
    return sum(daily) / len(daily)


def predictive_composite(bars) -> dict:
    surge = volume_surge(bars)
    udv   = up_down_vol_ratio(bars, 10)
    accel = acceleration(bars)
    vwap  = vwap_slope(bars)
    rng   = range_expansion(bars)

    if accel is not None and abs(accel) > 0.001:
        dir_hint = 1 if accel > 0 else -1
    elif udv is not None:
        dir_hint = 1 if udv > 0.5 else -1 if udv < 0.5 else 0
    else:
        dir_hint = 0

    surge_c = 0.0 if surge is None else max(0.0, min(25.0, (surge - 1) * 25)) * dir_hint
    udv_c   = 0.0 if udv   is None else max(-20.0, min(20.0, (udv - 0.5) * 80))
    accel_c = 0.0 if accel is None else max(-20.0, min(20.0, accel * 2000))
    vwap_c  = 0.0 if vwap  is None else max(-15.0, min(15.0, vwap * 5000))
    range_c = 0.0 if rng   is None else (
        max(-20.0, min(20.0, (1 if rng > 0 else -1 if rng < 0 else 0) * (20 if rng != 0 else 0)))
    )

    signed_raw = surge_c + udv_c + accel_c + vwap_c + range_c
    signed = max(-100, min(100, round(signed_raw)))
    return {
        "signed_score": signed,
        "magnitude":    abs(signed),
        "surge": surge, "udv": udv, "accel": accel,
        "vwap_slope": vwap, "range_exp": rng,
        "direction_hint": dir_hint,
    }


def action_label(signed_score, fundamental_composite, intraday_move):
    move = intraday_move or 0.0
    s = signed_score or 0
    if s <= -65 and move <= -0.02: return "BREAKDOWN"
    if s <= -65 and abs(move) < 0.02: return "PRE-BREAKDOWN"
    if s >=  65 and move >=  0.02: return "BREAKOUT"
    if s >=  65 and abs(move) < 0.02: return "PRE-BREAKOUT"
    if (fundamental_composite or 0) >= 0.7 and s >= 25: return "STRONG"
    if s <= -25: return "WEAKENING"
    if s >=  25: return "WATCH"
    return "NEUTRAL"


# ─── load helpers ─────────────────────────────────────────────────────────────
def load_eod_map():
    """Load EOD closes from latest screener CSV. Returns {ticker: last_price}."""
    out: dict[str, float] = {}
    if not SCREENER_CSV_PATH.exists():
        return out
    with SCREENER_CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            t = row.get("ticker")
            if not t:
                continue
            try:
                lp = float(row.get("last_price") or 0)
                if lp > 0:
                    out[t] = lp
            except (ValueError, TypeError):
                pass
    return out


def load_fundamental_composite_map():
    """Load fundamental composite_score from screener CSV."""
    out: dict[str, float] = {}
    if not SCREENER_CSV_PATH.exists():
        return out
    with SCREENER_CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            t = row.get("ticker")
            if not t:
                continue
            try:
                cs = float(row.get("composite_score") or 0)
                out[t] = cs
            except (ValueError, TypeError):
                pass
    return out


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    if not PRICES_PATH.exists():
        print(f"[predictions_capture] no bloomberg_prices.json at {PRICES_PATH}")
        return

    bbg = json.loads(PRICES_PATH.read_text())
    watchlist = bbg.get("watchlist") or {}
    if not watchlist:
        print("[predictions_capture] empty watchlist — skipping")
        return

    eod_map  = load_eod_map()
    comp_map = load_fundamental_composite_map()
    bbg_generated_at = bbg.get("generated_at")

    regime = regime_tag.compute_regime()
    print(f"[predictions] regime: {regime['regime_label']}  (vix={regime['vix']}, spy_5d={regime['spy_5d_pct']}%)")

    run_id = sdb.record_run(
        run_type="live_prediction",
        config={
            "source":            "predictions_capture.py",
            "bbg_generated_at":  bbg_generated_at,
            "n_watchlist":       len(watchlist),
            "screener_csv_seen": SCREENER_CSV_PATH.exists(),
            "regime":            regime,
        },
    )
    if not run_id:
        print("[predictions_capture] signals_db unavailable — capture skipped")
        return

    n_tickers = n_signals = 0
    for ticker, w in watchlist.items():
        if not w or w.get("error"):
            continue
        bars = w.get("bars") or []
        if len(bars) < 5:
            continue

        pred = predictive_composite(bars)
        live = w.get("price")
        eod  = eod_map.get(ticker)
        intraday_move = ((live - eod) / eod) if (live and eod and eod > 0) else None
        rsi  = intraday_rsi(bars)
        vnow = today_volume(bars)
        adv  = avg_daily_volume(bars)
        vadv = (vnow / adv) if (vnow is not None and adv) else None
        comp = comp_map.get(ticker)
        action = action_label(pred["signed_score"], comp, intraday_move)

        # Each numeric signal as a separate row keyed by (run_id, ticker, signal_name).
        numerics = {
            "pred_signed_score": pred["signed_score"],
            "pred_magnitude":    pred["magnitude"],
            "pred_surge":        pred["surge"],
            "pred_udv":          pred["udv"],
            "pred_accel":        pred["accel"],
            "pred_vwap_slope":   pred["vwap_slope"],
            "pred_range_exp":    pred["range_exp"],
            "live_price":        live,
            "eod_close":         eod,
            "intraday_move":     intraday_move,
            "intraday_rsi":      rsi,
            "vol_now":           vnow,
            "adv":               adv,
            "vol_adv_ratio":     vadv,
            "fundamental_composite": comp,
        }
        for name, val in numerics.items():
            if val is None:
                continue
            sdb.record_signal(run_id, ticker, name, value=float(val))
            n_signals += 1

        # Action label as a string-typed signal.
        sdb.record_signal(run_id, ticker, "pred_action", string=action)
        n_signals += 1
        n_tickers += 1

    sdb.finalize_run(run_id, n_out=n_tickers)
    print(f"[predictions_capture] run_id={run_id}  "
          f"tickers={n_tickers}  signals={n_signals}  "
          f"bbg_age={bbg_generated_at}")


if __name__ == "__main__":
    main()
