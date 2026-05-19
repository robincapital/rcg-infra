# Incident — Screener Dark for 26 Hours (May 18-19, 2026)

**Discovered:** 2026-05-19 13:38 ET, ~26h after the fault occurred
**Component:** `rcg-sentiment-refresh.service` (refresh server, port 8085)
**Severity:** Live-price overlay on `trade.html` stale (decision-affecting); tournament + meta-blend unaffected (they read DB, not live prices)
**Resolution:** Refresh server restarted at 13:43 ET; bloomberg pull verified successful within 1 minute

---

## What happened

1. `rcg-sentiment-refresh.service` is a long-running Python HTTP server on port 8085. The systemd timer `rcg-bloomberg-pull.timer` fires every 30 min during RTH and POSTs to `localhost:8085/refresh`, which then triggers a BBG pull on the Windows desktop via a separate mechanism. Result is written to `/home/nixos/Prod/V1/src/bloomberg_prices.json`.
2. At **2026-05-18 15:30 UTC**, the refresh server stopped responding to requests. It was still running (systemd reported active), but `curl` calls timed out (HTTP 000).
3. Memory had climbed to 878 MB (peak 2.1 GB) since the last restart. No log entries after 15:30.
4. Subsequent timer firings (every 30 min) all hit curl timeouts. The `rcg-bloomberg-pull.service` entered "failed (exit 28)" state but no alert was wired up; service is `Type=oneshot` with no `OnFailure=` and no monitoring downstream.
5. The dashboard kept rendering 24-hour-old prices with no warning.

## Root cause (suspected)

The refresh server appears to have hung — likely a deadlock or a stuck external call (BBG terminal on Windows side may have lost a connection, leaving a pending request open on the server side). Without watchdog logic, the process stays alive and systemd considers it healthy. No reproducer captured before the restart cleared the process state.

The systemd unit doesn't have:
- `Restart=on-failure` or `Restart=always`
- A `WatchdogSec=` directive (which would require app-side keepalive but is the textbook fix)
- An `OnFailure=` handler

## Fix applied

1. **Immediate (13:43 ET):** `sudo systemctl restart rcg-sentiment-refresh.service`. Verified refresh server responds (HTTP 202 on /refresh), bloomberg_prices.json updated within 1 min, downstream `rcg-bloomberg-pull.service` returns success.

2. **Watchdog (v28.1):** Added `src/screener_watchdog.sh` + cron entry (`*/5 * * * *`). Behavior:
   - Gates on RTH window (13:00-21:30 UTC, weekdays) — no-op outside
   - Checks `bloomberg_prices.json` mtime; if > 15 min stale, restarts refresh server
   - Restart budget: max 3 per hour. If exceeded, logs HARD-FAIL marker and stops trying.
   - Logs to `/home/nixos/screener_watchdog.log`

## Followups (not done yet)

- **NixOS-declarative unit migration** — current watchdog lives in user crontab, which isn't reproducible. Should move to `/etc/nixos/` declaratively, alongside the existing `rcg-*` units.
- **Slack alert on HARD-FAIL** — currently the only signal is the log file. A slack post when restart budget exceeds would close the loop.
- **App-side WatchdogSec** — modify `sentiment_refresh_server.py` to do `sd_notify("WATCHDOG=1")` every 30s, set `WatchdogSec=60s` in the unit. Systemd then auto-restarts when the process actually hangs. This is the textbook fix and prevents the externally-observed staleness gate from being the only line of defense.
- **Per-feed staleness dashboard** — surface mtime of every critical JSON on `trade.html` so a "data is N minutes old" indicator is always visible. The user shouldn't have to ask "is this fresh?".

## Detection lessons

The "trade.html hasn't updated in 25 hours" signal came from Nick at 13:38 ET — about 26 hours after the actual failure. Better detection would have caught it within 30 min:
- A heartbeat from the dashboard frontend (display age of `bloomberg_prices.json` in the header strip)
- An alert when `rcg-bloomberg-pull.service` enters failed state more than 3x in an hour
- A daily `rcg-infra-health.service` already exists (in timer list) — it should be checking bloomberg_prices.json freshness, but doesn't appear to be.

Each of these would have flipped the dark window from 26 hours to under an hour.
