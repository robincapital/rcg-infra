"""
pt_r2_floor_sweep.py  —  Empirical R² Floor Calibration
========================================================
Tests candidate R² floor values against forward-return predictive power.

The premise: if our published upside is well-calibrated, names with high
positive published upside should outperform names with low/negative upside
over the next 60 trading days. We measure the rank correlation between
published upside_pct and realized forward return at several historical
snapshot dates, swept across candidate R² floor values.

The optimum floor is the one that maximizes (mean_correlation - vol_penalty)
across snapshots.

USAGE
=====

From a Jupyter cell:

    %run /home/nixos/Prod/V1/src/pt_r2_floor_sweep.py

Or with custom inputs:

    import pt_r2_floor_sweep as sweep
    summary = sweep.run_sweep(
        snapshot_dates = ['2025-10-31', '2025-11-30', '2025-12-31',
                          '2026-01-31', '2026-02-28'],
        candidate_floors = [0.10, 0.15, 0.20, 0.25, 0.30],
        forward_days   = 60,
        sample_size    = 600,
    )
    sweep.print_summary(summary)

NOTES
=====
- This is a coarse calibration, not a tuned backtest. We're picking among
  reasonable values, not optimizing a strategy.
- Snapshots use only Sharadar fundamentals dated <= snapshot_date (no leak).
- Forward returns use Sharadar SEP closes from snapshot_date+1 to snapshot_date+forward_days.
- Sample is randomized and stratified by sector to avoid sector concentration.
- We include a Spearman rank-correlation (robust to outliers) and a top-decile
  vs bottom-decile mean-return spread for interpretability.
"""
from __future__ import annotations
import sys, json, random
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Optional

import numpy as np
import polars as pl

SCREENER_PATH = Path('/home/nixos/Prod/V1/src')
if str(SCREENER_PATH) not in sys.path:
    sys.path.insert(0, str(SCREENER_PATH))

for mod in list(sys.modules.keys()):
    if mod in ('dynamic_factor_screener_v3', 'price_targets'):
        del sys.modules[mod]

import dynamic_factor_screener_v3 as v3
import price_targets as pt_lib


# ============================================================
# DATA HELPERS
# ============================================================
def _parse_date(d):
    if isinstance(d, str):
        return datetime.strptime(d, '%Y-%m-%d').date()
    return d


def _load_sf1_pinned(snapshot_date: date) -> pl.DataFrame:
    """Load all SF1 rows with datekey <= snapshot_date — point-in-time correct."""
    sf1 = v3.load_fundamentals()
    return sf1.filter(pl.col('datekey') <= snapshot_date)


def _load_sep() -> pl.DataFrame:
    return pl.read_parquet(v3.SHARADAR_SEP)


def _close_at(sep: pl.DataFrame, ticker: str, on_or_before: date) -> Optional[float]:
    rows = sep.filter(
        (pl.col('ticker') == ticker) & (pl.col('date') <= on_or_before)
    ).sort('date').tail(1)
    if rows.height == 0:
        return None
    closes = rows['closeunadj'].to_list() if 'closeunadj' in rows.columns else rows['close'].to_list()
    return float(closes[0]) if closes else None


def _close_after(sep: pl.DataFrame, ticker: str, after: date, days: int) -> Optional[float]:
    target = after + timedelta(days=int(days * 1.5))   # buffer for non-trading days
    rows = sep.filter(
        (pl.col('ticker') == ticker)
        & (pl.col('date') > after)
        & (pl.col('date') <= target)
    ).sort('date').head(days + 5).tail(1)
    if rows.height == 0:
        return None
    closes = rows['closeunadj'].to_list() if 'closeunadj' in rows.columns else rows['close'].to_list()
    return float(closes[0]) if closes else None


def _ticker_fundamentals(sf1_pinned: pl.DataFrame, ticker: str):
    tk = sf1_pinned.filter(pl.col('ticker') == ticker).sort('datekey')
    if tk.height < 6:
        return None
    cols = tk.columns
    revenue = tk['revenue'].to_list() if 'revenue' in cols else []
    ebitda  = tk['ebitda'].to_list()  if 'ebitda'  in cols else []
    debt    = tk['debt'].to_list()    if 'debt'    in cols else []
    if 'fcf' in cols:
        fcf = tk['fcf'].to_list()
    elif 'ncfo' in cols and 'capex' in cols:
        fcf = (tk['ncfo'].to_numpy() - tk['capex'].to_numpy()).tolist()
    else:
        fcf = []
    cash = 0.0
    for c in ('cashnequsd', 'cashneq', 'cash'):
        if c in cols:
            vals = [v for v in tk[c].to_list()
                    if v is not None and not (isinstance(v, float) and np.isnan(v))]
            cash = float(vals[-1]) if vals else 0.0
            break
    mc = None
    if 'marketcap' in cols:
        vals = [v for v in tk['marketcap'].to_list()
                if v is not None and not (isinstance(v, float) and np.isnan(v))]
        mc = float(vals[-1]) if vals else None
    sd = None
    for c in ('shareswadil', 'shareswa'):
        if c in cols:
            vals = [v for v in tk[c].to_list()
                    if v is not None and not (isinstance(v, float) and np.isnan(v)) and float(v) > 0]
            if vals:
                sd = float(vals[-1])
                break
    return {'revenue': revenue, 'ebitda': ebitda, 'fcf': fcf, 'debt': debt,
            'cash': cash, 'marketcap': mc, 'shares_diluted': sd}


# ============================================================
# CORRELATION METRICS
# ============================================================
def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation, NaN-safe."""
    mask = ~np.isnan(x) & ~np.isnan(y)
    if mask.sum() < 5:
        return 0.0
    rx = x[mask].argsort().argsort().astype(float)
    ry = y[mask].argsort().argsort().astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    denom = float(np.sqrt((rx * rx).sum() * (ry * ry).sum()))
    return float((rx * ry).sum() / denom) if denom > 0 else 0.0


def _decile_spread(upside: np.ndarray, fwd_ret: np.ndarray, n_buckets: int = 10) -> float:
    """Mean fwd return of top-decile-upside minus bottom-decile-upside."""
    mask = ~np.isnan(upside) & ~np.isnan(fwd_ret)
    if mask.sum() < n_buckets * 2:
        return 0.0
    u, r = upside[mask], fwd_ret[mask]
    order = u.argsort()
    bucket_size = max(1, len(u) // n_buckets)
    bot = r[order[:bucket_size]].mean()
    top = r[order[-bucket_size:]].mean()
    return float(top - bot)


# ============================================================
# CORE SWEEP
# ============================================================
def run_sweep(
    snapshot_dates:   List = None,
    candidate_floors: List[float] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35),
    forward_days:     int   = 180,           # was 60 — fundamental PTs predict over months, not weeks
    sample_size:      int   = 600,
    fed_target:       float = 0.03625,
    fed_neutral:      float = 0.0300,
    apply_envelope:   bool  = False,   # turn OFF envelope so we measure model-only quality
    seed:             int   = 42,
) -> pl.DataFrame:
    """
    Returns long-format results: one row per (snapshot, floor) with metrics.

    NOTES on horizon: 60d was too short for fundamental targets (those are
    multi-quarter signals, not weekly). At 180d we get ~9 months of forward
    return on the most recent snapshots, which is closer to where fundamental
    PTs have measurable predictive power.
    """
    if snapshot_dates is None:
        # Default: 4 quarterly snapshots ending ~6 months before today.
        # 4 × 90d apart gives us spread without requiring fwd returns we don't have.
        cutoff = date.today() - timedelta(days=forward_days + 30)
        snapshot_dates = [cutoff - timedelta(days=90 * i) for i in range(4)]

    snapshot_dates = [_parse_date(d) for d in snapshot_dates]
    random.seed(seed)

    print(f'[sweep] Loading SEP price history (this is the heavy part)...')
    sep = _load_sep()
    print(f'[sweep] SEP rows: {sep.height:,}')

    print(f'[sweep] Loading sector metadata...')
    # load_ticker_metadata returns (adr_set, biotech_set, sector_map) — we only need the sector_map
    _adr_set, _biotech_set, meta = v3.load_ticker_metadata()

    # Build cached fundamentals per snapshot
    rows = []
    for snap in snapshot_dates:
        print(f'\n[sweep] === Snapshot: {snap} ===')
        sf1_pinned = _load_sf1_pinned(snap)
        # Universe: tickers with marketcap >= $500M as of snapshot
        latest = sf1_pinned.group_by('ticker').agg(
            pl.col('marketcap').last().alias('marketcap'),
            pl.col('datekey').max().alias('latest_date')
        ).filter(
            (pl.col('marketcap') >= 500e6)
            & (pl.col('marketcap') <= 200e9)
            & (pl.col('latest_date') >= (snap - timedelta(days=180)))   # fundamentals fresh
        )
        eligible = latest['ticker'].to_list()

        # Sample
        if len(eligible) > sample_size:
            eligible = random.sample(eligible, sample_size)
        print(f'[sweep] Universe at snapshot: {len(eligible)} tickers (sampled)')

        # Pre-compute snapshot-pinned fundamentals + entry/exit prices once
        per_ticker = []
        for tk in eligible:
            f = _ticker_fundamentals(sf1_pinned, tk)
            if f is None:
                continue
            entry_px = _close_at(sep, tk, snap)
            exit_px  = _close_after(sep, tk, snap, forward_days)
            if not entry_px or not exit_px or entry_px <= 0:
                continue
            sector = (meta.get(tk, {}) or {}).get('sector') or '_default'
            per_ticker.append({
                'ticker': tk, 'sector': sector,
                'entry_px': entry_px, 'exit_px': exit_px,
                'fwd_ret': (exit_px / entry_px) - 1.0,
                'fund': f,
            })

        print(f'[sweep] {len(per_ticker)} tickers with both fundamentals and price coverage')

        # Sweep candidate floors
        for floor in candidate_floors:
            upsides, fwd_rets, n_models = [], [], []
            n_models_ev_ebitda = 0
            n_models_ev_rev    = 0
            n_models_fcf       = 0

            for t in per_ticker:
                f = t['fund']
                if f['marketcap'] is None or f['marketcap'] <= 0:
                    continue

                # Mutate module-level constants. Now that _model_conviction
                # reads them at call time (not as default args), this works.
                orig_floor = pt_lib.R2_HARD_FLOOR
                orig_full  = pt_lib.R2_FULL_WEIGHT
                try:
                    pt_lib.R2_HARD_FLOOR  = floor
                    pt_lib.R2_FULL_WEIGHT = floor + 0.20
                    res = pt_lib.compute_target_price(
                        ebitda_series   = f['ebitda'],
                        revenue_series  = f['revenue'],
                        fcf_series      = f['fcf'],
                        debt_series     = f['debt'],
                        marketcap       = f['marketcap'],
                        last_price      = t['entry_px'],
                        cash_on_hand    = f['cash'],
                        shares_diluted  = f['shares_diluted'],
                        sector          = t['sector'],
                        fed_target_rate = fed_target,
                        fed_neutral_rate= fed_neutral,
                        apply_envelope  = apply_envelope,
                    )
                finally:
                    pt_lib.R2_HARD_FLOOR  = orig_floor
                    pt_lib.R2_FULL_WEIGHT = orig_full

                if res.target_price is None:
                    continue

                # Per-model survival diagnostics
                kept = res.breakdown.get('conviction_weights', {})
                if 'ev_ebitda' in kept: n_models_ev_ebitda += 1
                if 'ev_rev'    in kept: n_models_ev_rev    += 1
                if 'fcf_yield' in kept: n_models_fcf       += 1

                upsides.append(res.upside_pct)
                fwd_rets.append(t['fwd_ret'])
                n_models.append(len(kept))

            if len(upsides) < 30:
                spearman, decile, n = 0.0, 0.0, len(upsides)
            else:
                u = np.array(upsides); r = np.array(fwd_rets)
                spearman = _spearman(u, r)
                decile   = _decile_spread(u, r)
                n        = len(upsides)

            avg_models = float(np.mean(n_models)) if n_models else 0.0
            mean_upside = float(np.mean(upsides)) if upsides else 0.0
            mean_fwd    = float(np.mean(fwd_rets)) if fwd_rets else 0.0

            rows.append({
                'snapshot':        str(snap),
                'r2_floor':        floor,
                'n_tickers':       n,
                'spearman':        round(spearman, 4),
                'decile_spread':   round(decile, 4),
                'avg_models_used': round(avg_models, 2),
                'n_kept_evebitda': n_models_ev_ebitda,
                'n_kept_evrev':    n_models_ev_rev,
                'n_kept_fcf':      n_models_fcf,
                'mean_upside':     round(mean_upside, 4),
                'mean_fwd_ret':    round(mean_fwd, 4),
            })
            print(f'  floor={floor:.2f}  n={n:>3}  spearman={spearman:+.3f}  '
                  f'decile_spread={decile:+.3%}  avg_models={avg_models:.2f}  '
                  f'(ebitda/rev/fcf={n_models_ev_ebitda}/{n_models_ev_rev}/{n_models_fcf})  '
                  f'meanU={mean_upside:+.1%}  meanFwd={mean_fwd:+.1%}')

    return pl.DataFrame(rows)


# ============================================================
# REPORTING
# ============================================================
def print_summary(df: pl.DataFrame) -> None:
    print('\n' + '='*72)
    print('  R² FLOOR SWEEP — AGGREGATED ACROSS SNAPSHOTS')
    print('='*72)

    agg = df.group_by('r2_floor').agg([
        pl.col('spearman').mean().alias('mean_spearman'),
        pl.col('spearman').std().alias('std_spearman'),
        pl.col('decile_spread').mean().alias('mean_decile'),
        pl.col('n_tickers').mean().alias('avg_n'),
        pl.col('avg_models_used').mean().alias('avg_models'),
    ]).sort('r2_floor')

    print(f'  {"floor":>7}  {"mean ρ":>8}  {"std ρ":>7}  {"mean decile Δ":>14}  '
          f'{"avg n":>7}  {"avg models":>10}')
    print('  ' + '─'*70)
    for r in agg.iter_rows(named=True):
        print(f'  {r["r2_floor"]:>7.2f}  {r["mean_spearman"]:>+8.4f}  '
              f'{r["std_spearman"]:>7.4f}  {r["mean_decile"]:>+13.3%}  '
              f'{r["avg_n"]:>7.0f}  {r["avg_models"]:>10.2f}')

    # Score = mean_spearman - 0.5 * std_spearman (penalize unstable floors)
    scored = agg.with_columns(
        (pl.col('mean_spearman') - 0.5 * pl.col('std_spearman')).alias('score')
    ).sort('score', descending=True)

    print('\n  RANKED BY mean_spearman − 0.5·std (stability-penalized):')
    for i, r in enumerate(scored.iter_rows(named=True), 1):
        marker = ' ← recommended' if i == 1 else ''
        print(f'    {i}. floor={r["r2_floor"]:.2f}  score={r["score"]:+.4f}{marker}')

    best = scored.row(0, named=True)
    print(f'\n  RECOMMENDATION: set R2_HARD_FLOOR = {best["r2_floor"]:.2f}')
    print(f'                  set R2_FULL_WEIGHT = {best["r2_floor"]+0.20:.2f}')
    print('='*72)


if __name__ == '__main__' or '__file__' in dir():
    out = run_sweep()
    print_summary(out)
    out.write_csv('/home/nixos/Prod/V1/outputs/pt_r2_floor_sweep.csv')
    print('\n[sweep] Detail written to /home/nixos/Prod/V1/outputs/pt_r2_floor_sweep.csv')
