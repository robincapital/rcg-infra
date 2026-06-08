# Filtered Models — Re-validation Summary
**Last run:** 2026-06-07 13:00 UTC
**Cadence:** weekly Sunday 09:00 ET via rcg-filtered-models-revalidate.timer

Watchdog for the *_filtered model variants. Each run does a 60/40 train/test
walk-forward on the full available capture history and reports test-set IC
with vs without the filter.

**Verdict:** `stable` (lift >= +0.03 abs), `marginal` (lift > 0 but < +0.03),
`DECAY` (lift <= 0 — filter no longer adds edge).

## This run

| Model | Filter | Train d | Test d | Test base IC | Test filt IC | Lift | Verdict |
|---|---|---|---|---|---|---|---|
| `arima_20` | good_hours | 7 | 5 | +0.028 | +0.094 | +0.067 | ✅ stable |

## Historical log

Append-only log: `outputs/filtered_revalidation_log.jsonl`

Read with duckdb to plot IC drift over time:
```sql
SELECT run_ts, model, test_lift, verdict
FROM read_json_auto('outputs/filtered_revalidation_log.jsonl')
ORDER BY run_ts DESC, model;
```