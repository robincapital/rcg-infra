"""
markout_eval.py — trade-simulation engine for tournament models.

Implements the "Inflection Threshold Trader" rule (per
docs/markout_dashboard_spec.md §2):

  - Long entry:  score >= +60
  - Short entry: score <= -60
  - Exit:        |score| < 35 (hysteresis: dead zone closes positions)
  - Hold band:   35 <= |score| < 60 — existing positions held, no new entries
  - Sizing:      equal weight across active positions
  - Max concurrent: 15 positions (per RCG policy §10 diversification floor)
  - Costs:       5 bps/year platform drag + N bps/side slippage (default 5)

Reads from signals + runs tables. Emits per-day equity curve + summary
stats + trade log for the dashboard (markout_eval_publish.py orchestrates
the loop across all models × horizons).

Position-P&L marking strategy
─────────────────────────────
Between fires, position P&L is tracked using the **30min realized forward
return** captured at each fire. This is dense (every fire during market
hours) and doesn't require new data joins or a separate price feed.

To AVOID look-ahead bias: at fire T we mark existing positions using r30
at the PREVIOUS fire (which covers prev→T — already-realized history).
The r30 at fire T itself covers T→T+30 (still in the future at decision
time) and is applied at the next fire.

Bucketing: fires are grouped into 10-minute buckets (matches
meta_model.py convention) so score-runs and realized-return-runs that
fire seconds-to-minutes apart land in the same observation row.

Limitations in v1:
  - Overnight gaps (EOD → next-day open) are NOT modeled. Positions
    effectively "freeze" overnight. Conservatively understates volatility
    and slightly understates P&L on directional moves overnight.
  - Lunch-break gaps (12:00 fire to 13:00 fire — ~30min uncovered) are
    similarly ignored. Same direction of bias, smaller magnitude.
Refine in v2 by stitching SEP daily prices for overnight returns.

Author: RCG Quant Agent (under MM direction, v27 Day 1)
Date:   2026-05-19
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Optional

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import psycopg


# ────────────────────────────────────────────────────────────────────────
# Configuration — the trade-rule constants live here so the publisher
# script can override them and the doc cites a single source of truth.
# ────────────────────────────────────────────────────────────────────────
ENTRY_THRESHOLD       = 60.0       # |score| >= 60 → eligible to open
EXIT_THRESHOLD        = 35.0       # |score| < 35 → close any open position
MAX_CONCURRENT        = 15         # max positions held simultaneously
PLATFORM_FEE_BPS_YEAR = 5.0        # constant annual drag (modeled as daily decrement)
DEFAULT_SLIPPAGE_BPS  = 5.0        # per side, on entry and exit
TRADING_DAYS_PER_YEAR = 252

DB_DSN = "host=/run/postgresql user=nixos dbname=rcg_signals"

# Marker horizons — for now the simulation always marks positions to market
# using the 30min realized return (densest signal). The horizon parameter
# only selects which model variant's score we read (e.g. meta_blend_60min
# uses the 60min-horizon trained weights).
MARKING_HORIZON = "30min"

# Fire bucketing: 10-minute buckets group score-runs and realized-return-runs
# that fire seconds-to-minutes apart into the same observation. Same as
# meta_model.py to keep conventions consistent.
BUCKET_SECONDS = 600


# ────────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────────
@dataclass
class Position:
    ticker:        str
    direction:     int                 # +1 = long, -1 = short
    entry_time:    datetime
    entry_score:   float
    weight:        float = 0.0         # set on entry, re-set on rebalance


@dataclass
class TradeEvent:
    fire_time:     datetime
    ticker:        str
    event:         str                 # "ENTRY" | "EXIT"
    direction:     int
    score:         float
    weight:        float


@dataclass
class SimResult:
    model_name:               str
    horizon:                  str
    slippage_bps:             float
    n_fires:                  int
    n_trades:                 int       # COMPLETED round-trips (closed positions)
    n_open_at_end:            int       # positions still open when simulation ended
    n_long:                   int       # completed long round-trips
    n_short:                  int       # completed short round-trips
    daily_equity_gross:       dict      # date → equity (start=1.0)
    daily_equity_net:         dict      # date → equity after costs
    daily_returns_gross:      dict      # date → daily % return
    daily_returns_net:        dict
    cum_return_gross:         float
    cum_return_net:           float
    sharpe_net:               float
    max_dd_net:               float
    hit_rate:                 float     # fraction of round-trips that ended in-the-money
    avg_hold_minutes:         float
    trade_log:                list      # list of TradeEvent
    # Per-ticker bookkeeping for the dashboard heatmap
    per_ticker_contribution:  dict      # ticker → {n_trades, cum_pnl, n_long, n_short}


# ────────────────────────────────────────────────────────────────────────
# Data layer — pull (fire_time, ticker, score) + (fire_time, ticker, r30)
# ────────────────────────────────────────────────────────────────────────
def _pull_fires(
    model_name: str,
    horizon: str,
    cutoff_days: Optional[int] = None,
    cutoff_end: Optional[datetime] = None,
) -> tuple[dict, dict]:
    """
    Returns:
      scores_by_bucket:  {bucket_time: {ticker: score}}  — for the selected model
      r30_by_bucket:     {bucket_time: {ticker: r30_pct}} — for position marking

    Fires are grouped into 10-minute buckets so score-runs and
    realized-return-runs that fire seconds-to-minutes apart land in the
    same bucket. Same convention as meta_model.py.
    """
    # signal_name format on disk is `model_<stem>_score` (no horizon embedded
    # except for meta_blend which has `model_meta_blend_<horizon>_score`).
    score_signal = f"model_{model_name}_score"
    r30_signal   = f"realized_return_{MARKING_HORIZON}_pct"

    where_clause = ""
    params: list = [BUCKET_SECONDS, BUCKET_SECONDS, score_signal, r30_signal]
    if cutoff_days is not None:
        end = (cutoff_end or datetime.now(timezone.utc))
        start_ts = end.timestamp() - cutoff_days * 86400
        where_clause = " AND r.run_timestamp > to_timestamp(%s) AND r.run_timestamp <= %s"
        params += [start_ts, end]

    sql = f"""
        SELECT to_timestamp(floor(EXTRACT(EPOCH FROM r.run_timestamp) / %s) * %s) AS bucket_time,
               s.ticker, s.signal_name, s.signal_value
        FROM signals s
        JOIN runs r ON s.run_id = r.run_id
        WHERE s.signal_name IN (%s, %s)
          AND s.signal_value IS NOT NULL
          {where_clause}
        ORDER BY bucket_time
    """

    scores_by_bucket: dict = defaultdict(dict)
    r30_by_bucket:    dict = defaultdict(dict)

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        for bucket_time, ticker, sname, val in cur.fetchall():
            v = float(val)
            if sname == score_signal:
                # If multiple runs land in the same bucket (rare), last-write-wins
                scores_by_bucket[bucket_time][ticker] = v
            elif sname == r30_signal:
                r30_by_bucket[bucket_time][ticker] = v

    return dict(scores_by_bucket), dict(r30_by_bucket)


# ────────────────────────────────────────────────────────────────────────
# Core simulation
# ────────────────────────────────────────────────────────────────────────
def simulate(
    model_name:    str,
    horizon:       str,
    slippage_bps:  float = DEFAULT_SLIPPAGE_BPS,
    cutoff_days:   Optional[int] = None,
    cutoff_end:    Optional[datetime] = None,
    entry_thresh:  float = ENTRY_THRESHOLD,
    exit_thresh:   float = EXIT_THRESHOLD,
    max_concurrent: int  = MAX_CONCURRENT,
) -> SimResult:
    """
    Run the trade simulation for one model × horizon.

    Parameters
    ----------
    model_name : str
        Without the "model_" prefix or "_score" suffix.
        E.g. "momentum_120_60min", "meta_blend_30min".
    horizon : str
        For labeling only — the simulation always marks to MARKING_HORIZON.
    slippage_bps : float
        Per-side slippage in basis points. Applied on each entry and exit.
    cutoff_days : int, optional
        If set, only simulate the trailing N days. Default: all available.
    entry_thresh, exit_thresh : float
        Score thresholds (default 60 / 35 per spec §2).
    max_concurrent : int
        Cap on simultaneous open positions (default 15 per spec §3).
    """
    scores_by_bucket, r30_by_bucket = _pull_fires(
        model_name, horizon, cutoff_days, cutoff_end)

    buckets = sorted(scores_by_bucket.keys())
    if not buckets:
        return _empty_result(model_name, horizon, slippage_bps)

    positions:  dict[str, Position]   = {}
    trade_log:  list[TradeEvent]      = []
    per_ticker_pnl: dict              = defaultdict(lambda: {
        "n_trades": 0, "cum_pnl": 0.0, "n_long": 0, "n_short": 0
    })
    hold_durations: list[float]       = []   # minutes per round-trip
    round_trip_pnls: list[float]      = []   # P&L per round-trip (for hit rate)
    open_trip_pnl:  dict              = {}   # ticker → running P&L for current trip
    open_trip_entry: dict             = {}   # ticker → entry bucket_time
    daily_pnl_gross: dict             = defaultdict(float)   # pure market P&L (no costs)
    daily_slippage:  dict             = defaultdict(float)   # slippage costs by date
    slippage_cost_bps_dec = slippage_bps / 10000.0

    prev_bucket: Optional[datetime] = None

    for fire_time in buckets:
        scores_this_fire = scores_by_bucket[fire_time]

        # ─── (1) Mark existing positions to market — using PREVIOUS bucket's
        # r30 to avoid look-ahead bias. r30 at prev_bucket covers
        # prev → fire_time (already-realized history). r30 at fire_time
        # itself covers fire_time → fire_time+30min (future at decision time)
        # and is applied at the NEXT iteration.
        if prev_bucket is not None:
            r30_prev = r30_by_bucket.get(prev_bucket, {})
            for ticker, pos in list(positions.items()):
                r30 = r30_prev.get(ticker)
                if r30 is None:
                    continue
                pnl_contrib = pos.weight * pos.direction * (r30 / 100.0)
                daily_pnl_gross[fire_time.date()] += pnl_contrib
                open_trip_pnl[ticker] = open_trip_pnl.get(ticker, 0.0) + pnl_contrib

        # ─── (2) Apply exit rules ───
        # Existing positions where the latest |score| has dropped below the
        # exit threshold. (If no score reported this fire for that ticker,
        # we hold — no action.)
        for ticker, pos in list(positions.items()):
            score = scores_this_fire.get(ticker)
            if score is None:
                continue
            if abs(score) < exit_thresh:
                # Slippage on exit (tracked separately from market P&L)
                daily_slippage[fire_time.date()] += pos.weight * slippage_cost_bps_dec
                # Bookkeeping
                round_trip_pnls.append(open_trip_pnl.pop(ticker, 0.0))
                entry_ts = open_trip_entry.pop(ticker, fire_time)
                hold_durations.append((fire_time - entry_ts).total_seconds() / 60.0)
                trade_log.append(TradeEvent(
                    fire_time=fire_time, ticker=ticker, event="EXIT",
                    direction=pos.direction, score=score, weight=pos.weight))
                per_ticker_pnl[ticker]["n_trades"] += 1
                per_ticker_pnl[ticker]["cum_pnl"] += round_trip_pnls[-1]
                positions.pop(ticker)

        # ─── (3) Apply entry rules ───
        candidates = [
            (t, s) for t, s in scores_this_fire.items()
            if abs(s) >= entry_thresh and t not in positions
        ]
        candidates.sort(key=lambda x: -abs(x[1]))
        for ticker, score in candidates:
            if len(positions) >= max_concurrent:
                break
            direction = 1 if score > 0 else -1
            # Provisional weight — recomputed below after we know N
            positions[ticker] = Position(
                ticker=ticker, direction=direction, entry_time=fire_time,
                entry_score=score, weight=0.0)
            open_trip_pnl[ticker] = 0.0
            open_trip_entry[ticker] = fire_time
            # Stats
            if direction > 0: per_ticker_pnl[ticker]["n_long"] += 1
            else:             per_ticker_pnl[ticker]["n_short"] += 1
            trade_log.append(TradeEvent(
                fire_time=fire_time, ticker=ticker, event="ENTRY",
                direction=direction, score=score, weight=0.0))  # weight filled below

        # ─── (4) Rebalance — equal-weight 1/N across active positions ───
        n_active = len(positions)
        if n_active > 0:
            w = 1.0 / n_active
            for pos in positions.values():
                pos.weight = w
            # Fix the last few trade_log entries with correct weight
            # (entries from step 3 had weight=0)
            for ev in reversed(trade_log):
                if ev.fire_time == fire_time and ev.event == "ENTRY":
                    ev.weight = w
                    # Slippage on entry (tracked separately from market P&L)
                    daily_slippage[fire_time.date()] += w * slippage_cost_bps_dec
                else:
                    break

        prev_bucket = fire_time

    # ─── Force-close any positions still open at end-of-simulation ───
    # Apply final mark using r30 at the last processed bucket (covers
    # last_bucket → last_bucket+30min, even though we don't have any
    # later fires to confirm signal decay). This finalizes round-trip
    # P&L so the hit rate is computed over all positions, not just the
    # subset that organically exited.
    n_open_at_end = len(positions)
    if positions and prev_bucket is not None:
        last_r30 = r30_by_bucket.get(prev_bucket, {})
        for ticker, pos in list(positions.items()):
            r30 = last_r30.get(ticker)
            if r30 is not None:
                pnl_contrib = pos.weight * pos.direction * (r30 / 100.0)
                open_trip_pnl[ticker] = open_trip_pnl.get(ticker, 0.0) + pnl_contrib
                # Attribute the final mark to the last fire's date
                daily_pnl_gross[prev_bucket.date()] += pnl_contrib
            # Slippage on the forced exit (tracked separately from market P&L)
            daily_slippage[prev_bucket.date()] += pos.weight * slippage_cost_bps_dec
            # Record as a completed round-trip
            round_trip_pnls.append(open_trip_pnl.pop(ticker, 0.0))
            entry_ts = open_trip_entry.pop(ticker, prev_bucket)
            hold_durations.append((prev_bucket - entry_ts).total_seconds() / 60.0)
            trade_log.append(TradeEvent(
                fire_time=prev_bucket, ticker=ticker, event="EXIT_FORCED",
                direction=pos.direction, score=0.0, weight=pos.weight))
            per_ticker_pnl[ticker]["n_trades"] += 1
            per_ticker_pnl[ticker]["cum_pnl"] += round_trip_pnls[-1]

    # ─── Build equity curves ───
    # daily_pnl_gross is pure market P&L (no costs applied)
    # net curve subtracts slippage + platform fee from gross
    all_dates = sorted(set(daily_pnl_gross.keys()) | set(daily_slippage.keys()))
    daily_drag = (PLATFORM_FEE_BPS_YEAR / 10000.0) / TRADING_DAYS_PER_YEAR

    equity_gross: dict = {}
    equity_net:   dict = {}
    eg = 1.0; en = 1.0
    daily_returns_gross: dict = {}
    daily_returns_net:   dict = {}
    for d in all_dates:
        rg = daily_pnl_gross.get(d, 0.0)
        rn = rg - daily_slippage.get(d, 0.0) - daily_drag
        eg *= (1.0 + rg)
        en *= (1.0 + rn)
        equity_gross[d] = eg
        equity_net[d]   = en
        daily_returns_gross[d] = rg
        daily_returns_net[d]   = rn

    # ─── Summary statistics ───
    cum_gross = (eg - 1.0) if eg else 0.0
    cum_net   = (en - 1.0) if en else 0.0
    sharpe_net = _sharpe(list(daily_returns_net.values()))
    max_dd_net = _max_drawdown(list(equity_net.values()))
    hit_rate = (sum(1 for p in round_trip_pnls if p > 0)
                / len(round_trip_pnls)) if round_trip_pnls else 0.0
    avg_hold = (sum(hold_durations) / len(hold_durations)
                if hold_durations else 0.0)
    # Count COMPLETED round-trips by direction — pair each EXIT/EXIT_FORCED
    # event with its preceding ENTRY for the same ticker.
    n_long_completed = 0; n_short_completed = 0
    open_dir: dict = {}   # ticker -> direction of currently-open round-trip
    for ev in trade_log:
        if ev.event == "ENTRY":
            open_dir[ev.ticker] = ev.direction
        elif ev.event in ("EXIT", "EXIT_FORCED"):
            d = open_dir.pop(ev.ticker, ev.direction)
            if d > 0: n_long_completed += 1
            else:     n_short_completed += 1
    n_trades = n_long_completed + n_short_completed

    return SimResult(
        model_name=model_name, horizon=horizon, slippage_bps=slippage_bps,
        n_fires=len(buckets),
        n_trades=n_trades, n_open_at_end=n_open_at_end,
        n_long=n_long_completed, n_short=n_short_completed,
        daily_equity_gross=equity_gross, daily_equity_net=equity_net,
        daily_returns_gross=daily_returns_gross,
        daily_returns_net=daily_returns_net,
        cum_return_gross=cum_gross, cum_return_net=cum_net,
        sharpe_net=sharpe_net, max_dd_net=max_dd_net,
        hit_rate=hit_rate, avg_hold_minutes=avg_hold,
        trade_log=trade_log,
        per_ticker_contribution=dict(per_ticker_pnl),
    )


# ────────────────────────────────────────────────────────────────────────
# Stats helpers
# ────────────────────────────────────────────────────────────────────────
def _sharpe(daily_returns: list[float]) -> float:
    """Annualized Sharpe. Risk-free rate assumed 0 for relative comparison."""
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = var ** 0.5
    if std < 1e-9:
        return 0.0
    return (mean / std) * (TRADING_DAYS_PER_YEAR ** 0.5)


def _max_drawdown(equity_curve: list[float]) -> float:
    """Largest peak-to-trough drawdown as a negative fraction."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak: peak = v
        dd = (v / peak) - 1.0
        if dd < max_dd: max_dd = dd
    return max_dd


def _empty_result(model_name: str, horizon: str, slippage_bps: float) -> SimResult:
    return SimResult(
        model_name=model_name, horizon=horizon, slippage_bps=slippage_bps,
        n_fires=0, n_trades=0, n_open_at_end=0, n_long=0, n_short=0,
        daily_equity_gross={}, daily_equity_net={},
        daily_returns_gross={}, daily_returns_net={},
        cum_return_gross=0.0, cum_return_net=0.0,
        sharpe_net=0.0, max_dd_net=0.0,
        hit_rate=0.0, avg_hold_minutes=0.0,
        trade_log=[], per_ticker_contribution={},
    )


# ────────────────────────────────────────────────────────────────────────
# CLI smoke test (Day 1 unit test deliverable)
# ────────────────────────────────────────────────────────────────────────
def _print_summary(r: SimResult) -> None:
    print(f"\n  Model: {r.model_name}  |  Horizon: {r.horizon}  |  Slippage: {r.slippage_bps} bps/side")
    print(f"  Fires processed: {r.n_fires}")
    print(f"  Completed round-trips: {r.n_trades}  (long={r.n_long}, short={r.n_short})  "
          f"+ {r.n_open_at_end} force-closed at sim end")
    print(f"  Hit rate (round-trips ITM): {r.hit_rate * 100:.1f}%")
    print(f"  Avg hold: {r.avg_hold_minutes:.0f} min")
    print(f"  Cumulative return  GROSS: {r.cum_return_gross * 100:+.2f}%   NET: {r.cum_return_net * 100:+.2f}%")
    print(f"  Sharpe (net):       {r.sharpe_net:+.2f}")
    print(f"  Max DD (net):       {r.max_dd_net * 100:+.2f}%")
    if r.trade_log[:3]:
        print(f"  First trades:")
        for ev in r.trade_log[:3]:
            print(f"    [{ev.event}] {ev.fire_time}  {ev.ticker:6s}  "
                  f"dir={ev.direction:+d}  score={ev.score:+.1f}  w={ev.weight:.3f}")


if __name__ == "__main__":
    # Smoke test: run against an established entrant we know has data.
    # Use momentum_120_60min (long-running, plenty of fires) as the canary.
    # When meta_blend_60min has data we'll run that too.
    # Real signal names from the DB (`model_<stem>_score`). Pick a few that
    # span different families so we see varied behavior in the smoke test.
    smoke_models = [
        ("momentum_5bar",          "60min"),
        ("momentum_21bar",         "60min"),
        ("rsi_extreme_14",         "60min"),
        ("bollinger_pos_20",       "60min"),
        ("mean_rev_20",            "60min"),
    ]
    print("=" * 70)
    print("markout_eval.py — Day 1 smoke test")
    print("=" * 70)
    for name, hz in smoke_models:
        try:
            result = simulate(name, hz, slippage_bps=5.0, cutoff_days=30)
            _print_summary(result)
        except Exception as e:
            print(f"\n  {name}: FAILED — {type(e).__name__}: {e}")
    print("\n" + "=" * 70)
    print("Day 1 done.")
