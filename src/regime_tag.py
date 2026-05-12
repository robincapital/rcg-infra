"""
regime_tag.py — compute the current market regime label for tournament fires.

Two axes today (extend later if needed):
  · vol_regime   — VIX bucket: low (<15) / mid (15-25) / high (>25)
  · trend_regime — SPY 5d return: bear (<-1%) / flat (-1..+1%) / bull (>+1%)

Combined label is "<vol>/<trend>" e.g. "low/bull", "high/bear", giving a
9-cell regime grid. Every models_capture and predictions_capture fire writes
this tag into runs.config_json so the leaderboard can compute IC stratified
by regime later.

Source of truth:
  · VIX latest close → from bloomberg_prices.json (live, intraday)
  · SPY 5d return    → from Sharadar SFP daily closes, comparing close from
                       5 trading days ago to today's SPY close (BBG live)

Fallback chain handles missing data gracefully — when we can't compute a
piece, we emit None for that axis and "unknown/<other>" for the label.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BBG_PATH = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")
SFP_PATH = Path("/var/sharadar/data/SFP.parquet")


def _bbg_price(ticker: str) -> Optional[float]:
    if not BBG_PATH.exists():
        return None
    try:
        d = json.loads(BBG_PATH.read_text())
        rec = (d.get("watchlist") or {}).get(ticker, {})
        p = rec.get("price")
        return float(p) if p else None
    except Exception:
        return None


def _spy_close_n_days_ago(n: int = 5) -> Optional[float]:
    """Read SPY close from N trading days ago via Sharadar SFP."""
    try:
        import polars as pl
        df = pl.read_parquet(SFP_PATH)
        spy = df.filter(pl.col("ticker") == "SPY").sort("date")
        if spy.height < n + 1:
            return None
        # SFP rows are daily, sorted ascending. The "today" close hasn't necessarily
        # been written yet at time of run, so SPY[-1] could be today OR yesterday.
        # Take the row at index -(n+1) when looking back n trading days from the
        # latest available row. Trading-day arithmetic is approximate (we don't
        # adjust for the gap between SFP's last close and today), which is fine
        # for a regime tag — VIX dominates and a 1-day skew doesn't change buckets.
        return float(spy["close"].to_list()[-(n + 1)])
    except Exception:
        return None


def vol_bucket(vix: Optional[float]) -> Optional[str]:
    if vix is None: return None
    if vix < 15:    return "low"
    if vix <= 25:   return "mid"
    return "high"


def trend_bucket(spy_5d_pct: Optional[float]) -> Optional[str]:
    if spy_5d_pct is None: return None
    if spy_5d_pct < -1.0:  return "bear"
    if spy_5d_pct > 1.0:   return "bull"
    return "flat"


def compute_regime() -> dict:
    """
    Return a regime descriptor for the current moment. Always returns the same
    schema — None values where we couldn't compute a piece, plus a string
    label suitable for grouping in SQL.
    """
    vix_now      = _bbg_price("VIX")
    spy_now      = _bbg_price("SPY")
    spy_5d_ago   = _spy_close_n_days_ago(5)

    spy_5d_pct = None
    if spy_now is not None and spy_5d_ago and spy_5d_ago > 0:
        spy_5d_pct = (spy_now / spy_5d_ago - 1.0) * 100.0

    vol   = vol_bucket(vix_now)
    trend = trend_bucket(spy_5d_pct)
    label = f"{vol or 'unknown'}/{trend or 'unknown'}"

    return {
        "vix":           round(vix_now, 2) if vix_now else None,
        "spy_5d_pct":    round(spy_5d_pct, 2) if spy_5d_pct is not None else None,
        "vol_regime":    vol,
        "trend_regime":  trend,
        "regime_label":  label,
        "tagged_at":     datetime.now(timezone.utc).isoformat(),
    }


# Stable enumeration so the dashboard can render a regime grid in canonical order
VOL_BUCKETS   = ["low", "mid", "high"]
TREND_BUCKETS = ["bear", "flat", "bull"]
ALL_REGIME_LABELS = [f"{v}/{t}" for v in VOL_BUCKETS for t in TREND_BUCKETS]


if __name__ == "__main__":
    import json as _json
    r = compute_regime()
    print(_json.dumps(r, indent=2))
