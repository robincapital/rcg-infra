"""
forward_returns_daily.py — compute daily-horizon forward returns (1d, 5d, 14d, 30d)

Joins model_*_score signals with future EOD closes from Sharadar SEP table to
compute realized returns over business-day horizons.

Horizons:
  1d, 5d, 14d, 30d (business days, skipping weekends/holidays)

Run cadence:
  Daily after Sharadar morning pull (8 AM ET)
  Backfills last 90 days on first run

Idempotent — predictions that already have realized_return_<horizon>d signals
are skipped.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402

import psycopg

# Horizons in business days
HORIZONS = [
    ("1d", 1),
    ("5d", 5),
    ("14d", 14),
    ("30d", 30),
]

# Backfill window (days of history to process on first run)
BACKFILL_DAYS = 90


def signal_name_for(horizon_label: str) -> str:
    return f"realized_return_{horizon_label}_pct"


def _load_eod_prices_from_sharadar(conn, lookback_days: int) -> dict:
    """
    Load EOD closes from Sharadar SEP table.
    Returns {(ticker, date): close_price}
    """
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    
    with conn.cursor() as cur:
        # Assuming Sharadar data is in a table named `sep` with columns:
        # ticker, date, close
        # Adjust table/column names based on actual schema
        try:
            cur.execute(
                """
                SELECT ticker, date, close
                FROM sep
                WHERE date >= %s
                  AND close IS NOT NULL
                  AND close > 0
                ORDER BY ticker, date
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
        except Exception as e:
            print(f"[daily-markouts] Could not load Sharadar SEP: {e}")
            print("[daily-markouts] Falling back to signals table 'eod_close' if available")
            # Fallback: try loading from signals table if SEP not available
            cur.execute(
                """
                SELECT s.ticker, DATE(r.run_timestamp), s.signal_value
                FROM signals s
                JOIN runs r ON s.run_id = r.run_id
                WHERE s.signal_name = 'eod_close'
                  AND DATE(r.run_timestamp) >= %s
                  AND s.signal_value IS NOT NULL
                  AND s.signal_value > 0
                ORDER BY s.ticker, DATE(r.run_timestamp)
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
    
    out = {}
    for ticker, date, close in rows:
        # Ensure date is a date object
        if isinstance(date, datetime):
            date = date.date()
        out[(ticker, date)] = float(close)
    
    return out


def _next_business_day(start_date, n_days: int, price_map: dict, ticker: str):
    """
    Find the EOD close N business days after start_date for the given ticker.
    Uses actual available dates in price_map (accounts for holidays/weekends).
    Returns (date, close) or (None, None) if not found.
    """
    current = start_date
    days_counted = 0
    max_attempts = n_days * 2 + 10  # Allow for holidays
    
    for _ in range(max_attempts):
        current += timedelta(days=1)
        if (ticker, current) in price_map:
            days_counted += 1
            if days_counted >= n_days:
                return current, price_map[(ticker, current)]
    
    return None, None


def main() -> None:
    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        # Load EOD price history
        print("[daily-markouts] Loading EOD prices...")
        price_map = _load_eod_prices_from_sharadar(conn, BACKFILL_DAYS + 50)
        print(f"[daily-markouts] Loaded {len(price_map)} (ticker, date) price points")
        
        if not price_map:
            print("[daily-markouts] No EOD prices available — exiting")
            return
        
        # Pull all model_*_score signals from last BACKFILL_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
        
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.run_id, s.ticker, DATE(r.run_timestamp) as asof_date,
                       s.signal_name, s.signal_value
                FROM signals s
                JOIN runs r ON s.run_id = r.run_id
                WHERE r.run_type = 'model_score'
                  AND s.signal_name LIKE 'model_%_score'
                  AND r.run_timestamp >= %s
                ORDER BY s.ticker, r.run_timestamp
                """,
                (cutoff,),
            )
            signal_rows = cur.fetchall()
        
        print(f"[daily-markouts] Processing {len(signal_rows)} model score signals")
        
        # Pull already-computed forward returns (skip set)
        already = set()
        for h_label, _ in HORIZONS:
            sn = signal_name_for(h_label)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.run_id, s.ticker, %s as signal_name
                    FROM signals s
                    JOIN runs r ON s.run_id = r.run_id
                    WHERE r.run_type = 'model_score'
                      AND s.signal_name = %s
                      AND r.run_timestamp >= %s
                    """,
                    (sn, sn, cutoff),
                )
                for run_id, ticker, _ in cur.fetchall():
                    already.add((run_id, ticker, sn))
        
        # Compute forward returns
        inserts = []  # (run_id, ticker, signal_name, value)
        n_skipped_already = 0
        n_skipped_no_price = 0
        
        for run_id, ticker, asof_date, model_signal_name, score_value in signal_rows:
            if isinstance(asof_date, datetime):
                asof_date = asof_date.date()
            
            # Get entry price (EOD close on asof_date)
            entry_price = price_map.get((ticker, asof_date))
            if entry_price is None or entry_price <= 0:
                n_skipped_no_price += 1
                continue
            
            for h_label, n_days in HORIZONS:
                sn = signal_name_for(h_label)
                
                if (run_id, ticker, sn) in already:
                    n_skipped_already += 1
                    continue
                
                # Find exit price N business days later
                exit_date, exit_price = _next_business_day(asof_date, n_days, price_map, ticker)
                
                if exit_price is None:
                    # Future date not available yet or ticker delisted
                    continue
                
                ret_pct = (exit_price - entry_price) / entry_price * 100
                inserts.append((run_id, ticker, sn, ret_pct))
        
        # Write back
        if not inserts:
            print(f"[daily-markouts] Nothing to insert "
                  f"(already={n_skipped_already}, no-price={n_skipped_no_price})")
            return
        
        n_written = 0
        for run_id, ticker, sn, val in inserts:
            sdb.record_signal(run_id, ticker, sn, value=float(val))
            n_written += 1
        
        print(f"[daily-markouts] Wrote {n_written} realized-return signals "
              f"(already={n_skipped_already}, no-price={n_skipped_no_price})")


if __name__ == "__main__":
    main()
