# Markout Dashboard v32 — Build Record
**Date:** 2026-05-27
**Status:** Live at http://rcg-nixos:8080/markouts.html
**Supersedes:** v29 (table), v30 (table + scatter, never made it to canonical), v31 (card grid with placeholders, rolled back)

---

## What shipped

Replaces the v29 table dashboard with a card-grid + deep-linked detail view.
All Slack-agent v31 placeholders have been replaced with real backend values.

### Frontend
- src/markouts_v32.html — single page, two views (grid + detail), routed by ?model=<stem> URL param
- src/markouts_v32.css — mobile-first, color-tiered cards (hot / warm / cold / gray)
- src/markouts_v32.js — grid renderer, detail renderer, trade table with filter/sort, tooltip, deep-link routing

### Backend
- src/markout_eval.py:
  - New Trade dataclass (ticker, dir, entry/exit timestamps + scores + prices, hold mins, return_pct, exit_reason)
  - New _pull_prices() pulls live_price signal from Postgres in same RTH-filtered bucketing as fires
  - simulate() now stamps entry/exit prices onto each round-trip and emits SimResult.trades
- src/markout_eval_publish.py:
  - Imports descriptions from src/model_descriptions.py
  - New helpers: compute_streaks, rolling_sharpe, serialize_trade
  - Per-model JSON now emits: description, all_trades, current_streak, max_win_streak, max_loss_streak, best_trade_return, worst_trade_return, max_gain_dollars, max_loss_dollars, rolling_sharpe.{5d,10d,30d}, last_fire_ts
  - Source files for deploy pointed at markouts_v32.* (was markouts_v29.*)

### What the user asked for vs what shipped
| Ask                                            | Status |
|------------------------------------------------|--------|
| Restore v31 card-grid scaffolding              | ✅ |
| Per-trade table with signals + prices + hold + capital + ticker | ✅ real |
| Detail page in second window (not back-stuck)  | ✅ deep-link ?model= opened via target=_blank |
| Show metric timeframes on cards/detail         | ✅ Return · 90d, Sharpe · 90d, Max DD · 90d |
| Best return / worst return per model           | ✅ on card + detail |
| Max DD per model                               | ✅ on card + detail |
| Real win/loss streak                           | ✅ from chronological trade sequence |
| Real rolling Sharpe 5/10/30d                   | ✅ trailing-window from daily_returns_net |
| Real last-fire timestamp                       | ✅ from max(trade.exit_time) |
| Model methodology + entry/exit on hover        | ✅ tooltip + detail page banner |
| No CSV export                                  | ✅ not implemented |
| Stop loss                                      | Deferred per user — visuals first |

---

## Known limitations
- **Model description coverage:** model_descriptions.py has ~25 entries; many actual model stems (e.g. momentum_5bar, mean_rev_20) don't match the dict keys and fall back to generic description. Update the dict to match real stems for full coverage.
- **Rolling Sharpe collapses on short windows:** With only ~3 trading days in the live data, 5d/10d/30d Sharpe values are identical (computed on what's available). Will diverge as more daily history accumulates.
- **Prices missing on a small fraction of trades:** live_price signal isn't captured for every ticker at every fire. Affected rows show — for entry/exit price and return_pct = 0. Estimated 5-10% gap based on smoke test.
- **NOTIONAL_PER_BOOK = $100,000** is a publisher constant for translating weight × return → capital + $P&L. Sim still works in pure return-space; the dollar number is purely for display.

---

## Rollback path
The publisher's source-file constants control which files get deployed. To roll back to v29:
```python
# in src/markout_eval_publish.py:
HTML_SOURCE = Path('/home/nixos/Prod/V1/src/markouts_v29.html')
CSS_SOURCE  = Path('/home/nixos/Prod/V1/src/markouts_v29.css')
JS_SOURCE   = Path('/home/nixos/Prod/V1/src/markouts_v29.js')
```
Then re-run markout_eval_publish.py (or wait for the nightly 02:00 ET cron). v29 source files remain in src/ untouched.

The new Trade dataclass + trades field on SimResult are additive — v29 frontend ignores them, so the backend extension doesn't need to be rolled back even if the UI does.

---

## Compliance
Per docs/rcg_policy.md §17: internal dashboard UI changes are pass-through. No CCO escalation needed. No new external vendors / data sources added.

---

## GCS archive (added 2026-05-27)

Each publisher run now archives two artifacts to gs://rcg-prod-data, date-partitioned by the publisher's generated_at timestamp. Upload is best-effort — local outputs/markouts.json is always written first so the dashboard isn't gated on GCS health.

### Paths

```
gs://rcg-prod-data/markouts/year=YYYY/month=MM/day=DD/markouts_HHMMSS.json
    Full per-run snapshot. Same content the dashboard renders. ~800 KB.

gs://rcg-prod-data/markout_trades/year=YYYY/month=MM/day=DD/trades_HHMMSS.jsonl
    Flat per-trade ledger. One JSON object per line. ~500 KB / ~1000 rows
    per 90d run.
```

### Per-trade row schema

Each line in the JSONL contains:
```json
{
  model:          arima_20,
  family:         arima,
  horizon:        n/a,
  sim_run_at:     2026-05-27T20:06:46+00:00,
  lookback_days:  90,
  slippage_bps:   5.0,
  ticker:         LITE,
  direction:      long,
  entry_time:     2026-05-21T14:00:00+00:00,
  exit_time:      2026-05-21T14:30:00+00:00,
  entry_score:    100.0,
  exit_score:     34.0,
  entry_price:    941.05,
  exit_price:     938.205,
  hold_minutes:   30.0,
  return_pct:     -0.003023,
  capital_used:   100000.0,
  return_dollars: -302.32,
  exit_reason:    score_decay
}
```

### Query examples (duckdb)

```bash
# All arima_20 trades over the entire archive
duckdb -c "
  SELECT exit_time, ticker, direction, return_pct
  FROM read_json_auto('gs://rcg-prod-data/markout_trades/**/*.jsonl')
  WHERE model = 'arima_20'
  ORDER BY exit_time DESC LIMIT 50
"

# Per-model best/worst day of trade returns
duckdb -c "
  SELECT model, date_trunc('day', exit_time) AS d,
         SUM(return_dollars) AS pnl_day
  FROM read_json_auto('gs://rcg-prod-data/markout_trades/**/*.jsonl')
  GROUP BY model, d
  ORDER BY pnl_day DESC LIMIT 20
"
```

### Caveats
- **History begins from first archived run** (today). Trades that closed prior to 2026-05-27 still exist in Postgres signals + in re-runnable form via pg_dump, but no historical snapshots exist in GCS.
- **Same trade may appear in multiple JSONL files.** Each run's sim covers the trailing 90 days; consecutive runs share a sliding window. De-dup on (model, ticker, entry_time) if you want a trades-once view.
- **sim_run_at** is the publisher's start timestamp — use it to distinguish multiple runs same day or to find the freshest version of a trade.
- **No partition pruning of old files yet.** GCS lifecycle policy could be added later (e.g. 1-year retention on markouts/, indefinite on markout_trades/).

### Reverting
Comment out out['gcs_archive'] = archive_to_gcs(...) in markout_eval_publish.py. Publisher continues to write the local JSON — GCS uploads stop. Existing archived objects remain.
