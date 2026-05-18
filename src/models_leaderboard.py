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
import regime_tag

OUTPUT_PATH = Path("/home/nixos/Prod/V1/outputs/leaderboard.json")
HORIZONS = ["30min", "60min", "4h"]


# Fallback family mapping: most signal rows in the DB predate the family
# tag in runs.config_json. Until those age out (~7 days), we infer family
# from the model name. New entrants added to models_capture.py also need
# to be added here OR they'll show as "other".
def family_from_model(model_name: str) -> str:
    n = model_name.lower()
    if n == "bbg_predictive_composite":      return "bbg_composite"
    if n.startswith("momentum_"):            return "momentum"
    if n.startswith("mean_rev"):             return "mean_reversion"
    if n.startswith("rsi_extreme"):          return "rsi_extreme"
    if n.startswith("sma_cross"):            return "sma_cross"
    if n.startswith("ema_cross"):            return "ema_cross"
    if n.startswith("bb_squeeze"):           return "bollinger_pos"   # v24
    if n.startswith("bollinger_pos"):        return "bollinger_pos"
    if n.startswith("donchian_break"):       return "donchian_break"
    if n.startswith("lr_slope"):             return "lr_slope"
    if n.startswith("arima") or n.startswith("ar2"):  return "arima"  # v24
    if n.startswith("combo_"):               return "ensemble"
    # v24 — new families
    if n.startswith("hurst") or n.startswith("kalman") or n.startswith("ou_halflife"):
        return "pattern"
    if (n.startswith("relative_strength_rank")
            or n.startswith("sector_relative_momentum")
            or n.startswith("pca_residual")):
        return "cross_sectional"
    # v26 — Stage 1 OLS meta-blend
    if n.startswith("meta_blend"):
        return "meta_blend"
    return "other"


def _metrics(scores, rets):
    """Compute hit_rate, IC directional, IC Spearman, avg-score-abs, avg-realized
    from parallel arrays of (score, realized_return). Returns dict with the
    keys the dashboard expects, or empty dict if no data."""
    n = len(scores)
    if n == 0:
        return {"n": 0, "n_strong": 0, "hit_rate": None,
                "ic_directional": None, "ic_spearman": None,
                "avg_score_abs": None, "avg_realized": None}
    strong = [(s, r) for s, r in zip(scores, rets) if abs(s) >= 5]
    hits = sum(1 for s, r in strong if (s > 0 and r > 0) or (s < 0 and r < 0))
    hit_rate = hits / len(strong) if strong else None
    ic_dir_pairs = [(1 if s > 0 else -1 if s < 0 else 0)
                    * (1 if r > 0 else -1 if r < 0 else 0)
                    for s, r in zip(scores, rets)]
    ic_dir = sum(ic_dir_pairs) / n
    ic_sp = spearman(scores, rets) if n >= 5 else None
    return {
        "n":              n,
        "n_strong":       len(strong),
        "hit_rate":       round(hit_rate, 4) if hit_rate is not None else None,
        "ic_directional": round(ic_dir, 4) if ic_dir is not None else None,
        "ic_spearman":    round(ic_sp, 4) if ic_sp is not None else None,
        "avg_score_abs":  round(sum(abs(s) for s in scores) / n, 2),
        "avg_realized":   round(sum(rets) / n, 4),
    }


def _stratify_by_regime(triples):
    """
    triples: list of (score, return, regime_label_or_None)
    Returns: dict mapping regime_label -> metrics dict. Includes 'all' as the
    overall (unstratified) bucket.
    """
    by_regime = defaultdict(list)
    for s, r, lbl in triples:
        by_regime["all"].append((s, r))
        if lbl:
            by_regime[lbl].append((s, r))
    return {
        lbl: _metrics([s for s, _ in pairs], [r for _, r in pairs])
        for lbl, pairs in by_regime.items()
    }


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

        # ─── Pull all (model_score, realized_return, regime, family) triples per horizon ───
        # We aggregate across ALL captured runs in the DB (no time window).
        # Family + regime are read from the score-run's config_json (set by
        # models_capture at fire-time). Runs from before these tags were
        # added have NULL values and just collapse into "all" / no-family.
        results = []
        for score_name in score_names:
            model_label = score_name.replace("model_", "").replace("_score", "")
            family_seen = None
            for horizon in HORIZONS:
                ret_name = f"realized_return_{horizon}_pct"
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT score.signal_value, ret.signal_value,
                               r.config_json -> 'regime' ->> 'regime_label' AS regime_label,
                               r.config_json ->> 'family'                   AS family
                        FROM signals score
                        JOIN signals ret
                          ON score.run_id = ret.run_id AND score.ticker = ret.ticker
                        JOIN runs r ON r.run_id = score.run_id
                        WHERE score.signal_name = %s
                          AND ret.signal_name   = %s
                          AND score.signal_value IS NOT NULL
                          AND ret.signal_value   IS NOT NULL
                        """,
                        (score_name, ret_name),
                    )
                    rows = cur.fetchall()
                triples = [(float(s), float(r), lbl) for s, r, lbl, _ in rows]
                # Last non-null family wins (we just need the label, not stats)
                for _, _, _, fam in rows:
                    if fam:
                        family_seen = fam
                        break

                by_regime = _stratify_by_regime(triples)
                overall = by_regime.get("all", _metrics([], []))
                row = {
                    "model":          model_label,
                    "family":         family_seen or family_from_model(model_label),
                    "horizon":        horizon,
                    **overall,                   # n, hit_rate, ic_directional, ic_spearman, ...
                    "by_regime":      {k: v for k, v in by_regime.items() if k != "all"},
                }
                results.append(row)

    # Same computation for the BBG-derived live-prediction composite
    # so it shows in the leaderboard alongside the named models.
    with psycopg.connect("host=/run/postgresql user=nixos dbname=rcg_signals") as conn:
        for horizon in HORIZONS:
            ret_name = f"realized_return_{horizon}_pct"
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT score.signal_value, ret.signal_value,
                           r.config_json -> 'regime' ->> 'regime_label' AS regime_label
                    FROM signals score
                    JOIN signals ret
                      ON score.run_id = ret.run_id AND score.ticker = ret.ticker
                    JOIN runs r ON r.run_id = score.run_id
                    WHERE score.signal_name = 'pred_signed_score'
                      AND ret.signal_name   = %s
                      AND score.signal_value IS NOT NULL
                      AND ret.signal_value   IS NOT NULL
                    """,
                    (ret_name,),
                )
                triples = [(float(s), float(r), lbl) for s, r, lbl in cur.fetchall()]
            by_regime = _stratify_by_regime(triples)
            overall = by_regime.get("all", _metrics([], []))
            results.append({
                "model":          "bbg_predictive_composite",
                "family":         "bbg_composite",
                "horizon":        horizon,
                **overall,
                "by_regime":      {k: v for k, v in by_regime.items() if k != "all"},
            })

    # ─── Champion per (family, horizon) ─────────────────────────────────
    # Highest IC directional wins. Sample-size guard: variants with n < 50
    # aren't eligible to be champion (they're warming up). The dashboard
    # surfaces the champion prominently; other variants collapse under it
    # as challengers that keep running.
    MIN_N_FOR_CHAMPION = 50
    champions = {}                 # (family, horizon) → model_name
    by_fam_hz = {}
    for r in results:
        if not r.get("family"): continue
        key = (r["family"], r["horizon"])
        by_fam_hz.setdefault(key, []).append(r)
    for key, rows in by_fam_hz.items():
        eligible = [r for r in rows if (r.get("n") or 0) >= MIN_N_FOR_CHAMPION
                                       and r.get("ic_directional") is not None]
        if eligible:
            best = max(eligible, key=lambda r: r["ic_directional"])
            champions[f"{key[0]}|{key[1]}"] = best["model"]
            best["is_champion"] = True
        # Mark family + horizon list (so dashboard can rank within a family even
        # when sorted by some other column)
        family_rank_by_ic = sorted(rows, key=lambda r: r.get("ic_directional") or -2, reverse=True)
        for i, rr in enumerate(family_rank_by_ic):
            rr["family_rank"] = i + 1
            rr["family_size"] = len(rows)

    current_regime = regime_tag.compute_regime()
    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "horizons":        HORIZONS,
        "current_regime":  current_regime,
        "regime_labels":   regime_tag.ALL_REGIME_LABELS,
        "champions":       champions,        # "family|horizon" → model_name
        "results":         results,
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
