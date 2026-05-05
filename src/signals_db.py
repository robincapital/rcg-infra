"""
signals_db.py  —  RCG Signal Capture API
=========================================
Single-purpose module: write every signal the system emits to Postgres so
we can later compute IC, hit rate, decay curves, regime-conditional Sharpe,
and walk-forward weight calibrations.

DESIGN PRINCIPLES
-----------------
- Best-effort capture: if the DB is unreachable, log and continue. The
  screener MUST NOT fail because capture failed.
- Single connection per process, lazily opened, automatically reconnected
  on transient errors.
- Bulk inserts for screener writes — 40 tickers × ~15 signals each = 600
  rows per run; we batch these into a single executemany() for speed.
- Schema-aware: matches the schema in /tmp/rcg_signals_schema.sql exactly.
  If the schema evolves, this module evolves alongside.
- Driver: psycopg v3 (modern, statically-linked binary, no system libz dep).

PUBLIC API
----------
record_run(run_type, config=None, notes=None) -> run_id
record_signal(run_id, ticker, signal_name, value=None, ...)
record_signals_bulk(run_id, rows) -> n_inserted
finalize_run(run_id, n_in, n_out, runtime_seconds, output_path)
get_signal_history(ticker, signal_name, limit=100)
get_run_signals(run_id)

CONNECTION
----------
Uses Unix socket (/run/postgresql) with peer authentication. No password.
Database: rcg_signals  | User: nixos

USAGE
-----
    import signals_db as sdb

    # Start of a screener run
    rid = sdb.record_run('screener_daily',
                         config={'sentiment_override': 'BULLISH', 'cap_preset': 'all'},
                         notes='post-Phase 2A first capture')

    # Per-ticker signals during the run
    for ticker, scores in screener_output.items():
        sdb.record_signal(rid, ticker, 'composite_score', value=scores['composite'])
        sdb.record_signal(rid, ticker, 'pt_source',       string=scores['pt_source'])
        sdb.record_signal(rid, ticker, 'target_price',    value=scores['target'])

    # End of run
    sdb.finalize_run(rid, n_in=80, n_out=40, runtime_seconds=72.4,
                     output_path='/home/nixos/Prod/V1/outputs/long_screener_results.csv')

Author: RCG / Nick Diaz
Version: 1.0  (2026-04-29)
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

try:
    import psycopg
    from psycopg import sql
    _PSYCOPG_AVAILABLE = True
except ImportError:
    _PSYCOPG_AVAILABLE = False


# ============================================================
# CONFIG
# ============================================================
DB_NAME    = os.environ.get("RCG_SIGNALS_DB",       "rcg_signals")
DB_USER    = os.environ.get("RCG_SIGNALS_USER",     "nixos")
DB_HOST    = os.environ.get("RCG_SIGNALS_HOST",     "/run/postgresql")
DB_PORT    = int(os.environ.get("RCG_SIGNALS_PORT", "5432"))

CAPTURE_ENABLED = os.environ.get("RCG_SIGNALS_DISABLE", "").lower() not in ("1", "true", "yes")

logger = logging.getLogger("rcg.signals_db")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ============================================================
# CONNECTION MANAGEMENT
# ============================================================
_connection = None
_connection_failed_at = 0.0
_RETRY_AFTER_SECONDS = 60.0


def _get_connection():
    """
    Returns a live psycopg connection, lazily opening one or reconnecting
    on stale-connection errors. Returns None if the DB is unavailable
    (best-effort capture — never raises).
    """
    global _connection, _connection_failed_at

    if not CAPTURE_ENABLED:
        return None

    if not _PSYCOPG_AVAILABLE:
        logger.warning("psycopg not installed — signal capture disabled")
        return None

    if _connection_failed_at:
        elapsed = time.time() - _connection_failed_at
        if elapsed < _RETRY_AFTER_SECONDS:
            return None
        _connection_failed_at = 0.0

    if _connection is not None and not _connection.closed:
        try:
            with _connection.cursor() as cur:
                cur.execute("SELECT 1")
            return _connection
        except Exception:
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None

    try:
        # psycopg v3: use connection string. host as path = Unix socket.
        _connection = psycopg.connect(
            dbname=DB_NAME,
            user=DB_USER,
            host=DB_HOST,
            port=DB_PORT,
            autocommit=False,
        )
        return _connection
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        _connection_failed_at = time.time()
        _connection = None
        return None


@contextmanager
def _cursor():
    """Context manager: yields a cursor or None if DB is unavailable."""
    conn = _get_connection()
    if conn is None:
        yield None
        return
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception as e:
        logger.error(f"DB op failed, rolling back: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        global _connection
        try:
            conn.close()
        except Exception:
            pass
        _connection = None


# ============================================================
# WRITE API
# ============================================================
def record_run(run_type: str,
               config: Optional[dict] = None,
               notes: Optional[str] = None,
               git_commit: Optional[str] = None) -> Optional[int]:
    """
    Insert a row into `runs` and return its run_id. Returns None on DB failure.
    """
    with _cursor() as cur:
        if cur is None:
            return None
        cur.execute(
            """
            INSERT INTO runs (run_type, config_json, notes, git_commit)
            VALUES (%s, %s, %s, %s)
            RETURNING run_id
            """,
            (run_type,
             json.dumps(config) if config else None,
             notes,
             git_commit),
        )
        row = cur.fetchone()
        run_id = int(row[0]) if row else None
        logger.info(f"Started run {run_id} (type={run_type})")
        return run_id


def finalize_run(run_id: Optional[int],
                  n_in: Optional[int] = None,
                  n_out: Optional[int] = None,
                  runtime_seconds: Optional[float] = None,
                  output_path: Optional[str] = None) -> None:
    """Update the run row with end-of-run metadata. Silently skips if run_id is None."""
    if run_id is None:
        return
    with _cursor() as cur:
        if cur is None:
            return
        cur.execute(
            """
            UPDATE runs
               SET n_tickers_in    = COALESCE(%s, n_tickers_in),
                   n_tickers_out   = COALESCE(%s, n_tickers_out),
                   runtime_seconds = COALESCE(%s, runtime_seconds),
                   output_path     = COALESCE(%s, output_path)
             WHERE run_id = %s
            """,
            (n_in, n_out, runtime_seconds, output_path, run_id),
        )
        logger.info(f"Finalized run {run_id} (n_in={n_in}, n_out={n_out}, t={runtime_seconds}s)")


def record_signal(run_id: Optional[int],
                   ticker: str,
                   signal_name: str,
                   value: Optional[float] = None,
                   string: Optional[str] = None,
                   sector: Optional[str] = None,
                   asof_date: Optional[date] = None,
                   horizon_days: Optional[int] = None,
                   payload: Optional[dict] = None,
                   metadata: Optional[dict] = None) -> bool:
    """Insert a single row into `signals`. Returns True on success."""
    if run_id is None:
        return False
    if asof_date is None:
        asof_date = datetime.now(timezone.utc).date()

    with _cursor() as cur:
        if cur is None:
            return False
        cur.execute(
            """
            INSERT INTO signals
                (run_id, ticker, signal_name, signal_value, signal_string,
                 sector, asof_date, horizon_days, signal_json, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, ticker, signal_name, value, string,
             sector, asof_date, horizon_days,
             json.dumps(payload) if payload else None,
             json.dumps(metadata) if metadata else None),
        )
        return True


def record_signals_bulk(run_id: Optional[int],
                         rows: Iterable[dict]) -> int:
    """
    Bulk insert via psycopg executemany. Each row is a dict with keys:
        ticker (required)
        signal_name (required)
        value (optional float)
        string (optional str)
        sector (optional str)
        asof_date (optional date — defaults to today)
        horizon_days (optional int)
        payload (optional dict — serialized to JSONB)
        metadata (optional dict — serialized to JSONB)

    Returns count of rows inserted.
    """
    if run_id is None:
        return 0
    rows = list(rows)
    if not rows:
        return 0

    today = datetime.now(timezone.utc).date()

    tuples = []
    for r in rows:
        if "ticker" not in r or "signal_name" not in r:
            logger.warning(f"Skipping row missing ticker/signal_name: {r}")
            continue
        tuples.append((
            run_id,
            r["ticker"],
            r["signal_name"],
            r.get("value"),
            r.get("string"),
            r.get("sector"),
            r.get("asof_date") or today,
            r.get("horizon_days"),
            json.dumps(r["payload"])  if r.get("payload")  else None,
            json.dumps(r["metadata"]) if r.get("metadata") else None,
        ))

    if not tuples:
        return 0

    with _cursor() as cur:
        if cur is None:
            return 0
        # psycopg v3: use executemany. Roughly equivalent to v2's
        # execute_values for our row counts (hundreds, not millions).
        cur.executemany(
            """
            INSERT INTO signals
                (run_id, ticker, signal_name, signal_value, signal_string,
                 sector, asof_date, horizon_days, signal_json, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            tuples,
        )
        logger.info(f"Bulk inserted {len(tuples)} signals into run {run_id}")
        return len(tuples)


# ============================================================
# READ API
# ============================================================
def get_signal_history(ticker: str,
                        signal_name: str,
                        limit: int = 100) -> list:
    """Returns recent values of a named signal for a ticker (newest first)."""
    with _cursor() as cur:
        if cur is None:
            return []
        cur.execute(
            """
            SELECT asof_date, signal_value, signal_string, signal_json
              FROM signals
             WHERE ticker = %s AND signal_name = %s
             ORDER BY asof_date DESC, signal_id DESC
             LIMIT %s
            """,
            (ticker, signal_name, limit),
        )
        return [
            {"asof_date": r[0], "value": r[1], "string": r[2],
             "json": r[3] if r[3] else None}
            for r in cur.fetchall()
        ]


def get_run_signals(run_id: int) -> list:
    """Returns all signals from a given run."""
    with _cursor() as cur:
        if cur is None:
            return []
        cur.execute(
            """
            SELECT ticker, signal_name, signal_value, signal_string, sector
              FROM signals
             WHERE run_id = %s
             ORDER BY ticker, signal_name
            """,
            (run_id,),
        )
        return [
            {"ticker": r[0], "signal_name": r[1], "value": r[2],
             "string": r[3], "sector": r[4]}
            for r in cur.fetchall()
        ]


def get_recent_runs(run_type: Optional[str] = None, limit: int = 20) -> list:
    """Returns metadata about recent screener runs."""
    with _cursor() as cur:
        if cur is None:
            return []
        if run_type:
            cur.execute(
                """
                SELECT run_id, run_timestamp, run_type, n_tickers_out,
                       runtime_seconds, output_path
                  FROM runs
                 WHERE run_type = %s
                 ORDER BY run_timestamp DESC
                 LIMIT %s
                """,
                (run_type, limit),
            )
        else:
            cur.execute(
                """
                SELECT run_id, run_timestamp, run_type, n_tickers_out,
                       runtime_seconds, output_path
                  FROM runs
                 ORDER BY run_timestamp DESC
                 LIMIT %s
                """,
                (limit,),
            )
        return [
            {"run_id": r[0], "timestamp": r[1], "run_type": r[2],
             "n_tickers_out": r[3], "runtime_seconds": r[4], "output_path": r[5]}
            for r in cur.fetchall()
        ]


# ============================================================
# DIAGNOSTIC
# ============================================================
def health_check() -> dict:
    """
    Returns a status dict suitable for logging / dashboarding:
        {connected: bool, version: str, n_signals: int, n_runs: int,
         latest_run_at: str | None}
    """
    out = {"connected": False, "version": None, "n_signals": 0,
           "n_runs": 0, "latest_run_at": None}
    with _cursor() as cur:
        if cur is None:
            return out
        cur.execute("SELECT version()")
        out["version"] = cur.fetchone()[0].split(" on ")[0]

        cur.execute("SELECT COUNT(*) FROM signals")
        out["n_signals"] = int(cur.fetchone()[0])

        cur.execute("SELECT COUNT(*), MAX(run_timestamp) FROM runs")
        n, ts = cur.fetchone()
        out["n_runs"] = int(n)
        out["latest_run_at"] = ts.isoformat() if ts else None

        out["connected"] = True
    return out


# ============================================================
# Convenience entry point
# ============================================================
if __name__ == "__main__":
    import pprint
    print("=== RCG Signal DB Health Check ===")
    pprint.pprint(health_check())
