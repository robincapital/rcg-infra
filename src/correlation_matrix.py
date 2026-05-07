"""
correlation_matrix.py — daily rolling correlations across asset classes

Reads Sharadar SEP daily closes for a curated cross-asset basket, computes log
returns, and emits rolling Pearson correlations at 30d / 90d / 252d windows.

Output:
  /home/nixos/Prod/V1/outputs/correlations.json

Schema:
  {
    "generated_at": "2026-05-07T...",
    "tickers": [...],
    "labels": {ticker: "human-readable description"},
    "asof_date": "2026-05-06",
    "windows": {
      "30d":  { "matrix": [[...]], "n_obs": 30 },
      "90d":  { "matrix": [[...]], "n_obs": 90 },
      "1y":   { "matrix": [[...]], "n_obs": 252 }
    }
  }

The dashboard renders this as a heatmap with toggle for window length;
"delta" mode = 30d - 1y to surface regime-change correlations.

Run cadence: daily after Sharadar pull (~04:30 ET) via systemd timer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import polars as pl
import numpy as np

# ─── Cross-asset basket ────────────────────────────────────────────────────
BASKET = [
    # Equity
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("IWM", "Russell 2000"),
    ("EFA", "Developed intl"),
    ("EEM", "Emerging mkts"),
    # Sectors
    ("XLK", "Tech sector"),
    ("XLE", "Energy sector"),
    ("XLF", "Financials"),
    ("XLV", "Healthcare"),
    # Treasuries
    ("TLT", "20y+ treasuries"),
    ("IEF", "10y treasuries"),
    # Credit
    ("HYG", "High-yield credit"),
    ("LQD", "IG credit"),
    # Commodities
    ("GLD", "Gold"),
    ("SLV", "Silver"),
    ("USO", "Oil"),
    ("DBC", "Broad commodities"),
    # Currency
    ("UUP", "USD index"),
    # Volatility
    ("VXX", "VIX futures (short-term)"),
]

# Sharadar SEP = stocks; SFP = ETFs/funds. Our cross-asset basket is ETFs,
# so SFP is the right source.
SFP_PATH = Path("/var/sharadar/data/SFP.parquet")
OUTPUT_PATH = Path("/home/nixos/Prod/V1/outputs/correlations.json")

WINDOWS = {
    "30d":  30,
    "90d":  90,
    "1y":   252,
}


def main() -> None:
    if not SFP_PATH.exists():
        print(f"[correlations] no SEP at {SFP_PATH} — exiting")
        return

    tickers = [t for t, _ in BASKET]
    labels  = {t: lbl for t, lbl in BASKET}

    # Sharadar SEP columns (from earlier inspection): ticker, date, close,
    # closeadj, etc. Date column varies — try both.
    sep = pl.read_parquet(SFP_PATH, columns=None)
    print(f"[correlations] loaded SFP: {sep.height} rows, {len(sep.columns)} cols")

    date_col  = next((c for c in ("date", "datekey", "calendardate") if c in sep.columns), None)
    close_col = next((c for c in ("closeadj", "close", "closeunadj") if c in sep.columns), None)
    if not date_col or not close_col:
        print(f"[correlations] missing date/close column (have {sep.columns[:10]}...)")
        return

    sep = (sep
           .filter(pl.col("ticker").is_in(tickers))
           .select([pl.col("ticker"),
                    pl.col(date_col).alias("date"),
                    pl.col(close_col).cast(pl.Float64).alias("close")])
           .filter(pl.col("close") > 0)
           .sort(["ticker", "date"]))

    # Pivot to wide: index=date, cols=tickers, values=close
    wide = sep.pivot(values="close", index="date", on="ticker").sort("date")
    available = [c for c in wide.columns if c in tickers]
    if len(available) < 2:
        print(f"[correlations] only {len(available)} tickers in SEP — exiting")
        return

    # Most recent N rows for the largest window
    max_window = max(WINDOWS.values())
    wide_recent = wide.tail(max_window + 5)
    asof = wide_recent["date"].to_list()[-1]

    # Compute log returns then drop nulls
    closes_np = wide_recent.select(available).to_numpy()
    valid_mask = np.isfinite(closes_np).all(axis=1)
    closes_np = closes_np[valid_mask]
    if closes_np.shape[0] < 31:
        print(f"[correlations] too few aligned obs ({closes_np.shape[0]}) — exiting")
        return

    log_returns = np.diff(np.log(closes_np), axis=0)
    out_windows = {}
    for win_label, win_n in WINDOWS.items():
        if log_returns.shape[0] < win_n:
            print(f"[correlations] {win_label} skipped ({log_returns.shape[0]} obs < {win_n})")
            continue
        recent = log_returns[-win_n:]
        # Pearson corr — symmetric matrix, diag = 1
        corr = np.corrcoef(recent, rowvar=False)
        # Round for JSON compactness
        corr_rounded = np.round(corr, 3).tolist()
        out_windows[win_label] = {
            "matrix": corr_rounded,
            "n_obs":  int(win_n),
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date":    str(asof),
        "tickers":      available,
        "labels":       labels,
        "windows":      out_windows,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, default=str))
    print(f"[correlations] wrote {OUTPUT_PATH} "
          f"({OUTPUT_PATH.stat().st_size} bytes) "
          f"· {len(available)} tickers · {list(out_windows.keys())}")


if __name__ == "__main__":
    main()
