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
from datetime import datetime, timezone, date, time
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import psycopg


# ────────────────────────────────────────────────────────────────────────
# Trading-hours filter (NYSE regular session: 9:30 ET → 16:00 ET, Mon-Fri)
# Uses zoneinfo so DST transitions are handled correctly (EST in winter,
# EDT in summer). Holiday calendar deferred to v2 — for now any weekday
# bucket within 9:30-16:00 ET is considered tradeable.
# ────────────────────────────────────────────────────────────────────────
ET = ZoneInfo("America/New_York")
RTH_OPEN  = time(9, 30)
RTH_CLOSE = time(16, 0)


def is_rth(dt_utc: datetime) -> bool:
    """True if dt_utc falls within 9:30-16:00 ET on a weekday."""
    dt_et = dt_utc.astimezone(ET)
    if dt_et.weekday() >= 5:   # Sat=5, Sun=6
        return False
    t = dt_et.time()
    return RTH_OPEN <= t < RTH_CLOSE


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
    fires_held:    int   = 0           # incremented each fire the position survives


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
    avg_hold_trading_minutes: float     # mean fires_held × 30 across all round-trips
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
            # Drop anything outside 9:30 ET → 16:00 ET on weekdays. The
            # tournament shouldn't be firing outside RTH anyway, but
            # we filter defensively so the simulation never marks or
            # trades on pre-market / after-hours / weekend data.
            if not is_rth(bucket_time):
                continue
            v = float(val)
            if sname == score_signal:
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
    hold_durations: list[float]       = []   # wall-clock minutes per round-trip (incl. overnight)
    hold_trading_mins: list[float]    = []   # trading minutes per round-trip (fires_held × 30)
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
                pos.fires_held += 1   # one more fire of holding (RTH-only)
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
                hold_trading_mins.append(pos.fires_held * 30.0)
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
            hold_trading_mins.append(pos.fires_held * 30.0)
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
    avg_hold_trading = (sum(hold_trading_mins) / len(hold_trading_mins)
                        if hold_trading_mins else 0.0)
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
        avg_hold_trading_minutes=avg_hold_trading,
        trade_log=trade_log,
        per_ticker_contribution=dict(per_ticker_pnl),
    )


# ────────────────────────────────────────────────────────────────────────
# Day 2 — Supporting-panel data (calibration, rolling IC, daily-return
# series for cross-model correlation matrix)
# ────────────────────────────────────────────────────────────────────────
def compute_calibration(
    model_name:   str,
    horizon:      str,
    cutoff_days:  Optional[int] = None,
    cutoff_end:   Optional[datetime] = None,
) -> list[dict]:
    """
    For each score bucket, compute avg realized return + sample size.

    Buckets are signed: positive side covers longs (60-70, 70-80, 80-90,
    90-100) and negative side covers shorts (-70 to -60, -80 to -70, etc.).
    Sub-threshold bins (35-60 / -60 to -35) and dead-zone (-35 to +35) are
    also included for visibility into how the model behaves below the
    trading thresholds.

    Buckets are pulled from the same fires the trade-simulation uses
    (RTH-filtered), so the calibration plot reflects the same population
    the strategy was trained on.

    Returns: list of {bucket_label, lo, hi, n, avg_return_pct, hit_rate}
             ordered low → high.
    """
    scores_by_bucket, r30_by_bucket = _pull_fires(
        model_name, horizon, cutoff_days, cutoff_end)
    # Collect (score, r30) pairs paired by (bucket, ticker)
    pairs: list[tuple[float, float]] = []
    for bt, scores in scores_by_bucket.items():
        r30s = r30_by_bucket.get(bt, {})
        for ticker, s in scores.items():
            r = r30s.get(ticker)
            if r is not None:
                pairs.append((s, r))
    if not pairs:
        return []

    # Bucket edges — symmetric around 0
    edges = [-100, -90, -80, -70, -60, -35, 0, 35, 60, 70, 80, 90, 100.01]
    labels = ["-90/-100", "-80/-90", "-70/-80", "-60/-70",
              "-35/-60",  "-35/0",   "0/35",    "35/60",
              "60/70",    "70/80",   "80/90",   "90/100"]
    buckets: list[dict] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i+1]
        in_bucket = [(s, r) for s, r in pairs if lo <= s < hi]
        if not in_bucket:
            buckets.append({
                "bucket": labels[i], "lo": lo, "hi": hi,
                "n": 0, "avg_return_pct": None, "hit_rate": None,
            })
            continue
        n = len(in_bucket)
        avg_r = sum(r for _, r in in_bucket) / n
        # Hit rate: among observations with a NON-ZERO return, fraction
        # where sign(score) == sign(return). Zero-return observations are
        # excluded from the denominator — they're "no movement", neither
        # hit nor miss. This matches the convention models_leaderboard
        # uses (n_strong gate on signal magnitude, signed product on
        # return sign).
        non_zero = [(s, r) for s, r in in_bucket if r != 0]
        if non_zero:
            hits = sum(1 for s, r in non_zero if (s > 0) == (r > 0))
            hit_rate = hits / len(non_zero)
        else:
            hit_rate = None
        buckets.append({
            "bucket": labels[i], "lo": lo, "hi": hi,
            "n": n, "n_nonzero_returns": len(non_zero),
            "avg_return_pct": round(avg_r, 4),
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        })
    return buckets


def compute_rolling_ic(
    model_name:   str,
    horizon:      str,
    window_days:  int = 30,
    cutoff_days:  Optional[int] = None,
    cutoff_end:   Optional[datetime] = None,
) -> list[dict]:
    """
    Rolling directional IC over a `window_days`-trailing window, sampled
    at the end of each trading day.

    Directional IC = mean(sign(score) × sign(realized_return)) — same as
    models_leaderboard.py uses, so the value is comparable to the
    leaderboard's overall IC.

    Returns: list of {date, ic_dir, n} ordered by date.
    """
    scores_by_bucket, r30_by_bucket = _pull_fires(
        model_name, horizon, cutoff_days, cutoff_end)

    # Flatten into a timestamp-sorted list of (date, score, r30) triples
    triples: list[tuple[date, float, float]] = []
    for bt, scores in scores_by_bucket.items():
        r30s = r30_by_bucket.get(bt, {})
        for ticker, s in scores.items():
            r = r30s.get(ticker)
            if r is not None:
                # Use the ET date so windowing is RTH-aware
                triples.append((bt.astimezone(ET).date(), s, r))
    if not triples:
        return []
    triples.sort(key=lambda t: t[0])

    # For each unique trading day, compute trailing-window IC
    unique_dates = sorted({t[0] for t in triples})
    rolling: list[dict] = []
    from collections import deque
    # Build a deque of (date, score, r) we walk through
    triple_idx = 0
    window: list[tuple[date, float, float]] = []
    for d in unique_dates:
        # Add all triples up to and including d
        while triple_idx < len(triples) and triples[triple_idx][0] <= d:
            window.append(triples[triple_idx])
            triple_idx += 1
        # Trim window to N trailing trading days
        cutoff_date = d
        # Find trading days within window: collect distinct dates ≤ d, take last N
        seen_dates: list[date] = []
        for td, _, _ in reversed(window):
            if not seen_dates or seen_dates[-1] != td:
                seen_dates.append(td)
            if len(seen_dates) >= window_days:
                break
        if seen_dates:
            min_date = seen_dates[-1]
            window = [t for t in window if t[0] >= min_date]
        # Compute IC on the current window
        if len(window) < 5:
            rolling.append({"date": str(d), "ic_dir": None, "n": len(window)})
            continue
        ic_terms = [
            (1 if s > 0 else -1 if s < 0 else 0)
            * (1 if r > 0 else -1 if r < 0 else 0)
            for _, s, r in window
        ]
        ic = sum(ic_terms) / len(ic_terms)
        rolling.append({"date": str(d), "ic_dir": round(ic, 4), "n": len(window)})
    return rolling


def compute_model_correlation(
    sim_results: list[SimResult],
) -> dict:
    """
    Compute pairwise Pearson correlation of daily NET returns across a
    set of already-simulated models.

    sim_results: list of SimResult — typically the family champions only
                 (passed in by the publisher script).
    Returns: {labels: [...], matrix: [[1.0, 0.42, ...], ...]}
    """
    if len(sim_results) < 2:
        return {"labels": [r.model_name for r in sim_results], "matrix": []}

    labels = [r.model_name for r in sim_results]
    # Union of all dates across all results
    all_dates = sorted({d for r in sim_results for d in r.daily_returns_net})
    # Build matrix: row per model, col per date
    n = len(sim_results)
    cols = [[r.daily_returns_net.get(d, 0.0) for d in all_dates]
            for r in sim_results]

    def pearson(x: list[float], y: list[float]) -> float:
        if len(x) < 2: return 0.0
        mx = sum(x) / len(x); my = sum(y) / len(y)
        sx2 = sum((a - mx) ** 2 for a in x)
        sy2 = sum((b - my) ** 2 for b in y)
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        denom = (sx2 * sy2) ** 0.5
        return sxy / denom if denom > 1e-9 else 0.0

    matrix = [[round(pearson(cols[i], cols[j]), 3) for j in range(n)]
              for i in range(n)]
    return {"labels": labels, "matrix": matrix, "n_dates": len(all_dates)}


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
        hit_rate=0.0, avg_hold_minutes=0.0, avg_hold_trading_minutes=0.0,
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
    print(f"  Avg hold (trading-min): {r.avg_hold_trading_minutes:.0f}  ({r.avg_hold_trading_minutes/60:.1f}h)")
    print(f"  Cumulative return  GROSS: {r.cum_return_gross * 100:+.2f}%   NET: {r.cum_return_net * 100:+.2f}%")
    print(f"  Sharpe (net):       {r.sharpe_net:+.2f}")
    print(f"  Max DD (net):       {r.max_dd_net * 100:+.2f}%")
    if r.trade_log[:3]:
        print(f"  First trades:")
        for ev in r.trade_log[:3]:
            print(f"    [{ev.event}] {ev.fire_time}  {ev.ticker:6s}  "
                  f"dir={ev.direction:+d}  score={ev.score:+.1f}  w={ev.weight:.3f}")


if __name__ == "__main__":
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
    print("markout_eval.py — Day 1+2 smoke test")
    print("=" * 70)
    sim_results: list[SimResult] = []
    for name, hz in smoke_models:
        try:
            result = simulate(name, hz, slippage_bps=5.0, cutoff_days=30)
            _print_summary(result)
            sim_results.append(result)
        except Exception as e:
            print(f"\n  {name}: FAILED — {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("Day 2 — supporting-panel data")
    print("=" * 70)

    # 1. Calibration buckets for a non-trivial model
    print("\n[1/3] Calibration (bollinger_pos_20):")
    buckets = compute_calibration("bollinger_pos_20", "60min", cutoff_days=30)
    print(f"  {'bucket':>10s}  {'n':>5s}  {'avg_return_%':>12s}  {'hit_rate':>9s}")
    for b in buckets:
        if b["n"] == 0:
            print(f"  {b['bucket']:>10s}  {b['n']:>5d}  {'—':>12s}  {'—':>9s}")
        else:
            print(f"  {b['bucket']:>10s}  {b['n']:>5d}  "
                  f"{b['avg_return_pct']:>+12.4f}  {b['hit_rate']*100:>8.1f}%")

    # 2. Rolling 30d IC time-series
    print("\n[2/3] Rolling 30d IC (bollinger_pos_20):")
    rolling = compute_rolling_ic("bollinger_pos_20", "60min",
                                  window_days=30, cutoff_days=30)
    for r in rolling[-7:]:    # last week of points
        if r["ic_dir"] is None:
            print(f"  {r['date']}  n={r['n']:>4d}  ic_dir=  —")
        else:
            print(f"  {r['date']}  n={r['n']:>4d}  ic_dir={r['ic_dir']:+.4f}")

    # 3. Model correlation matrix across our smoke models
    print("\n[3/3] Model-vs-model correlation (NET daily returns):")
    if len(sim_results) >= 2:
        corr = compute_model_correlation([r for r in sim_results if r.n_trades > 0])
        labels = corr["labels"]
        print(f"  ({len(labels)} models, {corr.get('n_dates', 0)} dates)")
        # Print compact triangle
        print(f"  {'':25s}", end="");
        for j, lbl in enumerate(labels):
            print(f" {j:>5d}", end="")
        print()
        for i, lbl in enumerate(labels):
            print(f"  {i}: {lbl:<22s}", end="")
            for j in range(len(labels)):
                print(f" {corr['matrix'][i][j]:>+5.2f}", end="")
            print()

    print("\n" + "=" * 70)
    print("Day 2 done.")
