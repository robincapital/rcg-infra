"""
models_leaderboard.py — compute per-model IC + hit-rate + sample size

Reads signals table, finds every model_*_score paired with a
realized_return_<horizon>_pct on the same (run_id, ticker), and computes
performance stats per (model, horizon).

Emits /home/nixos/Prod/V1/outputs/leaderboard.json — fetched by the dashboard
to render the tournament scatter (X = sample size, Y = realized IC, color =
direction agreement, size = avg |signal|).

Run cadence: daily after forward_returns_capture has had time to populate.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import psycopg

OUTPUT_PATH = Path("/home/nixos/Prod/V1/outputs/leaderboard.json")
HORIZONS = ["30min", "60min", "4h"]


def spearman(xs, ys):
    """Spearman rank correlation. Returns 0 on degenerate input."""
    n = len(xs)
    if n < 3: return 0.0
    rx = _ranks(xs); ry = _ranks(ys)
    mean_rx = sum(rx) / n; mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = (sum((rx[i] - mean_rx) ** 2 for i in range(n))) ** 0.5
    den_y = (sum((ry[i] - mean_ry) ** 2 for i in range(n))) ** 0.5
    if den_x == 0 or den_y == 0: return 0.0
    return num / (den_x * den_y)


def _ranks(arr):
    """Average-rank (handles ties)."""
    indexed = sorted(enumerate(arr), key=lambda x: x[1])
    ranks = [0.0] * len(arr)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def main():
    # Discover all model_* score signal_names + matching realized return signals
    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        # ─── Find all model score signal names ───
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT signal_name
                FROM signals
                WHERE signal_name LIKE 'model_%_score'
                """
            )
            score_names = sorted(r[0] for r in cur.fetchall())

        if not score_names:
            print("[leaderboard] no model_* signals found yet")
            return

        # ─── Pull all (model_score, realized_return) pairs per horizon ───
        # We aggregate across ALL captured runs in the DB (no time window;
        # tournament covers the full history).
        results = []
        for score_name in score_names:
            model_label = score_name.replace("model_", "").replace("_score", "")
            for horizon in HORIZONS:
                ret_name = f"realized_return_{horizon}_pct"
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT score.signal_value, ret.signal_value
                        FROM signals score
                        JOIN signals ret
                          ON score.run_id = ret.run_id AND score.ticker = ret.ticker
                        WHERE score.signal_name = %s
                          AND ret.signal_name   = %s
                          AND score.signal_value IS NOT NULL
                          AND ret.signal_value   IS NOT NULL
                        """,
                        (score_name, ret_name),
                    )
                    pairs = cur.fetchall()

                n = len(pairs)
                if n == 0:
                    results.append({
                        "model":          model_label,
                        "horizon":        horizon,
                        "n":              0,
                        "hit_rate":       None,
                        "ic_directional": None,
                        "ic_spearman":    None,
                        "avg_score_abs":  None,
                        "avg_realized":   None,
                    })
                    continue

                scores = [float(p[0]) for p in pairs]
                rets   = [float(p[1]) for p in pairs]

                # Sign-match hit rate (only counts predictions with non-trivial magnitude)
                strong = [(s, r) for s, r in zip(scores, rets) if abs(s) >= 5]
                hits = sum(1 for s, r in strong
                           if (s > 0 and r > 0) or (s < 0 and r < 0))
                hit_rate = hits / len(strong) if strong else None

                # Directional IC = sign(score) × sign(ret) averaged
                ic_dir_pairs = [(1 if s > 0 else -1 if s < 0 else 0)
                                * (1 if r > 0 else -1 if r < 0 else 0)
                                for s, r in zip(scores, rets)]
                ic_dir = sum(ic_dir_pairs) / n if n else None

                ic_sp = spearman(scores, rets) if n >= 5 else None

                results.append({
                    "model":          model_label,
                    "horizon":        horizon,
                    "n":              n,
                    "n_strong":       len(strong),
                    "hit_rate":       round(hit_rate, 4) if hit_rate is not None else None,
                    "ic_directional": round(ic_dir, 4) if ic_dir is not None else None,
                    "ic_spearman":    round(ic_sp, 4) if ic_sp is not None else None,
                    "avg_score_abs":  round(sum(abs(s) for s in scores) / n, 2),
                    "avg_realized":   round(sum(rets) / n, 4),
                })

    # Same computation for the BBG-derived live-prediction composite
    # so it shows in the leaderboard alongside the named models.
    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        for horizon in HORIZONS:
            ret_name = f"realized_return_{horizon}_pct"
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT score.signal_value, ret.signal_value
                    FROM signals score
                    JOIN signals ret
                      ON score.run_id = ret.run_id AND score.ticker = ret.ticker
                    WHERE score.signal_name = 'pred_signed_score'
                      AND ret.signal_name   = %s
                      AND score.signal_value IS NOT NULL
                      AND ret.signal_value   IS NOT NULL
                    """,
                    (ret_name,),
                )
                pairs = cur.fetchall()
            n = len(pairs)
            if n == 0:
                results.append({"model": "bbg_predictive_composite", "horizon": horizon,
                                "n": 0, "hit_rate": None, "ic_directional": None,
                                "ic_spearman": None, "avg_score_abs": None, "avg_realized": None})
                continue
            scores = [float(p[0]) for p in pairs]; rets = [float(p[1]) for p in pairs]
            strong = [(s, r) for s, r in zip(scores, rets) if abs(s) >= 5]
            hits = sum(1 for s, r in strong if (s > 0 and r > 0) or (s < 0 and r < 0))
            hit_rate = hits / len(strong) if strong else None
            ic_dir_pairs = [(1 if s > 0 else -1 if s < 0 else 0)
                            * (1 if r > 0 else -1 if r < 0 else 0)
                            for s, r in zip(scores, rets)]
            ic_dir = sum(ic_dir_pairs) / n
            ic_sp = spearman(scores, rets) if n >= 5 else None
            results.append({
                "model":          "bbg_predictive_composite",
                "horizon":        horizon,
                "n":              n,
                "n_strong":       len(strong),
                "hit_rate":       round(hit_rate, 4) if hit_rate is not None else None,
                "ic_directional": round(ic_dir, 4),
                "ic_spearman":    round(ic_sp, 4) if ic_sp is not None else None,
                "avg_score_abs":  round(sum(abs(s) for s in scores) / n, 2),
                "avg_realized":   round(sum(rets) / n, 4),
            })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizons":     HORIZONS,
        "results":      results,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, default=str, indent=2))
    print(f"[leaderboard] wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes) "
          f"· {len(results)} (model, horizon) entries")
    # Print a small summary
    by_model = defaultdict(list)
    for r in results: by_model[r["model"]].append(r)
    for model, rs in by_model.items():
        rs.sort(key=lambda x: x["horizon"])
        for r in rs:
            ic = r['ic_directional']
            ic_str = f"{ic:+.3f}" if ic is not None else "  —  "
            print(f"  {model:30s} {r['horizon']:6s} n={r['n']:>3}  hit={(r['hit_rate'] or 0)*100:>5.1f}%  ic_dir={ic_str}")


if __name__ == "__main__":
    main()
