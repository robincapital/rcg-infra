"""
fundamentals_lookup.py — fetch a single ticker's quarterly fundamentals from
Sharadar SF1 in the exact shape that `price_targets.compute_target_price()`
expects.

Used by:
  · sentiment_refresh_server.py  — on-demand PT recompute when the user moves
                                    the per-ticker assumption sliders
  · report generator             — same recompute + the trailing-fundamentals
                                    sparklines on the 1-page PDF report

Encapsulating this in one module so the screener, server, and report all
agree on what "this ticker's fundamentals" means and don't drift apart.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

SHARADAR_SF1     = Path("/var/sharadar/data/SF1.parquet")
SHARADAR_TICKERS = Path("/var/sharadar/data/TICKERS.parquet")

# Match the screener's history window so PT regressions use the same input
# series. dynamic_factor_screener_v3.py: load_sf1 filters `datekey >= now-3y`.
HISTORY_YEARS = 3


# Sharadar parquet is ~200MB; cache the load so per-ticker lookups are fast.
@lru_cache(maxsize=1)
def _load_sf1() -> pl.DataFrame:
    if not SHARADAR_SF1.exists():
        raise FileNotFoundError(f"SF1 parquet missing: {SHARADAR_SF1}")
    return pl.read_parquet(SHARADAR_SF1)


@lru_cache(maxsize=1)
def _load_tickers_meta() -> dict:
    """Build a ticker → {sector, industry, name} dict from TICKERS.parquet.
    Sector matters because compute_target_price uses sector-specific multiples
    (Healthcare EV/EBITDA=14 vs _default=12, etc.). Without this lookup, the
    report endpoint would always pass sector=None → '_default' multiples,
    while the screener uses the correct sector — guaranteeing PT divergence
    on every name."""
    if not SHARADAR_TICKERS.exists():
        return {}
    df = pl.read_parquet(SHARADAR_TICKERS)
    out = {}
    has_sector   = "sector"   in df.columns
    has_industry = "industry" in df.columns
    has_name     = "name"     in df.columns
    for row in df.iter_rows(named=True):
        t = row.get("ticker")
        if not t: continue
        out[t.upper()] = {
            "sector":   row.get("sector")   if has_sector   else None,
            "industry": row.get("industry") if has_industry else None,
            "name":     row.get("name")     if has_name     else None,
        }
    return out


def _clean_floats(values) -> list:
    out = []
    for v in values:
        if v is None: continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        out.append(f)
    return out


def fetch_fundamentals(ticker: str) -> Optional[dict]:
    """
    Return the kwargs `price_targets.compute_target_price()` needs for this
    ticker, or None if the ticker isn't in SF1 or has insufficient history.

    Series are sorted oldest → newest by datekey. Lists, not pl.Series, so
    they're JSON-serializable for the API response.

    Filters to dimension='ARQ' (As-Reported Quarterly) to MATCH the daily
    screener (load_sf1 → dimension="ARQ"). Both paths use the same SF1
    slice, guaranteeing the report endpoint's PT matches the screener CSV's
    PT for any given ticker (subject to live-price differences in upside %).
    """
    sf1 = _load_sf1()
    flt = pl.col("ticker") == ticker.upper()
    if "dimension" in sf1.columns:
        flt = flt & (pl.col("dimension") == "ARQ")
    # Match screener's 3-year history window so Theil-Sen sees the same series
    cutoff = datetime.now() - timedelta(days=365 * HISTORY_YEARS)
    if "datekey" in sf1.columns:
        flt = flt & (pl.col("datekey") >= cutoff.date())
    tk = sf1.filter(flt).sort("datekey")
    if tk.height < 3:
        return None

    revenue = _clean_floats(tk["revenue"].to_list()) if "revenue" in tk.columns else []
    ebitda  = _clean_floats(tk["ebitda"].to_list())  if "ebitda"  in tk.columns else []
    debt    = _clean_floats(tk["debt"].to_list())    if "debt"    in tk.columns else []

    if "fcf" in tk.columns:
        fcf = _clean_floats(tk["fcf"].to_list())
    elif "ncfo" in tk.columns and "capex" in tk.columns:
        ncfo  = tk["ncfo"].to_numpy()
        capex = tk["capex"].to_numpy()
        fcf   = _clean_floats((ncfo - capex).tolist())
    else:
        fcf = []

    # EPS (diluted preferred — accounts for convertibles + dilutive instruments)
    eps = []
    if "epsdil" in tk.columns:
        eps = _clean_floats(tk["epsdil"].to_list())
    elif "eps" in tk.columns:
        eps = _clean_floats(tk["eps"].to_list())

    # Share count (latest non-null) — match the screener which passes
    # shares_diluted explicitly. Without it, the engine derives share_count
    # from marketcap/last_price, which diverges slightly when BBG live price
    # differs from the marketcap-implied price.
    shares_diluted = None
    for col in ("shareswadil", "shareswa", "sharesbas"):
        if col in tk.columns:
            vals = [v for v in tk[col].to_list() if v is not None]
            if vals and vals[-1] and vals[-1] > 0:
                try:
                    shares_diluted = float(vals[-1])
                    break
                except (TypeError, ValueError):
                    pass

    cash_on_hand = 0.0
    for col in ("cashnequsd", "cashneq", "cash"):
        if col in tk.columns:
            vals = tk[col].to_list()
            if vals and vals[-1] is not None:
                try:
                    last = float(vals[-1])
                    if not math.isnan(last):
                        cash_on_hand = last
                except (TypeError, ValueError):
                    pass
            break

    marketcap = None
    if "marketcap" in tk.columns:
        mc_vals = tk["marketcap"].to_list()
        if mc_vals and mc_vals[-1] is not None:
            try:
                marketcap = float(mc_vals[-1])
            except (TypeError, ValueError):
                pass

    # Sector + industry from TICKERS.parquet — required so compute_target_price
    # uses sector-specific multiples (matches what the screener passes).
    tmeta = _load_tickers_meta().get(ticker.upper(), {})

    return {
        "ticker":          ticker.upper(),
        "ebitda_series":   ebitda,
        "revenue_series":  revenue,
        "fcf_series":      fcf,
        "debt_series":     debt,
        "eps_series":      eps,
        "marketcap":       marketcap,
        "cash_on_hand":    cash_on_hand,
        "shares_diluted":  shares_diluted,
        "sector":          tmeta.get("sector"),
        "industry":        tmeta.get("industry"),
        "company_name":    tmeta.get("name"),
        "n_quarters":      tk.height,
        "latest_datekey":  str(tk["datekey"].to_list()[-1]) if tk.height else None,
    }


def invalidate_cache() -> None:
    """Force the SF1 parquet to be re-read on the next lookup. Call after the
    daily Sharadar pull so the server picks up fresh fundamentals."""
    _load_sf1.cache_clear()
