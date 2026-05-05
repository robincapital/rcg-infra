"""
pt_regression_harness.py  —  RCG Price Target Regression Tool
==============================================================
Runs BOTH the legacy and the new shared price-target engines on the same
universe of tickers (today's screener output) and emits a side-by-side
comparison so you can see exactly what would change before flipping the
live screener over.

USAGE
=====

From a Jupyter cell:

    %run /home/nixos/Prod/V1/src/pt_regression_harness.py

Or with custom inputs:

    import pt_regression_harness as harness
    df = harness.run_regression(
        screener_csv = '/home/nixos/Prod/V1/outputs/long_screener_results.csv',
        out_path     = '/home/nixos/Prod/V1/outputs/pt_regression.csv',
        fed_target   = 0.03625,
        fed_neutral  = 0.0300,
    )
    harness.print_summary(df)

OUTPUT
======
A CSV at the path you specify (default: pt_regression.csv) with columns:

    ticker, sector, last_price, marketcap_b, n_analysts, analyst_target,
    pt_old, upside_old_pct, pt_old_source,
    pt_new, upside_new_pct, pt_new_source,
    delta_pt_pct, gates_fired, quality_score, quality_haircut,
    dominant_model_old, dominant_model_new,
    classification

`classification` is one of:
    HALT       — the new engine refuses to publish a target (no models survived)
    CHANGE     — material PT change ( |Δ%| ≥ 25% )
    OK         — < 25% change, both engines roughly agree
    NEW_ONLY   — new produces a target where the old produced none
    OLD_ONLY   — old produced a target where new refuses
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from datetime import datetime

import numpy as np
import polars as pl

# Make sure the screener path is importable
SCREENER_PATH = Path('/home/nixos/Prod/V1/src')
if str(SCREENER_PATH) not in sys.path:
    sys.path.insert(0, str(SCREENER_PATH))

# Force a clean import of both engines
for mod in list(sys.modules.keys()):
    if mod in ('dynamic_factor_screener_v3', 'price_targets'):
        del sys.modules[mod]

import dynamic_factor_screener_v3 as v3
import price_targets as pt_new


def _load_fund_for_ticker(sf1, ticker: str):
    """Returns (revenue, ebitda, fcf, debt, marketcap, cash, shares_diluted) lists/floats."""
    tk = sf1.filter(pl.col('ticker') == ticker).sort('datekey')
    if tk.height < 3:
        return None

    cols = tk.columns
    revenue = tk['revenue'].to_list() if 'revenue' in cols else []
    ebitda  = tk['ebitda'].to_list()  if 'ebitda'  in cols else []
    debt    = tk['debt'].to_list()    if 'debt'    in cols else []

    if 'fcf' in cols:
        fcf = tk['fcf'].to_list()
    elif 'ncfo' in cols and 'capex' in cols:
        ncfo  = tk['ncfo'].to_numpy()
        capex = tk['capex'].to_numpy()
        fcf   = (ncfo - capex).tolist()
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

    shares_d = None
    for c in ('shareswadil', 'shareswa', 'sharesbas'):
        if c in cols:
            vals = [v for v in tk[c].to_list()
                    if v is not None and not (isinstance(v, float) and np.isnan(v)) and float(v) > 0]
            if vals:
                shares_d = float(vals[-1])
                break

    return {
        'revenue': revenue, 'ebitda': ebitda, 'fcf': fcf,
        'debt': debt, 'cash': cash, 'marketcap': mc,
        'shares_diluted': shares_d,
    }


def _classify(pt_old, pt_new, gates_fired, pt_new_source, threshold_pct: float = 0.25) -> str:
    if pt_new is None and pt_old is None:
        return 'HALT'
    if pt_new is None and pt_old is not None:
        return 'OLD_ONLY'
    if pt_old is None and pt_new is not None:
        return 'NEW_ONLY'
    # If new engine fell back to analyst consensus, that's a successful FALLBACK,
    # not a HALT — there IS a PT, it just came from analyst data not models.
    if pt_new_source == 'A':
        return 'FALLBACK_TO_ANALYST'
    delta = abs(pt_new - pt_old) / pt_old if pt_old else 0
    return 'CHANGE' if delta >= threshold_pct else 'OK'


def run_regression(
    screener_csv: str = '/home/nixos/Prod/V1/outputs/long_screener_results.csv',
    out_path:     str = '/home/nixos/Prod/V1/outputs/pt_regression.csv',
    fed_target:   float = 0.03625,
    fed_neutral:  float = 0.0300,
    apply_envelope: bool = True,
    fetch_analyst_targets: bool = True,
) -> pl.DataFrame:
    """
    Compares the legacy screener PT engine vs price_targets.py for every ticker
    in the screener output CSV.
    """
    print(f'[regression] Loading screener output from {screener_csv}')
    screen = pl.read_csv(screener_csv)
    print(f'[regression] {screen.height} tickers in screener output')

    print('[regression] Loading Sharadar SF1 fundamentals...')
    sf1 = v3.load_fundamentals()

    print('[regression] Loading sector/industry metadata...')
    # load_ticker_metadata returns (adr_set, biotech_set, sector_map) — we only need the sector_map
    _adr_set, _biotech_set, meta = v3.load_ticker_metadata()

    if fetch_analyst_targets:
        print('[regression] Fetching analyst price targets from Finnhub...')
        analyst_data = v3.fetch_analyst_price_targets(screen['ticker'].to_list())
    else:
        analyst_data = {}

    rows = []
    for r in screen.iter_rows(named=True):
        ticker = r['ticker']
        last_price = r.get('last_price')
        if not last_price or last_price <= 0:
            continue
        sector = (meta.get(ticker, {}) or {}).get('sector') or r.get('sector') or '_default'

        fund = _load_fund_for_ticker(sf1, ticker)
        if fund is None:
            continue

        # OLD engine — read what the screener actually published
        pt_old        = r.get('target_price') or r.get('internal_target')
        upside_old    = r.get('upside_pct')
        pt_old_source = r.get('pt_source') or 'M'
        try:
            pt_detail_old = json.loads(r.get('pt_detail_json') or '{}')
        except Exception:
            pt_detail_old = {}
        dominant_old  = pt_detail_old.get('dominant_model', 'N/A')

        # NEW engine — full pipeline incl. R² floor + quality + envelope
        adata     = analyst_data.get(ticker, {})
        atarget   = adata.get('target_mean')

        # n_analysts heuristic. Finnhub's recommendation endpoint (which fills
        # the screener's `analyst_count`) returns data for fewer tickers than
        # the price-target endpoint. When target_mean exists but n=0, infer
        # n=3 (the minimum required to engage Gate B) — Finnhub's price-target
        # endpoint generally only returns data when there are analysts.
        n_analyst = int(r.get('analyst_count') or 0)
        if n_analyst < 3 and atarget and atarget > 0:
            n_analyst = max(n_analyst, 3)

        new_res = pt_new.compute_target_price(
            ebitda_series   = fund['ebitda'],
            revenue_series  = fund['revenue'],
            fcf_series      = fund['fcf'],
            debt_series     = fund['debt'],
            marketcap       = float(fund['marketcap'] or r['marketcap']),
            last_price      = float(last_price),
            cash_on_hand    = float(fund['cash'] or 0),
            shares_diluted  = fund['shares_diluted'],
            sector          = sector,
            fed_target_rate = fed_target,
            fed_neutral_rate= fed_neutral,
            analyst_target  = atarget,
            n_analysts      = n_analyst,
            apply_envelope  = apply_envelope,
        )

        pt_n           = new_res.target_price
        upside_n       = new_res.upside_pct * 100 if new_res.target_price else None
        pt_n_source    = new_res.pt_source
        gates          = new_res.gates_fired
        quality        = new_res.quality_score
        quality_hair   = new_res.quality_haircut
        dominant_new   = new_res.breakdown.get('dominant_model', 'N/A')

        delta_pct = None
        if pt_old and pt_n and pt_old > 0:
            delta_pct = (pt_n / pt_old - 1) * 100

        cls = _classify(pt_old, pt_n, gates, pt_n_source)

        rows.append({
            'ticker':              ticker,
            'sector':              sector,
            'last_price':          round(float(last_price), 2),
            'marketcap_b':         round(float(r.get('marketcap', 0)) / 1e9, 2),
            'n_analysts':          n_analyst,
            'analyst_target':      round(float(atarget), 2) if atarget else None,
            'pt_old':              round(float(pt_old), 2)  if pt_old   else None,
            'upside_old_pct':      round(float(upside_old) * 100, 1) if upside_old is not None else None,
            'pt_old_source':       pt_old_source,
            'pt_new':              pt_n,
            'upside_new_pct':      round(float(upside_n), 1) if upside_n is not None else None,
            'pt_new_source':       pt_n_source,
            'delta_pt_pct':        round(float(delta_pct), 1) if delta_pct is not None else None,
            'gates_fired':         '; '.join(gates) if gates else '',
            'quality_score':       quality,
            'quality_haircut_pct': round(float(quality_hair) * 100, 1),
            'dominant_old':        dominant_old,
            'dominant_new':        dominant_new,
            'classification':      cls,
        })

    df = pl.DataFrame(rows)
    df = df.sort('classification').sort('delta_pt_pct', descending=True, nulls_last=True)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(out_path)
    print(f'[regression] Wrote {df.height} rows to {out_path}')
    return df


def print_summary(df: pl.DataFrame) -> None:
    """Pretty-print classification counts and the worst offenders."""
    print('\n' + '='*72)
    print('  REGRESSION SUMMARY')
    print('='*72)

    counts = df.group_by('classification').agg(pl.len().alias('n')).sort('n', descending=True)
    for r in counts.iter_rows(named=True):
        print(f'  {r["classification"]:10s}  {r["n"]:>3}')

    big_changes = df.filter(
        (pl.col('classification') == 'CHANGE') &
        (pl.col('delta_pt_pct').is_not_null())
    ).sort('delta_pt_pct', descending=False).head(15)

    print('\n  TOP 15 PT REDUCTIONS (old → new)')
    print('  ' + '─'*70)
    print(f'  {"TKR":<6}  {"OLD PT":>9}  {"NEW PT":>9}  {"Δ%":>7}  {"GATES"}')
    for r in big_changes.iter_rows(named=True):
        gates = (r["gates_fired"] or '')[:42]
        print(f'  {r["ticker"]:<6}  ${r["pt_old"] or 0:>8.2f}  ${r["pt_new"] or 0:>8.2f}  '
              f'{r["delta_pt_pct"] or 0:>+7.1f}%  {gates}')

    halts = df.filter(pl.col('classification') == 'HALT')
    if halts.height:
        print(f'\n  HALTED — no PT available at all ({halts.height} tickers)')
        print('  ' + '─'*70)
        for r in halts.head(20).iter_rows(named=True):
            print(f'  {r["ticker"]:<6}  ${r["pt_old"] or 0:>8.2f}  → no PT  '
                  f'(was source={r["pt_old_source"]})')

    fallbacks = df.filter(pl.col('classification') == 'FALLBACK_TO_ANALYST')
    if fallbacks.height:
        print(f'\n  FALLBACK TO ANALYST CONSENSUS ({fallbacks.height} tickers)')
        print('  ── all models dropped by R² floor; using analyst target as PT')
        print('  ' + '─'*70)
        for r in fallbacks.head(20).iter_rows(named=True):
            old = r["pt_old"] or 0
            new = r["pt_new"] or 0
            delta = (new/old - 1)*100 if old else 0
            print(f'  {r["ticker"]:<6}  old=${old:>8.2f}  new=${new:>8.2f}  '
                  f'Δ={delta:+6.1f}%  (model→analyst)')

    new_only = df.filter(pl.col('classification') == 'NEW_ONLY')
    if new_only.height:
        print(f'\n  NEW_ONLY ({new_only.height}) — new engine produces PT where old returned none')
    print('='*72)


# ------------------------------------------------------------
# Direct-run entry point (for `%run` from Jupyter)
# ------------------------------------------------------------
if __name__ == '__main__' or '__file__' in dir():
    try:
        df = run_regression()
        print_summary(df)
    except FileNotFoundError as e:
        print(f'[regression] {e}')
        print('[regression] Run the screener first to generate long_screener_results.csv')
