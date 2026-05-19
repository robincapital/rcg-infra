# RCG Observability Standard — Track / Monitor / Repair

**Status:** Active as of 2026-05-19, v28.5
**Owner:** MM-set policy. Every new RCG deliverable must satisfy this before it ships.

---

## Why this exists

On 2026-05-18, the screener went dark for 26 hours and nobody noticed until a manual spot-check. The refresh server was alive (systemd reported healthy) but wedged — `bloomberg_prices.json` hadn't been written. There was no detection, no alert, no auto-recovery. Live prices on the dashboard were 24 hours stale.

**Lesson:** "systemd reports active" ≠ "the thing is working." Every deliverable that writes data needs three independent layers.

---

## The Three Layers

Every system that produces data or runs on a schedule must answer these three questions:

### 1. **Track** — *Where can I see whether it's working?*

The artifact's freshness is observable without SSH-ing into the box.

- Data files must surface their `mtime` somewhere the user can see (dashboard header strip, status page, or `/health` endpoint).
- All RCG dashboards include `<script src="feed_status.js"></script>` which posts a top-right chip showing every critical feed's age.
- New JSON outputs must be added to `feed_status.js`'s `FEEDS` array with a sensible threshold (e.g. 15 min for intraday, 24 hr for batch).

### 2. **Monitor** — *Who alerts me when it's broken?*

Detection that doesn't depend on a human noticing.

- Long-running services: use systemd `Type=notify` + `WatchdogSec=` with an app-side loopback probe (see `sentiment_refresh_server.py` for the pattern: ThreadingHTTPServer + 20s probe of `/status` before sending `WATCHDOG=1`).
- Output files: an external watchdog checks symptom (file mtime) and bypasses the mechanism. See `src/screener_watchdog.sh` for the template.
- Failures post to Slack `#infra-ops` automatically. Bot token at `~/.slack_tokens.json`.

### 3. **Repair** — *What fixes it?*

Recovery happens automatically or with a single command.

- App-side `WatchdogSec=` → systemd kills+restarts hung processes.
- External watchdog auto-restart with a budget (e.g. 3/hr; further failures escalate to `HARD-FAIL` Slack alert + stop trying so we don't thrash).
- For irrecoverable failures: an `#infra-ops` Slack alert tells the human exactly what to look at + the manual command to run.
- A recovery action that requires more than a single `systemctl restart` should be documented in an `docs/incidents/` post-mortem.

---

## Per-deliverable checklist

When shipping a new service, script, or dashboard, the spec must include a section like this:

```
## Observability (Track / Monitor / Repair)

Track:    Output `outputs/foo.json` (mtime exposed via feed_status.js entry)
Monitor:  rcg-foo-watchdog timer (5min), alerts #infra-ops on stale-and-failing
Repair:   Auto-restart on staleness (budget 3/hr); HARD-FAIL escalates to Slack
          Manual playbook: `sudo systemctl restart rcg-foo.service`
```

If any of the three is "n/a" or "we'll figure it out later", the deliverable does not ship.

---

## Concrete patterns in the repo

| Concern | File |
|---|---|
| App-side `sd_notify` watchdog pattern | `src/sentiment_refresh_server.py` (`_sd_notify`, `_watchdog_loop`) |
| External watchdog script template | `src/screener_watchdog.sh` |
| Slack alert helper (bash) | `src/screener_watchdog.sh` `slack_alert()` function |
| Universal data-freshness widget | `src/feed_status.js` |
| NixOS unit with WatchdogSec | `/etc/nixos/claude-finance.nix` → `rcg-sentiment-refresh` |
| NixOS unit + timer template | `/etc/nixos/claude-finance.nix` → `rcg-screener-watchdog` |

When building something new, copy from these and adapt.

---

## What changes for future work

Effective immediately:

1. **Specs must include an Observability section** (Track / Monitor / Repair). The markout dashboard spec at `docs/markout_dashboard_spec.md` is the first one to add this retroactively in its next revision.
2. **PR descriptions** mention the watchdog/alert wiring as part of "done."
3. **Postmortems** filed under `docs/incidents/YYYY-MM-DD-<slug>.md` for any user-visible outage. Template: `docs/incidents/2026-05-19-screener-dark.md`.
4. **Critical-feeds list** — `feed_status.js`'s `FEEDS` array is the source of truth. New outputs that the user might rely on get added there.

---

## Known gaps (current state, 2026-05-19)

What we still don't monitor:
- Postgres signal capture itself (rcg-predictions-capture, rcg-models-capture, rcg-forward-returns): timers fire, but if the script silently fails to insert rows, we don't notice. Should check signals-table row count delta per timer firing.
- Meta-blend `outputs/meta_model_weights.json` updates only after the gate hits (≥1000 obs + ≥7 days). When the gate first triggers we'll know fairly soon, but we don't yet alert on "gate met but train script failed."
- The cron-based markout publish at 06:00 UTC: if it fails, no alert. Need to wrap in a systemd-managed wrapper that posts to `#infra-ops` on non-zero exit.

These are followups. Each gets its own watchdog + Slack alert before the related work is considered "complete."

---

## How to add observability to a thing that already exists

The screener migration template:

1. Add an entry for the output file to `src/feed_status.js` FEEDS array
2. Decide on the freshness threshold (intraday: 15-30min; daily-batch: 24-48hr)
3. If it's a long-running service:
   - Modify the app to call `_sd_notify("READY=1")` after start and `_sd_notify("WATCHDOG=1")` periodically (with a self-probe gate)
   - Update the NixOS unit: `Type = "notify"`, add `WatchdogSec = "60s"`, `NotifyAccess = "main"`
4. If it's a periodic job:
   - Wrap the ExecStart in a script that posts to Slack on non-zero exit
   - Add an external watchdog timer if the symptom (output file freshness) matters more than the process exit code
5. Test by manually wedging the thing (e.g. `kill -STOP <pid>`) and confirming the alert fires
6. Document the recovery playbook in `docs/incidents/` or in the service's own readme
