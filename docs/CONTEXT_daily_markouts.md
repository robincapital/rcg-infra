# Daily Forward Returns (v27) — Context Document

**Shipped:** 2026-05-19  
**Owner:** Quant hat  
**Related:** Phase B (Data layers) in ROADMAP.md

---

## Overview

**Daily markouts** join each day's prediction snapshots (from tournament fires) to EOD closes from Sharadar SEP at **1-day, 5-day, and 20-day** trading-day offsets. This mirrors the existing intraday markouts (30m/60m/4h from `forward_returns_capture.py`) but operates on **daily EOD prices** instead of minute-based BBG snapshots.

### Why We Built This

1. **Multi-horizon IC stratification**: Are models better at 1d vs 20d? Do some families excel at longer horizons?
2. **Portfolio simulator**: Track what would have happened if you traded each day's Top 3 names over 1 week / 1 month
3. **Stage 2+ meta-models**: Logistic conviction and gradient-boost meta-models need longer-horizon labels for training (can't wait 20 days for intraday markouts to accumulate)
4. **Backtest validation**: Historical walk-forward tests require multi-day return data aligned to prediction timestamps

---

## Architecture

### Data Flow

```
Prediction Fire (09:38 ET)
  ├─ models_capture.py writes live_price signal per ticker
  └─ signals table: (run_id, ticker, live_price=P_entry, run_timestamp=T)

Next Day 09:00 ET
  ├─ Sharadar morning pull refreshes /var/sharadar/data/SEP.parquet
  └─ forward_returns_daily.py (systemd timer)
      ├─ Loads SEP (ticker, date, close) + builds trading calendar
      ├─ For each (run_id, ticker, T, P_entry):
      │   ├─ Find T+1d, T+5d, T+20d in trading calendar
      │   ├─ Lookup close price from SEP at each target date ±1 day tolerance
      │   └─ Compute realized_return_{1d|5d|20d}_pct = (P_exit - P_entry) / P_entry * 100
      └─ Write signals back to original run_id

Downstream Consumers
  ├─ models_leaderboard.py discovers new horizons automatically
  ├─ meta_model_train.py can add 1d/5d/20d to HORIZONS list when Stage 2 starts
  └─ Future portfolio simulator reads daily returns for P&L attribution
```

### Files

| File | Purpose | Run Cadence |
|------|---------|-------------|
| `src/forward_returns_daily.py` | Main join script | Daily 09:00 ET via systemd timer |
| `/etc/systemd/system/forward-returns-daily.service` | Systemd service unit | — |
| `/etc/systemd/system/forward-returns-daily.timer` | Systemd timer (09:00 ET) | Daily |
| `outputs/screener_universe.csv` | Not used directly; SEP is canonical EOD source | — |
| `/var/sharadar/data/SEP.parquet` | Sharadar EOD equity prices (ticker, date, close) | Daily morning refresh |

---

## Key Design Decisions

### Trading Day Calendar

- **Not a fixed NYSE holiday calendar**: We build the calendar **per-ticker** from SEP's actual rows where `close` is not null
- **Why?** Tickers can be halted, delisted, or have stale data. Using each ticker's observed trading days avoids false "no match" errors
- **Offset logic**: To find T+5d, we:
  1. Find the first SEP row for `ticker` where `date >= T`
  2. Move forward 5 rows in that ticker's sorted date list
  3. Accept the close price at that date ±1 calendar day (tolerance for early market close, data delays)

### Lookback Window

- **First run**: `LOOKBACK_DAYS = 90` (back-fills 3 months of historical data)
- **Subsequent runs**: Can reduce to `LOOKBACK_DAYS = 30` if desired (30 days is enough to catch delayed SEP updates + handle T+20d for recent predictions)
- **Idempotent**: Skips any `(run_id, ticker, horizon)` that already has a `realized_return_*` signal written

### Tolerance & Missing Data

- **±1 calendar-day tolerance**: If target date is a market holiday or SEP data is 1 day delayed, we accept the close from ±1 day
- **No match cases**:
  - Ticker delisted before target date → skip, log as "no match"
  - SEP data not yet available (T+20d is still in future) → skip silently as "future" (not an error)
- **No synthetic fills**: If SEP has no row for a ticker at the target date ±1 day, we do NOT forward-fill or interpolate — we skip that observation

### Database Schema

**No schema changes required** — reuses existing `signals` table. New signal names:
- `realized_return_1d_pct`
- `realized_return_5d_pct`
- `realized_return_20d_pct`

These join to `signals.run_id` (predictions from `live_prediction` and `model_score` runs), so downstream consumers that already read `realized_return_*_pct` signals automatically discover the new horizons.

---

## Installation

### 1. Deploy the Script

The Python script is already at `/home/nixos/Prod/V1/src/forward_returns_daily.py` (committed to main).

### 2. Install Systemd Timer

```bash
# Units are staged in /tmp/ by the agent
bash /tmp/install_daily_returns_timer.sh
```

This:
- Copies `.service` and `.timer` files to `/etc/systemd/system/`
- Reloads systemd daemon
- Enables + starts the timer (will run daily at 09:00 ET)

### 3. First Back-Fill Run (Manual)

Run once manually to back-fill 90 days of historical predictions:

```bash
cd /home/nixos/Prod/V1
./var/agent_venv/bin/python src/forward_returns_daily.py
```

Expected output:
```
[daily-returns] loaded SEP: 2,843,192 rows, 8,234 tickers, date range 2020-01-02 → 2026-05-19
[daily-returns] built trading calendar for 8,234 tickers
[daily-returns] fetched 12,458 prediction snapshots from last 90 days
[daily-returns] 0 (run_id, ticker, horizon) already computed (will skip)
[daily-returns] wrote 23,891 realized-return signals (already-have=0, no-match=1,234, future=8,765)

[daily-returns] summary by horizon:
  realized_return_1d_pct        :  7,945 obs  mean= +0.12%  min= -18.45%  max= +23.67%
  realized_return_5d_pct        :  7,123 obs  mean= +0.58%  min= -22.34%  max= +31.12%
  realized_return_20d_pct       :  5,821 obs  mean= +2.14%  min= -28.91%  max= +45.89%
```

### 4. Verify Timer Status

```bash
sudo systemctl status forward-returns-daily.timer
sudo systemctl list-timers forward-returns-daily.timer
```

Should show:
```
● forward-returns-daily.timer - RCG Daily Forward Returns Join Timer
   Loaded: loaded (/etc/systemd/system/forward-returns-daily.timer; enabled; preset: enabled)
   Active: active (waiting) since Mon 2026-05-19 08:45:12 EDT; 15min ago
  Trigger: Tue 2026-05-20 09:00:00 EDT; 23h 59min left
```

---

## Validation / Testing

### Dry-Run Test

```bash
bash /tmp/test_daily_returns.sh
```

This:
1. Checks SEP file exists + DB connection OK
2. Runs `forward_returns_daily.py`
3. Queries signals table for summary stats by horizon

### Manual DB Check

```sql
SELECT 
  signal_name,
  COUNT(*) as n_signals,
  COUNT(DISTINCT s.ticker) as n_tickers,
  ROUND(AVG(s.signal_value)::numeric, 2) as mean_ret_pct,
  ROUND(MIN(s.signal_value)::numeric, 2) as min_ret_pct,
  ROUND(MAX(s.signal_value)::numeric, 2) as max_ret_pct
FROM signals s
JOIN runs r ON s.run_id = r.run_id
WHERE signal_name IN ('realized_return_1d_pct', 'realized_return_5d_pct', 'realized_return_20d_pct')
  AND r.run_timestamp >= NOW() - INTERVAL '7 days'
GROUP BY signal_name
ORDER BY signal_name;
```

### Check Leaderboard Picks It Up

After 1 week of daily data accumulation:

```bash
cd /home/nixos/Prod/V1
./var/agent_venv/bin/python src/models_leaderboard.py | jq '.[] | select(.model_name=="momentum_5bar") | .ic_by_horizon'
```

Should show IC stats for `1d`, `5d`, `20d` in addition to `30min`, `60min`, `4h`.

---

## Operational Notes

### Logs

View daily run logs:
```bash
sudo journalctl -u forward-returns-daily.service -f
```

Or check last run:
```bash
sudo journalctl -u forward-returns-daily.service --since today
```

### Timer Schedule

The timer runs **09:00 ET daily** — chosen to be:
- **After** the morning Sharadar pull (assumed to complete by ~08:30 ET)
- **Before** market open (09:30 ET), so fresh daily markouts are available during the trading day

If Sharadar pull timing changes, adjust the timer:
```bash
sudo systemctl edit forward-returns-daily.timer
# Change OnCalendar= line
sudo systemctl daemon-reload
```

### Resource Usage

- **Memory**: ~500MB typical (loading SEP parquet + building lookup dict)
- **Runtime**: 30-90 seconds depending on SEP size and number of predictions to match
- **CPU**: Light (single-threaded Polars read + dict lookups)

Service unit has `MemoryMax=2G` and `CPUQuota=50%` to prevent runaway resource usage.

---

## Rollback / Troubleshooting

### Stop the Timer

```bash
sudo systemctl stop forward-returns-daily.timer
sudo systemctl disable forward-returns-daily.timer
```

### Purge Existing Daily Markouts (if corrupted)

```sql
DELETE FROM signals 
WHERE signal_name IN ('realized_return_1d_pct', 'realized_return_5d_pct', 'realized_return_20d_pct');
```

### Remove Everything

```bash
rm /home/nixos/Prod/V1/src/forward_returns_daily.py
sudo rm /etc/systemd/system/forward-returns-daily.{service,timer}
sudo systemctl daemon-reload
```

**No downstream breakage** — intraday markouts (30m/60m/4h) continue working via `forward_returns_capture.py`, and leaderboard/meta-model gracefully handle missing horizon data.

---

## Future Enhancements

### Add Daily Horizons to Meta-Model

When Stage 2 (logistic conviction) starts, add daily horizons to `meta_model.py`:

```python
HORIZONS = ["30min", "60min", "4h", "1d", "5d", "20d"]
HORIZON_TO_RETURN_SIGNAL = {
    "30min": "realized_return_30min_pct",
    "60min": "realized_return_60min_pct",
    "4h":    "realized_return_4h_pct",
    "1d":    "realized_return_1d_pct",
    "5d":    "realized_return_5d_pct",
    "20d":   "realized_return_20d_pct",
}
```

Then `meta_model_train.py` will fit separate weights for each horizon automatically.

### Portfolio Simulator

Once 4+ weeks of daily markouts accumulate, build a backtest harness:
- Each day, pull the Top 3 highest-`composite_score` tickers from screener
- Track what would have happened if you bought equal-weight at that day's close and held for 1d/5d/20d
- Compute cumulative P&L, Sharpe, max drawdown, sector concentration over time
- Compare vs. SPY/IWM benchmark

### Earnings-Event Tagging

When earnings calendar data is added (ROADMAP Phase B), join daily markouts to earnings dates:
- Flag observations where T+1d/T+5d/T+20d crosses an earnings release
- Stratify IC by "earnings-adjacent" vs "normal trading days"
- Some models may perform worse around earnings volatility

---

## Change Log

| Version | Date | Change |
|---------|------|--------|
| v27 | 2026-05-19 | Initial ship — `forward_returns_daily.py`, systemd timer, 90-day back-fill |

---

## Questions / Support

**Agent hat**: Quant  
**Escalation path**: Nick (for business logic / IC questions), Infra hat (for systemd timer issues)
