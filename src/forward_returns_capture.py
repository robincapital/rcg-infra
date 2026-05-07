"""
forward_returns_capture.py — join predictions with realized forward returns

For every captured live_prediction snapshot at time T with entry price P, find
later snapshots of the same ticker around T + horizon and compute the realized
return. Stored as additional signals on the ORIGINAL prediction's run_id so
the existing /predictions endpoint surfaces them automatically.

Horizons computed here (INTRADAY only — diff successive prediction snapshots):
  30min, 60min, 4h

Daily horizons (1d, 5d, 20d) come from a separate process that runs after
the morning Sharadar pull, joining with EOD closes from SEP.

Idempotent — predictions that already have realized_return_<horizon> signals
are skipped. Designed to be safe to run repeatedly.

Run cadence:
  systemd timer every 30 min (after each predictions_capture). Newer
  predictions get their realized returns filled in as time elapses.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402

import psycopg

# ─── Horizons (label → timedelta + tolerance window) ────────────────────────
HORIZONS = [
    # (label, target_offset, tolerance_+/-, signal_name)
    ("30min", timedelta(minutes=30), timedelta(minutes=15)),
    ("60min", timedelta(minutes=60), timedelta(minutes=15)),
    ("4h",    timedelta(hours=4),    timedelta(minutes=30)),
]

# Look back this far when scanning for predictions to fill in.
LOOKBACK_HOURS = 24 * 7  # 7 days


def signal_name_for(horizon_label: str) -> str:
    return f"realized_return_{horizon_label}_pct"


def main() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    # We process BOTH live_prediction (the main BBG predictive composite) AND
    # model_score runs (the tournament entrants). Both tables capture live_price
    # per ticker per fire, so the same diff-successive-snapshots logic applies.
    RUN_TYPES = ("live_prediction", "model_score")

    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        # ─── Pull all (run_id, ticker, ts, live_price) for relevant run types
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.run_id, s.ticker, r.run_timestamp, s.signal_value
                FROM signals s JOIN runs r ON s.run_id = r.run_id
                WHERE r.run_type    = ANY(%s)
                  AND s.signal_name = 'live_price'
                  AND r.run_timestamp >= %s
                ORDER BY s.ticker ASC, r.run_timestamp ASC
                """,
                (list(RUN_TYPES), cutoff),
            )
            rows = cur.fetchall()

        # ─── Pull all already-computed realized_return signals (skip set) ──
        already = set()
        for h_label, _, _ in HORIZONS:
            sn = signal_name_for(h_label)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.run_id, s.ticker
                    FROM signals s JOIN runs r ON s.run_id = r.run_id
                    WHERE r.run_type    = ANY(%s)
                      AND s.signal_name = %s
                      AND r.run_timestamp >= %s
                    """,
                    (list(RUN_TYPES), sn, cutoff),
                )
                for run_id, ticker in cur.fetchall():
                    already.add((run_id, ticker, sn))

    # ─── Group predictions by ticker, sorted by ts ──────────────────────
    by_ticker: dict[str, list] = {}
    for run_id, ticker, ts, price in rows:
        if price is None or price <= 0:
            continue
        by_ticker.setdefault(ticker, []).append({
            "run_id": run_id,
            "ts":     ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc),
            "price":  float(price),
        })

    # ─── Match each prediction to a target snapshot per horizon ─────────
    inserts = []  # list of (run_id, ticker, signal_name, value)
    n_skipped_already = 0
    n_skipped_no_match = 0

    for ticker, preds in by_ticker.items():
        for i, p in enumerate(preds):
            for h_label, target_offset, tol in HORIZONS:
                sn = signal_name_for(h_label)
                if (p["run_id"], ticker, sn) in already:
                    n_skipped_already += 1
                    continue

                target_ts = p["ts"] + target_offset
                tol_lo, tol_hi = target_ts - tol, target_ts + tol

                # Find a later snapshot of the same ticker within the tolerance
                match = None
                for q in preds[i+1:]:
                    if q["ts"] < tol_lo:
                        continue
                    if q["ts"] > tol_hi:
                        break
                    match = q
                    break

                if match is None:
                    # Either too soon (horizon hasn't elapsed yet) or there
                    # really is no follow-up snapshot in the window
                    if datetime.now(timezone.utc) >= tol_hi:
                        n_skipped_no_match += 1
                    continue

                ret_pct = (match["price"] - p["price"]) / p["price"] * 100
                inserts.append((p["run_id"], ticker, sn, ret_pct))

    # ─── Write back as signals on each prediction's original run_id ─────
    if not inserts:
        print(f"[forward-returns] nothing to insert "
              f"(already-have={n_skipped_already}, no-match={n_skipped_no_match})")
        return

    n_written = 0
    for run_id, ticker, sn, val in inserts:
        sdb.record_signal(run_id, ticker, sn, value=float(val))
        n_written += 1

    print(f"[forward-returns] wrote {n_written} realized-return signals "
          f"(already-have={n_skipped_already}, no-match={n_skipped_no_match})")


if __name__ == "__main__":
    main()
