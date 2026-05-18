"""
models_capture.py — parameterized tournament + per-fire regime tagging

Runs every 30 min during market hours. Each entrant implements
score(bars) -> float | None and returns a directional score (typically
-100 to +100, though magnitude is ranked by IC, not enforced).

Roster is organized by FAMILY. Each family has multiple parameter variants.
All variants keep running indefinitely — even underperformers — because a
"dead" model usually just means we're in the wrong regime for it. When
conditions flip, the formerly-dead variant typically revives. The
leaderboard surfaces the per-family champion (highest IC over the trailing
window) but no variant is ever silenced.

Every fire writes the current regime to runs.config_json so per-(model,
horizon, regime) IC can be computed downstream.

Families (and parameter sweeps):
  momentum         — lookback ∈ {3, 5, 8, 13, 21}                       (5)
  mean_reversion   — z-score window ∈ {10, 20, 40}                      (3)
  rsi_extreme      — period ∈ {7, 14, 21}                               (3)
  sma_cross        — (fast, slow) ∈ {(5,20), (10,50), (20,100)}         (3)
  ema_cross        — (fast, slow) ∈ {(8,21), (12,26), (20,50)}          (3)
  bollinger_pos    — (period, k) ∈ {(20,2), (20,2.5), (50,2)}           (3)
  donchian_break   — period ∈ {10, 20, 55}                              (3)
  lr_slope         — period ∈ {10, 20, 40}                              (3)
  arima            — period ∈ {20, 30, 50}                              (3)
  combo_trend      — ensemble of trend members                          (1)
  combo_meanrev    — ensemble of MR members                             (1)
                                                                       ──
                                                                Total = 31
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402
import regime_tag  # noqa: E402
import quant_signals as qs  # noqa: E402  # v24 — pattern + cross-sectional signals

PRICES_PATH = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")


# ─── Helpers ────────────────────────────────────────────────────────────────
def _closes(bars):
    return [b.get("close") for b in bars if b.get("close")]


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


# ─── Factory functions per family ───────────────────────────────────────────
# Each returns a fn(bars) -> float|None scoring function configured with
# the requested parameter combo. Wrapping in factories lets us run multiple
# parameter variants in parallel within one tournament fire.

def make_momentum(lookback: int):
    """N-bar return × 100. Bull > 0, bear < 0."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < lookback + 1 or c[-(lookback + 1)] == 0: return None
        return ((c[-1] - c[-(lookback + 1)]) / c[-(lookback + 1)]) * 100
    return fn


def make_mean_reversion(window: int):
    """Z-score, sign-flipped. Stretched up → bear; stretched down → bull."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < window: return None
        w = c[-window:]
        m = sum(w) / window
        sd = (sum((x - m) ** 2 for x in w) / window) ** 0.5
        if sd <= 0: return None
        return -((c[-1] - m) / sd) * 50
    return fn


def make_rsi_extreme(period: int):
    """RSI extreme reversion: RSI < 30 → bull, RSI > 70 → bear."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < period + 1: return None
        g = l = 0.0
        for i in range(len(c) - period, len(c)):
            d = c[i] - c[i - 1]
            if d > 0: g += d
            else:     l += -d
        if l == 0: return -50.0
        rs  = (g / period) / (l / period)
        rsi = 100 - (100 / (1 + rs))
        if rsi < 30: return min(100, (30 - rsi) * 5)
        if rsi > 70: return max(-100, -(rsi - 70) * 5)
        return 0.0
    return fn


def make_sma_cross(fast: int, slow: int):
    """SMA(fast) vs SMA(slow): positive when fast > slow (golden cross zone)."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < slow: return None
        sf = sum(c[-fast:]) / fast
        ss = sum(c[-slow:]) / slow
        if ss == 0: return None
        return max(-100.0, min(100.0, ((sf - ss) / ss) * 100))
    return fn


def make_ema_cross(fast: int, slow: int):
    """EMA(fast) vs EMA(slow) — MACD-style trend score."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < slow: return None
        ef = _ema(c[-slow:], fast)
        es = _ema(c[-slow:], slow)
        if ef is None or es is None or es == 0: return None
        return max(-100.0, min(100.0, ((ef - es) / es) * 100))
    return fn


def make_bollinger_pos(period: int = 20, k: float = 2.0):
    """Position within ±k·σ band, sign-flipped (upper → bear, lower → bull)."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < period: return None
        w = c[-period:]
        m = sum(w) / period
        sd = (sum((x - m) ** 2 for x in w) / period) ** 0.5
        if sd <= 0: return None
        pos = (c[-1] - m) / (k * sd)
        return max(-100.0, min(100.0, -pos * 100))
    return fn


def make_donchian_break(period: int = 20):
    """Donchian breakout: close above/below prior N-bar high/low, proportional."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < period + 1: return None
        prior = c[-(period + 1):-1]
        hi = max(prior); lo = min(prior)
        if hi == lo: return 0.0
        last = c[-1]
        if last > hi: return min(100.0, (last - hi) / (hi - lo) * 100)
        if last < lo: return max(-100.0, (last - lo) / (hi - lo) * 100)
        return 0.0
    return fn


def make_lr_slope(period: int = 20):
    """N-bar linear regression slope (% per bar × period), clipped ±100."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < period: return None
        y = c[-period:]
        n = period
        xs = list(range(n))
        mx = (n - 1) / 2
        my = sum(y) / n
        num = sum((xs[i] - mx) * (y[i] - my) for i in range(n))
        den = sum((xs[i] - mx) ** 2 for i in range(n))
        if den == 0 or my == 0: return None
        slope = num / den
        pct_per_bar = slope / my * 100
        return max(-100.0, min(100.0, pct_per_bar * period))
    return fn


def make_arima(period: int = 30):
    """AR(1) on log-returns over the trailing N bars; score = next-bar log-return × 10,000."""
    def fn(bars):
        c = _closes(bars)
        if len(c) < period + 2: return None
        series = c[-(period + 1):]
        rets = []
        for i in range(1, len(series)):
            if series[i - 1] <= 0 or series[i] <= 0: return None
            rets.append(math.log(series[i] / series[i - 1]))
        if len(rets) < 5: return None
        x = rets[:-1]; y = rets[1:]
        n = len(x)
        mx = sum(x) / n; my = sum(y) / n
        num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        den = sum((x[i] - mx) ** 2 for i in range(n))
        if den == 0: return None
        phi = num / den
        c0 = my - phi * mx
        forecast = c0 + phi * rets[-1]
        return max(-100.0, min(100.0, forecast * 100 * 100))
    return fn


def make_combo_trend(member_fns):
    """Equal-weight ensemble of trend members. Returns None if < 2 members fired."""
    def fn(bars):
        parts = []
        for f in member_fns:
            try:
                v = f(bars)
                if v is not None: parts.append(v)
            except Exception:
                continue
        if len(parts) < 2: return None
        return sum(parts) / len(parts)
    return fn


# ─── Tournament roster ──────────────────────────────────────────────────────
# (model_name, family, score_fn) — model_name is the signal_name persisted
# to the DB; family groups variants together in the UI / leaderboard.
#
# IMPORTANT: existing model_name strings are PRESERVED so historical
# leaderboard data continues to count for those entrants. New variants
# are added alongside under the same family.

# Build family member fns first so combo_trend can reference them
_sma_5_20    = make_sma_cross(5, 20)
_sma_10_50   = make_sma_cross(10, 50)
_sma_20_100  = make_sma_cross(20, 100)
_ema_8_21    = make_ema_cross(8, 21)
_ema_12_26   = make_ema_cross(12, 26)
_ema_20_50   = make_ema_cross(20, 50)
_lr_10       = make_lr_slope(10)
_lr_20       = make_lr_slope(20)
_lr_40       = make_lr_slope(40)
_donch_10    = make_donchian_break(10)
_donch_20    = make_donchian_break(20)
_donch_55    = make_donchian_break(55)
_mr_10       = make_mean_reversion(10)
_mr_20       = make_mean_reversion(20)
_mr_40       = make_mean_reversion(40)
_rsi_7       = make_rsi_extreme(7)
_rsi_14      = make_rsi_extreme(14)
_rsi_21      = make_rsi_extreme(21)
_boll_20_2   = make_bollinger_pos(20, 2.0)
_boll_20_25  = make_bollinger_pos(20, 2.5)
_boll_50     = make_bollinger_pos(50, 2.0)

# ─── v24: quant-signal wrappers (signature: bars-only or (ticker, ctx)) ────
# Single-name patterns delegate to quant_signals; cross-sectional take
# (ticker, ctx) so wrap them as factory closures that ignore `bars`.
def _wrap_pattern(fn, **kw):
    def wrapped(bars):
        return fn(bars, **kw) if kw else fn(bars)
    return wrapped

def _wrap_universe(fn):
    """Wrap a (ticker, ctx) scorer to a (bars, ticker=None, ctx=None) shape
    consistent with the rest of MODELS. The main loop calls fn(bars,
    ticker=ticker, ctx=ctx) — universe scorers ignore bars but use the rest."""
    def wrapped(bars, ticker=None, ctx=None):
        return fn(ticker, ctx)
    return wrapped


MODELS = [
    # ─ momentum family (5 variants)
    ("momentum_3bar",      "momentum",        make_momentum(3)),
    ("momentum_5bar",      "momentum",        make_momentum(5)),
    ("momentum_8bar",      "momentum",        make_momentum(8)),
    ("momentum_13bar",     "momentum",        make_momentum(13)),
    ("momentum_21bar",     "momentum",        make_momentum(21)),
    # ─ mean reversion family (3)
    ("mean_rev_10",        "mean_reversion",  _mr_10),
    ("mean_rev_20",        "mean_reversion",  _mr_20),
    ("mean_rev_40",        "mean_reversion",  _mr_40),
    # ─ rsi extreme family (3)
    ("rsi_extreme_7",      "rsi_extreme",     _rsi_7),
    ("rsi_extreme_14",     "rsi_extreme",     _rsi_14),
    ("rsi_extreme_21",     "rsi_extreme",     _rsi_21),
    # ─ SMA cross family (3)
    ("sma_cross_5_20",     "sma_cross",       _sma_5_20),
    ("sma_cross_10_50",    "sma_cross",       _sma_10_50),
    ("sma_cross_20_100",   "sma_cross",       _sma_20_100),
    # ─ EMA cross family (3)
    ("ema_cross_8_21",     "ema_cross",       _ema_8_21),
    ("ema_cross_12_26",    "ema_cross",       _ema_12_26),
    ("ema_cross_20_50",    "ema_cross",       _ema_20_50),
    # ─ Bollinger position family (3)
    ("bollinger_pos_20",     "bollinger_pos", _boll_20_2),
    ("bollinger_pos_20_k25", "bollinger_pos", _boll_20_25),
    ("bollinger_pos_50",     "bollinger_pos", _boll_50),
    # ─ Donchian breakout family (3)
    ("donchian_break_10",  "donchian_break",  _donch_10),
    ("donchian_break_20",  "donchian_break",  _donch_20),
    ("donchian_break_55",  "donchian_break",  _donch_55),
    # ─ LR slope family (3)
    ("lr_slope_10",        "lr_slope",        _lr_10),
    ("lr_slope_20",        "lr_slope",        _lr_20),
    ("lr_slope_40",        "lr_slope",        _lr_40),
    # ─ ARIMA family (3)
    ("arima_20",           "arima",           make_arima(20)),
    ("arima_1",            "arima",           make_arima(30)),  # legacy name — period=30
    ("arima_50",           "arima",           make_arima(50)),
    # ─ Ensembles (2)
    ("combo_trend",        "ensemble",
        make_combo_trend([_sma_5_20, _sma_10_50, _ema_12_26, _ema_20_50,
                          _lr_20, _lr_40, _donch_20, _donch_55])),
    ("combo_meanrev",      "ensemble",
        make_combo_trend([_mr_10, _mr_20, _mr_40, _rsi_7, _rsi_14, _rsi_21,
                          _boll_20_2, _boll_50])),

    # ─── v24 — Tier 1: single-name pattern signals (5) ────────────────
    ("hurst_20",                "pattern",        _wrap_pattern(qs.hurst_signal, max_lag=20)),
    ("kalman_trend_20",         "pattern",        _wrap_pattern(qs.kalman_trend_slope, period=20)),
    ("ar2_forecast_30",         "arima",          _wrap_pattern(qs.ar2_forecast, period=30)),
    ("ou_halflife_30",          "pattern",        _wrap_pattern(qs.ou_halflife_signal, period=30)),
    ("bb_squeeze_breakout_20",  "bollinger_pos",  _wrap_pattern(qs.bb_squeeze_breakout, period=20)),

    # ─── v24 — Tier 2: cross-sectional signals (3) ────────────────────
    # These use the universe context (ret_5bar, sector ETFs, PCA residuals)
    # computed once per fire in main(). Wrappers ignore `bars` and read ctx.
    ("relative_strength_rank_5bar", "cross_sectional", _wrap_universe(qs.relative_strength_rank)),
    ("sector_relative_momentum",    "cross_sectional", _wrap_universe(qs.sector_relative_momentum)),
    ("pca_residual_mr",             "cross_sectional", _wrap_universe(qs.pca_residual_mr)),
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
    # leaderboard can compute IC stratified by regime later.
    regime = regime_tag.compute_regime()
    print(f"[models] regime: {regime['regime_label']}  (vix={regime['vix']}, spy_5d={regime['spy_5d_pct']}%)")
    print(f"[models] {len(MODELS)} entrants across families: "
          f"{sorted({m[1] for m in MODELS})}")

    # v24: compute universe context once per fire so cross-sectional entrants
    # (rank, sector-rel, pca-residual) can read the same precomputed features
    # for every ticker without re-doing the PCA SVD.
    sector_map = qs.load_sector_map_from_screener_csv()
    ctx = qs.build_universe_context(watchlist, sector_map=sector_map)
    print(f"[models] universe ctx: {len(ctx.get('ret_5bar') or {})} tickers, "
          f"{len(ctx.get('sector_etf_5bar_for_ticker') or {})} sector-matched, "
          f"{len(ctx.get('pca_residuals') or {})} PCA-residual scored")

    runs = {}
    n_signals = 0
    for model_name, family, fn in MODELS:
        run_id = sdb.record_run(
            run_type="model_score",
            config={"model":       model_name,
                    "family":      family,
                    "bbg_age":     bbg.get("generated_at"),
                    "n_watchlist": len(watchlist),
                    "regime":      regime},
        )
        if not run_id:
            print(f"[models] DB unavailable — skipping {model_name}")
            continue
        runs[model_name] = run_id

        for ticker, w in watchlist.items():
            if not w or w.get("error"):
                continue
            bars = w.get("bars") or []
            # Cross-sectional entrants don't need per-ticker bars (they read
            # from ctx) but they DO need to fire per ticker. Single-name
            # entrants need bars. Try calling with ctx; fall back to bars-only.
            try:
                try:
                    score = fn(bars, ticker=ticker, ctx=ctx)
                except TypeError:
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


if __name__ == "__main__":
    main()
