# Bloomberg Expansion — v1 spec is SUPERSEDED

The original `bloomberg_expansion_spec.md` (v1, 2026-05-28 morning) was scoped at 500 tickers + full backend persistence + multi-month rollout.

After the MM reviewed Bloomberg's DAPI terms-of-use document (provided 2026-05-28 mid-morning), scope was reduced and a known-non-compliance posture was logged. See:

- `docs/bbg_dapi_override.md` — MM override of the DAPI compliance finding
- `docs/bloomberg_expansion_spec.md` — original v1, retained for reference but **superseded**

## Active reduced scope (2026-05-28)

| Element | v1 | v2 (active) |
|---|---|---|
| Universe size | 500 | **350** |
| Persistence | Full / blanket | **Trigger-only** (only when model fires) |
| GCS archive | New paths planned | **No new BBG-derived GCS paths** |
| Display tick | 1-min snapshot | **1-min snapshot** (unchanged) |
| Existing capture (predictions_capture, screener) | Untouched | **Convert to trigger-only over time** (Phase C, deferred) |

## What shipped today (Phase A + B)

- `src/bloomberg_stream.py` (Windows) — long-running BLPAPI SubscriptionSession, 7 fields × current watchlist (~120 tickers), 60s snapshots to `C:\ProgramData\rcg\bloomberg_stream.json` + SCP to `/home/nixos/Prod/V1/outputs/bloomberg_stream.json`. Display-only path. No persistence.
- `run_bloomberg_stream.bat` + Windows Task Scheduler entry `RCG-Bloomberg-Stream` (logon-mode interactive, runs at logon).
- `src/trade.html` — patched to fetch the stream alongside the hourly puller, overlay live LAST_PRICE onto the existing per-ticker structure, refresh every 30s (was 60s), display green 🟢 LIVE indicator with stream-age + tick-count.

## What is deferred

- Phase C: trigger-only conversion of `predictions_capture.py` (cuts 12K writes/day → ~1K/day). Larger refactor; defer until current flow proves stable.
- Phase D: bump screener watchlist from 120 → 350. Wait for one clean cycle of streaming at 120 first to confirm no surprises before scaling.
- Phase 4 / 5 (alpha modeling on 1-min data, tick-level studies): deferred — these are downstream of having the data; we have the data now.
- Data-source migration: deferred per MM, contingent on alpha materializing.
