---
date: 2026-05-26
severity: P2
category: production-tool
component: market_sentiment_bbg.py
status: resolved
summary: Pinned/starred tickers had prices but no MR/sentiment signals (dual-watchlist file pattern, stale static source)
tags: [pinned-tickers, watchlist, dual-source, latent-months]
opened_by: nick
opened_at: 2026-05-26T16:30:00Z
resolved_at: 2026-05-26T16:40:00Z
---
# Incident — Starred/pinned tickers had no MR + sentiment signals (May 21 → May 26)

**Discovered:** 2026-05-26, when Nick noticed starred tickers weren't getting fresh prices/signals on the trade dashboard after the rcg-nixos WSL reload.
**Component:** `market_sentiment_bbg.py` + the dual-watchlist file layout.
**Severity:** Quietly degraded. Pinned tickers were appearing in the screener output (`outputs/watchlist.json`, 120 tickers) AND in the BBG intraday pull (`bloomberg_prices.json`, 120 tickers with prices) — but were NOT appearing in `factor_signals_bbg.json` (only 3 tickers: SPY, QBTS, ONDS), which is what `trade.html` reads for per-ticker MR/sentiment signals. So the dashboard showed pinned tickers but with no signal data. Bug had been latent since the screener-output-watchlist split, likely months.

---

## Root cause

Two watchlist files on rcg-nixos with the same name but different contents and consumers:

| File | Content | Consumers |
|---|---|---|
| `/home/nixos/Prod/V1/src/watchlist.json` | **Static 6 tickers** (SPY, QBTS, ONDS, NVDA, TSLA, SERV), mtime 2026-03-12 | `market_sentiment_bbg.py` (line 863: `WATCHLIST_PATH = Path("/home/nixos/Prod/V1/src/watchlist.json")`) |
| `/home/nixos/Prod/V1/outputs/watchlist.json` | **Daily-regenerated 120 tickers** including user-pinned + TAM-tagged + screener top-tickers | `dynamic_factor_screener_v3.py` writes; SCP'd to `rcg-base:C:\Users\ndiaz\Dropbox\RCG_2020\watchlist.json` for Windows BBG puller to consume |

The Windows side was correct — `bloomberg_prices.py` pulled all 120 tickers including pinned, and they showed up in `bloomberg_prices.json` with valid prices.

The NixOS-side `market_sentiment_bbg.py` was wrong — it computed MR/sentiment signals only for the 6 static tickers from `src/watchlist.json`, completely ignoring the much-larger `outputs/watchlist.json` that the screener generates daily.

The pin-flow gives a false sense of completeness: `trade.html` POSTs `/pinned/<T>` → `user_pinned.json` updates → screener merges it into `outputs/watchlist.json` → Windows BBG pull includes it → prices appear in `bloomberg_prices.json`. But the signal-computation step on NixOS reads the OTHER watchlist and never sees the pin.

## Why it wasn't caught earlier

- The pinned tickers DID appear on the dashboard (because the price column comes from `bloomberg_prices.json`, which had them).
- They were missing only the MR z-score / sentiment label / signal columns — easy to miss in the UI because empty cells just look like "low conviction" or "inactive."
- No alert/watchdog watches for "factor_signals_bbg.json watchlist size != bloomberg_prices.json watchlist size."

## Fix (v29.15)

Symlinked `src/watchlist.json` → `outputs/watchlist.json`. One source of truth; both consumers now read the same 120-ticker daily-regenerated list. Original static file preserved at `src/watchlist.json.bak-static-2026-03-12` for reference.

Schema-compatible: `outputs/watchlist.json` is a strict superset of the old static file (has all the keys `market_sentiment_bbg.py` expects: `tickers`, `lookback_bars`, `lookback_override`, `notes` — plus `generated_at` / `source` extras that don't break anything).

Verification: after fix + one sentiment refresh cycle, `factor_signals_bbg.json.watchlist` went from 3 entries to 120, all 6 pinned tickers present.

## Hardening followups

- **3 macro tickers (NVDA, TSLA, SERV) dropped between static + screener-generated watchlists.** They were in the original `src/watchlist.json` static list but the screener's daily run doesn't include them in `outputs/watchlist.json` (they aren't in the macro list, user_pinned, or TAM-tagged). Either add them to the screener's hard-coded macro tickers in `dynamic_factor_screener_v3.py`, OR add them to `user_pinned.json` if Nick still wants them tracked, OR explicitly drop them. **MM decision needed.**
- **Cross-file size invariant alert:** the infra probe could compare `bloomberg_prices.json.watchlist` size vs `factor_signals_bbg.json.watchlist` size and alert if they diverge by >20%. Would have caught this immediately.
- **Eliminate the dual-watchlist file pattern.** The fact that there were two files with the same name in different paths and one was stale by months is itself the bug. Consolidate: have the screener write directly to `src/watchlist.json` (which is what `market_sentiment_bbg.py` expects), OR rename one of them to make the difference obvious.
