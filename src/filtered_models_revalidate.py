"""
filtered_models_revalidate.py — periodic walk-forward IC tracking for
filtered model variants.

Run cadence: weekly (Sunday 09:00 ET via systemd timer rcg-filtered-models-revalidate).

What it does:
  - For each filtered model in MODELS_TO_TRACK:
    1. Pull all (score, r30) paired observations from the signals DB
    2. Split by date: oldest 60% train, newest 40% test
    3. Derive the model's filter parameters from TRAIN ONLY
    4. Compute test-set signed IC + hit rate with vs without the filter
    5. Append to outputs/filtered_revalidation_log.jsonl
  - Regenerate docs/filtered_revalidation_summary.md with the latest
    rolling-window stats so we can detect when filters decay.

Why this exists:
  arima_20_filtered (and any future *_filtered variants) were validated
  on small (10-day) samples. As more capture data accumulates, the IC
  estimate becomes more reliable. We want to know quickly if a filter
  that looked good at week 1 is decaying by week 4. This script is the
  ongoing watchdog.

Output schemas:

  outputs/filtered_revalidation_log.jsonl  (append-only)
    one row per (run_ts, model) per run:
    {
      "run_ts": "2026-05-28T16:00:00+00:00",
      "model": "arima_20",
      "filter": "good_hours",
      "train_days": 14, "test_days": 7,
      "train_n": 50000, "test_n": 25000,
      "train_baseline_ic": 0.06, "train_filtered_ic": 0.12,
      "test_baseline_ic": 0.07,  "test_filtered_ic": 0.15,
      "test_lift": 0.08, "verdict": "stable"
    }

  docs/filtered_revalidation_summary.md  (overwritten each run)
    human-readable table of the most recent N runs per model
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import psycopg

DB_DSN = "host=/run/postgresql user=nixos dbname=rcg_signals"
LOG_PATH = Path("/home/nixos/Prod/V1/outputs/filtered_revalidation_log.jsonl")
SUMMARY_PATH = Path("/home/nixos/Prod/V1/docs/filtered_revalidation_summary.md")

# ─── filter definitions ─────────────────────────────────────────────
# good_hours uses the actual fire (HH:MM) ET — the systemd timer fires
# at HH:08 and HH:38, so we match with ±5 min slack to allow cron drift.
GOOD_HOURS_ET = {(9, 38), (10, 8), (11, 8), (12, 8), (13, 8), (14, 8), (15, 8)}


def in_good_hours(et_h: int, et_m: int) -> bool:
    """Match an (hour, minute) ET pair to good_hours, allowing ±5 min slack."""
    for h, m in GOOD_HOURS_ET:
        if h == et_h and abs(et_m - m) <= 5:
            return True
    return False

MODELS_TO_TRACK = [
    {
        "model": "arima_20",
        "filter_name": "good_hours",
        "filter_type": "time_of_day",
    },
    # Add more here as filtered variants ship — same JSON structure.
]


# ─── helpers ────────────────────────────────────────────────────────
def sgn(v: float) -> int:
    return 1 if v > 0 else (-1 if v < 0 else 0)


def compute_ic(rows: list[tuple]) -> dict:
    """rows: list of (score, r30) tuples. Returns dict with n, ic, hit_rate."""
    n = len(rows)
    if n < 30:
        return {"n": n, "ic": None, "hit_rate": None}
    ic = sum(sgn(s) * sgn(r) for s, r in rows) / n
    hit_n = sum(1 for _, r in rows if r != 0)
    hit = sum(1 for s, r in rows if r != 0 and sgn(s) == sgn(r)) / max(hit_n, 1) if hit_n else 0
    return {"n": n, "ic": round(ic, 4), "hit_rate": round(hit, 4)}


def evaluate_model(model: str, filter_name: str) -> dict:
    """Pull all data for `model`, walk-forward split, compute IC vs filter."""
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            WITH s AS (
              SELECT s.ticker, to_timestamp(floor(EXTRACT(EPOCH FROM r.run_timestamp)/600)*600) AS bucket,
                     s.signal_value AS score, r.run_timestamp::date AS d,
                     EXTRACT(HOUR FROM r.run_timestamp AT TIME ZONE 'America/New_York')::int AS h,
                     EXTRACT(MINUTE FROM r.run_timestamp AT TIME ZONE 'America/New_York')::int AS m
              FROM signals s JOIN runs r ON s.run_id = r.run_id
              WHERE s.signal_name = %s AND s.signal_value IS NOT NULL
            ),
            rt AS (
              SELECT s.ticker, to_timestamp(floor(EXTRACT(EPOCH FROM r.run_timestamp)/600)*600) AS bucket,
                     s.signal_value AS r30
              FROM signals s JOIN runs r ON s.run_id = r.run_id
              WHERE s.signal_name = 'realized_return_30min_pct' AND s.signal_value IS NOT NULL
            )
            SELECT s.d, s.h, s.m, s.score, rt.r30
            FROM s JOIN rt ON s.ticker = rt.ticker AND s.bucket = rt.bucket
            WHERE ABS(s.score) >= 35
        """, (f"model_{model}_score",))
        rows = [(d, h, m, float(s), float(r)) for d, h, m, s, r in cur.fetchall()]

    dates = sorted({r[0] for r in rows})
    if len(dates) < 5:
        return {"error": f"insufficient_dates: {len(dates)}"}
    split = max(int(len(dates) * 0.6), 3)
    train_dates = set(dates[:split])
    test_dates  = set(dates[split:])

    train_rows = [(s, r) for d, h, m, s, r in rows if d in train_dates]
    test_rows  = [(s, r) for d, h, m, s, r in rows if d in test_dates]

    # Apply filter to TEST rows ONLY (filter is structural, doesn't need train)
    if filter_name == "good_hours":
        test_filtered = [(s, r) for d, h, m, s, r in rows
                         if d in test_dates and in_good_hours(h, m)]
        train_filtered = [(s, r) for d, h, m, s, r in rows
                          if d in train_dates and in_good_hours(h, m)]
    else:
        return {"error": f"unknown_filter: {filter_name}"}

    train_base = compute_ic(train_rows)
    train_filt = compute_ic(train_filtered)
    test_base  = compute_ic(test_rows)
    test_filt  = compute_ic(test_filtered)

    # Verdict: lift must survive out-of-time
    if test_base["ic"] is None or test_filt["ic"] is None:
        verdict = "insufficient_data"
    else:
        lift = test_filt["ic"] - test_base["ic"]
        if lift >= 0.03:
            verdict = "stable"
        elif lift > 0:
            verdict = "marginal"
        else:
            verdict = "DECAY"

    return {
        "model":              model,
        "filter":             filter_name,
        "train_days":         len(train_dates),
        "test_days":          len(test_dates),
        "first_train_date":   str(min(train_dates)),
        "last_test_date":     str(max(test_dates)),
        "train_baseline":     train_base,
        "train_filtered":     train_filt,
        "test_baseline":      test_base,
        "test_filtered":      test_filt,
        "test_lift":          (round(test_filt["ic"] - test_base["ic"], 4)
                               if test_base["ic"] is not None and test_filt["ic"] is not None else None),
        "verdict":            verdict,
    }


def append_log(run_ts: str, results: list[dict]) -> None:
    """Append one JSON line per result to the log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"run_ts": run_ts, **r}, default=str) + "\n")


def write_summary(results: list[dict]) -> None:
    """Overwrite docs/filtered_revalidation_summary.md with the latest run."""
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Filtered Models — Re-validation Summary",
        f"**Last run:** {now}",
        f"**Cadence:** weekly Sunday 09:00 ET via rcg-filtered-models-revalidate.timer",
        "",
        "Watchdog for the *_filtered model variants. Each run does a 60/40 train/test",
        "walk-forward on the full available capture history and reports test-set IC",
        "with vs without the filter.",
        "",
        "**Verdict:** `stable` (lift >= +0.03 abs), `marginal` (lift > 0 but < +0.03),",
        "`DECAY` (lift <= 0 — filter no longer adds edge).",
        "",
        "## This run",
        "",
        "| Model | Filter | Train d | Test d | Test base IC | Test filt IC | Lift | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r.get('model','?')} | — | — | — | — | — | — | error: {r['error']} |")
            continue
        verdict_emoji = {"stable": "✅", "marginal": "⚠️", "DECAY": "❌"}.get(r["verdict"], "?")
        def fmt(v):
            return f"{v:+.3f}" if v is not None else "—"
        tb = r["test_baseline"]["ic"]
        tf = r["test_filtered"]["ic"]
        lines.append(
            f"| `{r['model']}` | {r['filter']} | {r['train_days']} | {r['test_days']} | "
            f"{fmt(tb)} | {fmt(tf)} | {fmt(r['test_lift'])} | {verdict_emoji} {r['verdict']} |"
        )
    lines += [
        "",
        "## Historical log",
        "",
        f"Append-only log: `outputs/filtered_revalidation_log.jsonl`",
        "",
        "Read with duckdb to plot IC drift over time:",
        "```sql",
        "SELECT run_ts, model, test_lift, verdict",
        "FROM read_json_auto('outputs/filtered_revalidation_log.jsonl')",
        "ORDER BY run_ts DESC, model;",
        "```",
    ]
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    run_ts = datetime.now(timezone.utc).isoformat()
    print(f"[filtered_revalidate] starting {run_ts}", flush=True)
    results = []
    for cfg in MODELS_TO_TRACK:
        print(f"[filtered_revalidate] evaluating {cfg['model']} / {cfg['filter_name']}", flush=True)
        res = evaluate_model(cfg["model"], cfg["filter_name"])
        results.append(res)
        if "error" in res:
            print(f"  ! {cfg['model']}: {res['error']}", flush=True)
        else:
            tlift = (res['train_filtered']['ic'] - res['train_baseline']['ic']
                     if res['train_filtered']['ic'] is not None
                     and res['train_baseline']['ic'] is not None else None)
            tlift_s = f"{tlift:+.3f}" if tlift is not None else "n/a"
            test_lift_s = f"{res['test_lift']:+.3f}" if res['test_lift'] is not None else "n/a"
            print(f"  {cfg['model']}: train_lift={tlift_s} · test_lift={test_lift_s} · {res['verdict']}",
                  flush=True)
    append_log(run_ts, results)
    write_summary(results)
    print(f"[filtered_revalidate] wrote {LOG_PATH} and {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
