"""
quant_signals.py — additional quantitative signal computations for the
tournament (v24). Two groups:

  ▸ Single-name patterns         (function(bars) → float | None)
      hurst_signal       — H exponent × sign(recent ret); trending vs MR
      kalman_trend_slope — local-linear-trend Kalman velocity
      ar2_forecast       — AR(2) one-step-ahead forecast
      ou_halflife_signal — Ornstein-Uhlenbeck mean-rev (deviation × strength)
      bb_squeeze_breakout — BB-width compression + directional break

  ▸ Cross-sectional (universe context required)
                                  (function(ticker, ctx) → float | None)
      relative_strength_rank      — percentile rank of 5-bar return
      sector_relative_momentum    — 5-bar return minus sector ETF's
      pca_residual_mr             — residual after stripping PC1 ("market")

Plus the once-per-fire helper:
      build_universe_context(watchlist, sector_map) → dict

The PCA piece is the cleanest "let the data tell us what's idiosyncratic"
move — first principal component of bar-to-bar log returns typically
captures the broad market move; what's left is the alpha to trade.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

# ════════════════════════════════════════════════════════════════════
# PCA UNIVERSE — top-N high-conviction names, frozen weekly
# ════════════════════════════════════════════════════════════════════
# Why a curated universe instead of the full 118-name watchlist:
#   PCA on a heterogeneous mix of cross-asset ETFs + small caps + macro
#   proxies produces a noisy PC1 that mostly captures generic market beta
#   with low statistical confidence. A focused universe of high-conviction
#   trending names produces a meaningful PC1 (the dominant common factor
#   among names we'd actually trade) and a cleaner idiosyncratic residual.
#
# Selection: top 20 by composite_score, filtered to names with engine
#   upside > +20% (high-conviction longs only). Read from the daily
#   screener_universe.csv.
#
# Cadence: weekly hold. Universe frozen Monday → following Monday so
#   residuals are comparable within the week. When the universe rotates,
#   the meta-model sees a discontinuity — acceptable since training is
#   weekly-walk-forward.
#
# Drift handling: naive recompute on each rebalance. No transition window.
PCA_UNIVERSE_PATH       = Path("/home/nixos/Prod/V1/src/pca_universe.json")
PCA_TOP_N               = 20
PCA_MIN_UPSIDE_PCT      = 20.0    # engine upside floor for membership
SCREENER_CSV_PATH       = "/home/nixos/Prod/V1/outputs/screener_universe.csv"


def _next_monday_utc(now: datetime) -> datetime:
    """Return next Monday 00:00 UTC strictly after `now`."""
    # weekday(): Mon=0, Sun=6
    days_ahead = (7 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return target


def load_pca_universe(force_rebuild: bool = False) -> dict:
    """
    Load the frozen high-conviction universe used for the PCA residual signal.
    Auto-rebuilds when:
      · file missing
      · next_rebalance datetime has passed (Monday rollover)
      · force_rebuild=True

    Returns the full universe descriptor (frozen_at, next_rebalance, criteria,
    universe list). Never raises — falls back to empty universe + logs reason.
    """
    now = datetime.now(timezone.utc)
    existing = None
    if PCA_UNIVERSE_PATH.exists():
        try:
            existing = json.loads(PCA_UNIVERSE_PATH.read_text())
        except Exception:
            existing = None

    needs_rebuild = force_rebuild or existing is None
    if existing and not needs_rebuild:
        try:
            next_rb = datetime.fromisoformat(existing["next_rebalance"])
            if next_rb.tzinfo is None:
                next_rb = next_rb.replace(tzinfo=timezone.utc)
            if now >= next_rb:
                needs_rebuild = True
        except Exception:
            needs_rebuild = True

    if not needs_rebuild:
        return existing

    # Build from screener CSV — top-N by composite, filtered by upside floor
    candidates = []
    rebuild_error = None
    try:
        with open(SCREENER_CSV_PATH) as fh:
            for row in csv.DictReader(fh):
                t = (row.get("ticker") or "").upper()
                if not t:
                    continue
                try:
                    composite = float(row.get("composite_score") or 0)
                    # CSV stores upside as a fraction (0.83 = 83%)
                    upside_pct = float(row.get("upside_pct") or 0) * 100
                except (TypeError, ValueError):
                    continue
                if upside_pct < PCA_MIN_UPSIDE_PCT:
                    continue
                candidates.append((t, composite, upside_pct))
    except Exception as e:
        rebuild_error = str(e)[:200]

    candidates.sort(key=lambda x: -x[1])     # composite descending
    universe = [c[0] for c in candidates[:PCA_TOP_N]]

    payload = {
        "frozen_at":       now.isoformat(),
        "next_rebalance":  _next_monday_utc(now).isoformat(),
        "selection":       {
            "method":           "top_N_by_composite",
            "top_n":            PCA_TOP_N,
            "min_upside_pct":   PCA_MIN_UPSIDE_PCT,
        },
        "universe":        universe,
        "n_candidates":    len(candidates),
        "rebuild_error":   rebuild_error,
        "previous":        (existing.get("universe") if existing else None),
    }
    try:
        PCA_UNIVERSE_PATH.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass
    return payload


# ════════════════════════════════════════════════════════════════════
# SINGLE-NAME PATTERNS
# ════════════════════════════════════════════════════════════════════

def _closes(bars):
    return [b.get("close") for b in bars if b.get("close")]


def hurst_signal(bars, max_lag: int = 20):
    """
    Rescaled-range (R/S) Hurst exponent on closing prices.

    H > 0.5  → series is trending; trade in the direction of recent return.
    H < 0.5  → series is mean-reverting; trade against the recent return.
    H ~ 0.5  → random walk; no signal.

    Score = (H - 0.5) × 200 × sign(recent 5-bar return), clipped to ±100.
    """
    closes = _closes(bars)
    if len(closes) < max_lag * 2 + 1:
        return None
    series = np.asarray(closes, dtype=float)
    if (series <= 0).any():
        return None
    lags = list(range(2, max_lag))
    tau = []
    for lag in lags:
        diffs = series[lag:] - series[:-lag]
        s = float(np.std(diffs))
        if s <= 0:
            return None
        tau.append(s)
    log_lags = np.log(lags)
    log_tau = np.log(tau)
    slope, _ = np.polyfit(log_lags, log_tau, 1)
    H = float(slope)
    # Direction comes from sign of recent 5-bar return
    if len(series) < 6 or series[-6] <= 0:
        return None
    recent_ret = (series[-1] - series[-6]) / series[-6]
    direction = 1.0 if recent_ret > 0 else -1.0 if recent_ret < 0 else 0.0
    if direction == 0.0:
        return 0.0
    raw = (H - 0.5) * 200 * direction
    return float(np.clip(raw, -100, 100))


def kalman_trend_slope(bars, period: int = 20):
    """
    Local-linear-trend Kalman filter with state = [level, velocity].
    Returns the final velocity estimate as % of latest price per bar × 10,
    clipped to ±100.

    Robust to single-bar noise vs naive LR slope — Kalman discounts outliers
    based on the running covariance estimate.
    """
    closes = _closes(bars)
    if len(closes) < period + 1:
        return None
    series = np.asarray(closes[-period:], dtype=float)

    # State: [level, velocity]; transition adds velocity to level each step.
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.diag([0.01, 0.001])               # process noise — small drift
    R_obs = max(0.01, float(np.var(series)) * 0.1)

    x = np.array([series[0], 0.0])
    P = np.eye(2) * 1.0

    for z in series[1:]:
        # Predict
        x = F @ x
        P = F @ P @ F.T + Q
        # Update
        innov = z - (H @ x)[0]
        S = (H @ P @ H.T + R_obs)[0, 0]
        K = (P @ H.T).flatten() / S
        x = x + K * innov
        P = (np.eye(2) - np.outer(K, H[0])) @ P

    if series[-1] <= 0:
        return None
    velocity_pct_per_bar = (x[1] / series[-1]) * 100.0
    return float(np.clip(velocity_pct_per_bar * 10, -100, 100))


def ar2_forecast(bars, period: int = 30):
    """
    AR(2) one-step-ahead forecast on log-returns:
        r_t = c + φ₁·r_{t-1} + φ₂·r_{t-2} + ε

    Extends the existing arima_1 (AR(1)) entrant. Score is forecast log-ret
    × 10,000 (≈ bps × 100), clipped to ±100.
    """
    closes = _closes(bars)
    if len(closes) < period + 3:
        return None
    series = closes[-(period + 1):]
    rets = []
    for i in range(1, len(series)):
        if series[i - 1] <= 0 or series[i] <= 0:
            return None
        rets.append(math.log(series[i] / series[i - 1]))
    if len(rets) < 10:
        return None
    y = np.asarray(rets[2:], dtype=float)
    X = np.column_stack([
        np.ones(len(y)),
        np.asarray(rets[1:-1], dtype=float),
        np.asarray(rets[:-2], dtype=float),
    ])
    try:
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    c, phi1, phi2 = float(coef[0]), float(coef[1]), float(coef[2])
    forecast = c + phi1 * rets[-1] + phi2 * rets[-2]
    return float(np.clip(forecast * 100 * 100, -100, 100))


def ou_halflife_signal(bars, period: int = 30):
    """
    Ornstein-Uhlenbeck mean reversion strength signal.

    Fits  Δx_t = a + b·x_{t-1} + ε  on the trailing window. Half-life of
    reversion = -ln(2) / b (only defined when b < 0, i.e. the series
    actually mean-reverts).

    Score = -(deviation_z from MA) × (1/√half_life) × 30, clipped ±100.
    Stretched UP (deviation > 0) on a short-half-life name → bear signal.
    Names without a meaningful reversion (b ≥ 0 or half-life > period × 3)
    return 0.
    """
    closes = _closes(bars)
    if len(closes) < period:
        return None
    x = np.asarray(closes[-period:], dtype=float)
    if (x <= 0).any():
        return None
    dx = np.diff(x)
    x_lag = x[:-1]
    X = np.column_stack([np.ones(len(x_lag)), x_lag])
    try:
        coef, *_ = np.linalg.lstsq(X, dx, rcond=None)
    except np.linalg.LinAlgError:
        return None
    b = float(coef[1])
    if b >= 0:
        return 0.0                                          # not MR — no signal
    half_life = -math.log(2) / b
    if half_life > period * 3:
        return 0.0                                          # too slow
    ma = float(np.mean(x))
    sd = float(np.std(x))
    if sd <= 0:
        return 0.0
    deviation_z = (x[-1] - ma) / sd
    strength = 1.0 / math.sqrt(max(half_life, 1.0))
    return float(np.clip(-deviation_z * strength * 30, -100, 100))


def bb_squeeze_breakout(bars, period: int = 20, k: float = 2.0):
    """
    Bollinger band squeeze detector. When current BB width is compressed
    well below the trailing median width, a breakout is likely. The signed
    score = compression_intensity × sign(recent 5-bar return).

    No squeeze (current ≥ 85% of trailing median) → 0.
    Otherwise the signal magnitude grows as compression deepens, capped ±100.
    """
    closes = _closes(bars)
    if len(closes) < period * 2:
        return None
    series = np.asarray(closes, dtype=float)
    widths = []
    for i in range(period, len(series) + 1):
        window = series[i - period:i]
        m = float(window.mean())
        s = float(window.std())
        if m == 0:
            continue
        widths.append((k * 2 * s) / m)
    if len(widths) < period:
        return None
    current = widths[-1]
    median = float(np.median(widths[-period:]))
    if median == 0:
        return 0.0
    compression_ratio = current / median
    if compression_ratio >= 0.85:
        return 0.0                                          # no meaningful squeeze
    if series[-6] <= 0:
        return None
    recent_ret = (series[-1] - series[-6]) / series[-6]
    if recent_ret == 0:
        return 0.0
    direction = 1.0 if recent_ret > 0 else -1.0
    raw = (1.0 - compression_ratio) * 200 * direction
    return float(np.clip(raw, -100, 100))


# ════════════════════════════════════════════════════════════════════
# CROSS-SECTIONAL (universe context required)
# ════════════════════════════════════════════════════════════════════
# Sector → SPDR sector ETF mapping. If a ticker's sector doesn't have a
# corresponding ETF in the watchlist, the sector-rel entrant returns None.
SECTOR_TO_ETF = {
    "Technology":             "XLK",
    "Energy":                 "XLE",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Financial Services":     "XLF",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Materials":              "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Consumer Cyclical":      "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive":     "XLP",
    "Consumer Staples":       "XLP",
    "Communication Services": "XLC",
}


def relative_strength_rank(ticker, ctx):
    """Percentile rank of ticker's 5-bar return vs the universe.
    +100 = top of pack, -100 = bottom, 0 = median."""
    rets = (ctx or {}).get("ret_5bar") or {}
    this = rets.get(ticker)
    if this is None:
        return None
    vals = sorted(rets.values())
    n = len(vals)
    if n < 5:
        return None
    # rank position (lower index = lower value)
    try:
        idx = vals.index(this)
    except ValueError:
        return None
    percentile = idx / (n - 1)
    return float(np.clip((percentile - 0.5) * 200, -100, 100))


def sector_relative_momentum(ticker, ctx):
    """Ticker's 5-bar return minus its sector ETF's 5-bar return.
    Strips out market/sector beta. Score = excess × 50, clipped ±100."""
    if not ctx:
        return None
    sector_etf_5bar = ctx.get("sector_etf_5bar_for_ticker") or {}
    ret_5bar = ctx.get("ret_5bar") or {}
    sector_ret = sector_etf_5bar.get(ticker)
    ticker_ret = ret_5bar.get(ticker)
    if sector_ret is None or ticker_ret is None:
        return None
    excess = ticker_ret - sector_ret
    return float(np.clip(excess * 50, -100, 100))


def pca_residual_mr(ticker, ctx):
    """
    Mean-revert the residual after stripping PC1 from the universe returns
    matrix. Positive residual (stretched above the market) → bear; negative
    (lagging the market) → bull. Score = -z_residual × 30, clipped ±100.
    """
    if not ctx:
        return None
    z = (ctx.get("pca_residuals") or {}).get(ticker)
    if z is None:
        return None
    return float(np.clip(-z * 30, -100, 100))


# ════════════════════════════════════════════════════════════════════
# UNIVERSE CONTEXT (compute once per fire, share across cross-sectional
# entrants — avoids re-running PCA per ticker)
# ════════════════════════════════════════════════════════════════════
def build_universe_context(watchlist: dict, sector_map: Optional[dict] = None) -> dict:
    """
    Pre-compute the cross-sectional features needed by Tier 2 entrants.
    Cheap (~50 ms on 120-ticker watchlist) so safe to call every fire.

    Returns dict with:
      ret_5bar:                    {ticker: 5-bar return %}
      sector_etf_5bar_for_ticker:  {ticker: ticker's sector ETF's 5-bar return %}
      pca_residuals:               {ticker: z-scored residual after PC1 removed}
    """
    sector_map = sector_map or {}

    ret_5bar = {}
    closes_by_ticker = {}
    for ticker, w in (watchlist or {}).items():
        if not w or w.get("error"):
            continue
        bars = w.get("bars") or []
        closes = [b.get("close") for b in bars if b.get("close")]
        if len(closes) < 6 or closes[-6] <= 0:
            continue
        ret_5bar[ticker] = (closes[-1] - closes[-6]) / closes[-6] * 100
        closes_by_ticker[ticker] = closes

    # Map each ticker → its sector ETF's 5-bar return
    sector_etf_5bar_for_ticker = {}
    for ticker in ret_5bar:
        srec = sector_map.get(ticker)
        sector = srec.get("sector") if isinstance(srec, dict) else srec
        etf = SECTOR_TO_ETF.get(sector)
        if etf and etf in ret_5bar:
            sector_etf_5bar_for_ticker[ticker] = ret_5bar[etf]

    # PCA only on the FROZEN HIGH-CONVICTION UNIVERSE (top-20 by composite +
    # upside floor, rebalanced weekly). Running PCA on the full 118-name
    # watchlist produced too-noisy a PC1 — the curated universe yields a
    # statistically meaningful dominant factor among names we'd actually
    # trade. Tickers outside the universe get None (entrant doesn't fire
    # for them).
    pca_universe_info = load_pca_universe()
    pca_universe_set = set(pca_universe_info.get("universe") or [])

    pca_residuals: dict = {}
    pca_n_bars_used = None
    try:
        # Sliding window: 10 → 7 → 5 → 4 bars, take widest where ≥5 of the
        # universe qualify. (Min lowered from 10 since universe is smaller.)
        n_bars_to_use = 4
        for candidate in (10, 7, 5, 4):
            qual = sum(1 for t in pca_universe_set
                       if t in closes_by_ticker
                       and len(closes_by_ticker[t]) >= candidate + 1
                       and all(x > 0 for x in closes_by_ticker[t][-(candidate + 1):]))
            if qual >= 5:
                n_bars_to_use = candidate
                break

        rows, tickers = [], []
        for t in pca_universe_set:
            closes = closes_by_ticker.get(t)
            if not closes or len(closes) < n_bars_to_use + 1:
                continue
            tail = closes[-(n_bars_to_use + 1):]
            if any(c <= 0 for c in tail):
                continue
            log_rets = [math.log(tail[i] / tail[i - 1])
                        for i in range(1, len(tail))]
            rows.append(log_rets)
            tickers.append(t)

        if len(rows) >= 5:
            R = np.asarray(rows, dtype=float)
            R_centered = R - R.mean(axis=0)
            U, S, Vt = np.linalg.svd(R_centered, full_matrices=False)
            pc1_dir = Vt[0]
            raw_residuals = {}
            for i, t in enumerate(tickers):
                pc1_loading = float(U[i, 0] * S[0])
                total = float(np.sum(R[i]))
                pc1_explained = float(pc1_loading * np.sum(pc1_dir))
                raw_residuals[t] = total - pc1_explained
            vals = list(raw_residuals.values())
            m = float(np.mean(vals))
            sd = float(np.std(vals))
            if sd > 0:
                pca_residuals = {t: (v - m) / sd for t, v in raw_residuals.items()}
            pca_n_bars_used = n_bars_to_use
    except Exception:
        pass

    return {
        "ret_5bar":                    ret_5bar,
        "sector_etf_5bar_for_ticker":  sector_etf_5bar_for_ticker,
        "pca_residuals":               pca_residuals,
        "pca_universe":                list(pca_universe_set),
        "pca_universe_frozen_at":      pca_universe_info.get("frozen_at"),
        "pca_universe_next_rebalance": pca_universe_info.get("next_rebalance"),
        "pca_n_bars_used":             pca_n_bars_used,
    }


def load_sector_map_from_screener_csv(csv_path: str = "/home/nixos/Prod/V1/outputs/screener_universe.csv") -> dict:
    """Read ticker → {sector, industry} mapping from the daily screener CSV.
    Returns {} on failure — sector-rel entrant will then return None per ticker."""
    import csv
    out: dict = {}
    try:
        with open(csv_path) as fh:
            for row in csv.DictReader(fh):
                t = (row.get("ticker") or "").upper()
                s = row.get("sector")
                if t and s:
                    out[t] = {"sector": s, "industry": row.get("industry") or ""}
    except Exception:
        pass
    return out
