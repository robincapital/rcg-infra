---
date: 2026-05-26
severity: P2
category: infra
component: mixed: cron / systemd / WSL2 / bloomberg_prices.py
status: resolved
summary: Post-WSL-reboot replay gaps: cron jobs (markouts) didn't catch up while systemd timers did; intraday bars too thin
tags: [reboot, cron-vs-timer, persistent, wsl2, intraday]
opened_by: nick
opened_at: 2026-05-26T16:30:00Z
resolved_at: 2026-05-26T17:00:00Z
---
# Incident — Post-WSL-reboot replay gaps + intraday-bars too thin (May 23-26)

**Discovered:** 2026-05-26 ~16:30 UTC, when Nick noticed multiple staleness problems after reloading WSL2 + reconnecting Tailscale: Slack agent silent, markouts 3-day stale, top-mover EOD closes wrong, intraday chart-on-click empty.
**Component:** Mixed — user-systemd lingering, cron-vs-timer replay behavior, BBG-pull bars depth, sharadar↔screener ordering.
**Severity:** No data lost (all sources had fresh data; consumers either weren't running or were reading stale snapshots). Trading dashboard quality was degraded for the reboot day.

---

## What broke and why — the four threads

### 1. Slack agent + infra probe silent
**Cause:** user-systemd instance never started post-WSL-reload — `/run/user/1000/systemd/` socket was missing despite `user@1000.service` showing "active". No linger.
**Fix:** `sudo loginctl enable-linger nixos` + `sudo systemctl restart user@1000.service`. Linger is now permanent — future reboots auto-start the user services without an SSH login.
**Doc:** `CONTEXT_infra.md` "WSL2 user-bus quirk" section captures the symptom + recipe.

### 2. Markouts 3 days stale
**Cause:** `markout_eval_publish.py` is scheduled via crontab (`0 6 * * *`). The box was DOWN May 24, 25, 26 mornings. **Cron does not replay missed fires.** Last successful run was 2026-05-23 06:03.
**Fix tonight:** manually ran `/home/nixos/venv-rcg-prod/bin/python /home/nixos/Prod/V1/src/markout_eval_publish.py` — 47 rows / 25 with trades / 290KB / 185s.
**Permanent fix (followup):** migrate to a systemd timer with `Persistent=true`. See TODO.

### 3. Top-mover EOD closes 1-day stale
**Cause:** Ordering race at boot catch-up. Both `sharadar-download.timer` and `rcg-screener-long.timer` have `Persistent=true`. After the 16:09 boot they BOTH tried to catch up missed runs at the same time. The screener finished at 16:12 (reading the OLD SEP from before today's Sharadar fetch). Sharadar then finished writing fresh parquet files at 16:14. So `long_screener_results.csv` captured `last_price` from SEP-as-of-yesterday-morning instead of SEP-as-of-Friday-close.
**Self-heal:** Tomorrow's normal schedule (sharadar 08:20 → screener 09:00, 40 min gap) avoids the race. Re-running tonight crashed on a separate polars Int64-vs-Float64 bug in `apply_blended_targets` (line 1611). Tracked as separate followup.
**Permanent fix (followup):** Add `After=sharadar-download.service` to the screener unit so the race can't happen at boot catch-up either.

### 4. Intraday chart-on-click had only 4 bars
**Cause:** `bloomberg_prices.py:fetch_watchlist_bars` uses `days_back=3`. With Memorial Day + weekend, a 3-day window from Tuesday morning only captures today's first 3-4 open hours. No prior-week context. `market_sentiment_bbg.py`'s MR computation also chokes with "only 4 bars" (it expects ≥10 lookback bars).
**Fix tonight:** bumped to `days_back=7` in `bloomberg_prices.py`. Verified: BB bar_count went from 4 → 29, MR signal went from `label: INACTIVE, error: "only 4 bars"` → `label: WATCH, z_score: 1.318, signal: -0.318, n_bars: 10` (full record).

---

## The architectural lesson

**Cron and systemd timers have different "missed-fire" semantics, and we have both in production:**

| Scheduler type | Replays missed fires after downtime? | Examples currently in use |
|---|---|---|
| `systemd .timer` with `Persistent=true` | **Yes** — fires immediately at boot if it missed its scheduled time | Bloomberg pull, screener-long, sharadar download, models capture, predictions capture, forward returns, leaderboard, correlations, infra-health (all of them) |
| `systemd .timer` without `Persistent` | No — silently skips missed window | (none currently) |
| `crontab` | **No** — silently skips missed window | markout_eval_publish (the bug), market_sentiment_bbg (intraday, OK to skip), jupyter check (self-heals via `*/5`) |
| `@reboot` cron | Fires once on boot | http server :8080 |
| user-systemd timer | Yes IF linger enabled — wasn't until tonight | rcg-agent (service), rcg-infra-probe.timer |

**Rule of thumb going forward:** anything that produces a daily artifact (markouts, EOD reports, models-train) MUST be a systemd timer with `Persistent=true`. Don't use cron for it. Intraday jobs that fire every 30 min are fine on cron because the next fire is right around the corner anyway.

## Hardening followups

- **Migrate markout_eval_publish from cron → systemd timer with Persistent=true.** This is the canonical fix for the "markouts 3-day stale after reboot" bug. ~15 min of work.
- **Add `After=sharadar-download.service` ordering to `rcg-screener-long.service`.** Prevents the boot-catch-up race that gave us 1-day-stale EOD closes today. Normal daily schedule is unaffected. ~5 min of work.
- **Fix the polars `apply_blended_targets` Int64-vs-Float64 crash.** `dynamic_factor_screener_v3.py` line 1611 — `pl.Series("analyst_target_mean", analyst_means)` blows up when Finnhub returns floats. Add `strict=False` or explicit `dtype=pl.Float64`.
- **Test post-reboot recovery as a routine drill.** This is the second WSL-reload incident this month. Worth a simple Slack-postable script that prints "what's running, what's stale, what would replay" so we can run it after every reboot and catch regressions early.
- **Mirror context-doc updates to the Dropbox handoff folder.** When SESSION_HANDOFF.md was written it didn't yet know about these patterns; future sessions should pick them up from `Dropbox/RCG_2020/laptop_onboarding/`.

## Quick "is everything caught up" checklist (after a reboot)

Run from a fresh SSH session:

```bash
# 1. Boot+linger check
loginctl show-user nixos | grep Linger   # must be Linger=yes

# 2. Each output file's freshness (anything > 24h stale is suspect)
for f in /home/nixos/Prod/V1/outputs/{markouts,bloomberg_prices,factor_signals_bbg,long_screener_results,leaderboard,correlations}*.{json,csv}; do
    [ -e "$f" ] && stat -c '%y  %n' "$(readlink -f "$f")"
done | sort

# 3. Slack agent is alive
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active rcg-agent.service

# 4. Sentiment refresh confirms last_refresh < 30 min ago
curl -s http://localhost:8085/status | python3 -m json.tool
```

If anything's stale, the postmortem above maps which scheduler owns it.
