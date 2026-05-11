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
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

SHARADAR_SF1 = Path("/var/sharadar/data/SF1.parquet")


# Sharadar parquet is ~200MB; cache the load so per-ticker lookups are fast.
@lru_cache(maxsize=1)
def _load_sf1() -> pl.DataFrame:
    if not SHARADAR_SF1.exists():
        raise FileNotFoundError(f"SF1 parquet missing: {SHARADAR_SF1}")
    return pl.read_parquet(SHARADAR_SF1)


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

    Filters to dimension='MRQ' (Most-Recent Quarterly, restated) so each row
    is a clean point-in-time quarter — not mixed with trailing-12 or annual
    aggregates. This is what we want for trend regression on the trailing
    fundamentals.
    """
    sf1 = _load_sf1()
    flt = pl.col("ticker") == ticker.upper()
    if "dimension" in sf1.columns:
        flt = flt & (pl.col("dimension") == "MRQ")
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

    sector = None
    industry = None

    return {
        "ticker":          ticker.upper(),
        "ebitda_series":   ebitda,
        "revenue_series":  revenue,
        "fcf_series":      fcf,
        "debt_series":     debt,
        "marketcap":       marketcap,
        "cash_on_hand":    cash_on_hand,
        "sector":          sector,
        "industry":        industry,
        "n_quarters":      tk.height,
        "latest_datekey":  str(tk["datekey"].to_list()[-1]) if tk.height else None,
    }


def invalidate_cache() -> None:
    """Force the SF1 parquet to be re-read on the next lookup. Call after the
    daily Sharadar pull so the server picks up fresh fundamentals."""
    _load_sf1.cache_clear()
