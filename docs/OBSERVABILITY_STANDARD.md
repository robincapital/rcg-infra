# RCG Observability Standard — Track / Monitor / Repair

**Status:** Active as of 2026-05-26, v29.16 (formal Postmortem Policy added)
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

### Repair sub-principle: classify failure mode before applying a repair

*Added 2026-05-21 after the BBG Tailscale-blip incident (`docs/incidents/2026-05-21-bbg-tailscale-blip.md`).*

A symptom-based watchdog typically has ONE repair (e.g. "restart the service") but the symptom may have MULTIPLE causes (e.g. local server wedged vs. upstream host unreachable). Applying the repair to the wrong cause:

- Doesn't fix anything,
- Burns the restart budget pointlessly,
- Triggers HARD-FAIL alerts that misdirect the human ("restart the local server!" when the actual problem is on the upstream box).

**Rule:** Before counting a restart against the budget, run a cheap probe that classifies the failure. For cross-host data pipelines, that's a 3-second TCP probe of the upstream port. Cost: trivial. Value: differentiated alerts ("UPSTREAM-DOWN, check box X" vs "LOCAL-WEDGE, check service Y") and no budget burn on irrelevant retries.

**Rule:** Every alert that can repeat needs a cooldown key. The May 21 incident emitted the same HARD-FAIL message 9 times in 40 min. Cooldown of 1 alert/hour per key reduces noise to "telling you once an hour this is still broken" — exactly right for things needing manual action.

Reference template: `src/screener_watchdog.sh` v29.12. The helpers `upstream_alive()` and `should_alert()` are the pattern to copy when building new watchdogs.

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
3. **Postmortems** filed under `docs/incidents/YYYY-MM-DD-<slug>.md` for any user-visible outage. Template: `docs/incidents/TEMPLATE.md`. **See § Postmortem Policy below — this is a compliance-grade REQUIREMENT, not best-effort.**
4. **Critical-feeds list** — `feed_status.js`'s `FEEDS` array is the source of truth. New outputs that the user might rely on get added there.

---

## Postmortem Policy (REQUIREMENT, not a suggestion)

*Codified 2026-05-26 at MM direction. Compliance-grade requirement, not best-effort.*

### When a postmortem is REQUIRED

You MUST file a postmortem for any of:

1. **Production-tool failure** — any RCG production component (`rcg-agent`, `rcg-sentiment-refresh`, BBG pull pipeline, screener, markout publisher, watchdogs, infra probe, dashboards, postgres pipelines, etc.) fails, errors, or produces stale/wrong data that reaches a user.
2. **User-visible outage** — anything Nick or any RCG user notices "is broken" — even if the cause turns out to be benign (browser cache, expected behavior). File anyway with `status: resolved` and a brief explanation. The record matters more than the severity.
3. **Client-affecting issue** — any incident touching a client deliverable, report, communication, or invoice.
4. **Data quality issue** — silently corrupt / stale / wrong / missing data in any output that downstream consumers (dashboards, models, signals, reports) read.
5. **Security or compliance event** — any failed auth, credential leak, unexpected access, or compliance-gate hit. `category: security` or `category: compliance`.
6. **Development regression** — any commit that breaks production after deploy, even briefly. `category: development`.

If unsure, file it. Cheaper to over-record than to discover months later that you needed a paper trail for the regulator.

### When a postmortem is NOT required

- Single transient errors caught by retry/cooldown logic that recovered automatically without human notice.
- Routine planned maintenance (deployments, reboots) that succeeded as designed.
- Latent bug discovery before any production impact — file a `docs/TODO.md` entry, not a postmortem.

### The workflow (4 steps)

1. Copy `docs/incidents/TEMPLATE.md` → `docs/incidents/YYYY-MM-DD-<short-slug>.md`. Fill every field of the YAML front-matter — tooling consumes them.
2. Write the body. Required sections: Timeline (UTC), Root cause, Why it wasn't caught earlier, Fix, Hardening followups, Detection lessons.
3. Publish:
   ```bash
   python3 /home/nixos/Prod/V1/scripts/post_incident_to_slack.py \
       /home/nixos/Prod/V1/docs/incidents/<your-file>.md
   python3 /home/nixos/Prod/V1/scripts/regen_incident_index.py
   cp /home/nixos/Prod/V1/docs/incidents/<your-file>.md \
       /home/nixos/Prod/V1/outputs/incidents/
   ```
4. Cross-reference in `docs/TODO.md` under "Infra Hardening Followups" if there are open action items.

### Three independent records (regulators can query any)

| Where | Format | Best for |
|---|---|---|
| `docs/incidents/*.md` (git-tracked) | Markdown + YAML front-matter | Immutable timestamped record. `git log docs/incidents/` is the audit trail. |
| `docs/incidents/INDEX.md` (auto-generated) | Sortable markdown table | Quick human / regulator browsing. Filter by date / severity / category / status. |
| Slack `#rcg-postmortems` (channel `C0B673DJB0E`) | Structured chat with links back to source | Slack's native search ("in:#rcg-postmortems P0", "in:#rcg-postmortems client") for ad-hoc regulator queries. |

### Front-matter schema

```yaml
---
date: 2026-MM-DD                # ISO date discovered
severity: P0|P1|P2|P3           # see TEMPLATE for definitions
category: production-tool | development | client | infra | security | compliance
component: <file or system>
status: open | investigating | resolved | wontfix
summary: <≤120 chars — appears in Slack + INDEX>
tags: [free-form, lowercase, hyphens]
opened_by: nick | agent | claude | <name>
opened_at: 2026-MM-DDTHH:MM:SSZ
resolved_at: <fill when status flips to resolved>
---
```

### Sample queries (for CCO / regulator requests)

```bash
# All P0/P1 incidents in May 2026:
grep -l 'date: 2026-05' docs/incidents/*.md | xargs grep -lE '^severity:\s*(P0|P1)'

# All client-affecting incidents ever:
grep -l '^category: client' docs/incidents/*.md

# All currently-open (unresolved) incidents:
grep -l '^status: open' docs/incidents/*.md

# Anything involving a specific component:
grep -l 'component:.*market_sentiment_bbg' docs/incidents/*.md
```

INDEX.md is plain markdown; any tool that parses markdown tables (or Claude itself) can slice it.

---

## Known gaps (current state, 2026-05-21)

What we still don't monitor:
- Postgres signal capture itself (rcg-predictions-capture, rcg-models-capture, rcg-forward-returns): timers fire, but if the script silently fails to insert rows, we don't notice. Should check signals-table row count delta per timer firing.
- Meta-blend `outputs/meta_model_weights.json` updates only after the gate hits (≥1000 obs + ≥7 days). When the gate first triggers we'll know fairly soon, but we don't yet alert on "gate met but train script failed."
- The cron-based markout publish at 06:00 UTC: if it fails, no alert. Need to wrap in a systemd-managed wrapper that posts to `#infra-ops` on non-zero exit.

These are followups. Each gets its own watchdog + Slack alert before the related work is considered "complete."

## Closed gaps (since prior revision)

- **2026-05-21** — Symptom-based watchdogs now classify upstream-vs-local failure before applying their one-size-fits-all repair. See "Repair sub-principle" above + `src/screener_watchdog.sh` v29.12.
- **2026-05-21** — Repeating alerts have cooldown keys (1/hr default). No more HARD-FAIL Slack spam during long outages.
- **2026-05-21** — Cross-host Tailscale liveness is now monitored. `src/rcg_infra_probe.sh` v29.13 runs every 5 min, alerts on 2 consecutive `tailscale ping` failures per critical peer with 1/hr cooldown. Closes the gap that let the May 21 BBG-stale-16h incident go undetected until downstream symptoms appeared.
- **2026-05-21** — Windows BBG box now emits an independent heartbeat (Task Scheduler "RCG Heartbeat" runs `rcg_heartbeat.py` every 10 min, SCPs timestamp to NixOS). The infra probe alerts when the heartbeat is >30 min stale, distinguishing "Windows down / scripts broken" from "Tailscale down" (which has its own probe) and from "BBG terminal logged out" (which only manifests as bloomberg_prices.json staleness).

## Pattern reference: consecutive-fail debouncing

The infra probe introduces a different debounce strategy than the screener watchdog:

- **Watchdog (`src/screener_watchdog.sh`):** *budget-based*. Up to N restarts per hour; further failures escalate to HARD-FAIL. Fits well when each probe-failure incurs a real recovery action.
- **Probe (`src/rcg_infra_probe.sh`):** *consecutive-fail-count*. Alert only after K probes-in-a-row have failed. Fits well when the probe is read-only — no point penalizing the budget for a single flap.

When building a new monitor, pick the pattern that matches whether your probe TAKES ACTION on each fire (budget) or just OBSERVES (consecutive count).

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
