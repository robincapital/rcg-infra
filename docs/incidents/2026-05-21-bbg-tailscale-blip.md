---
date: 2026-05-21
severity: P1
category: infra
component: rcg-screener-watchdog + Tailscale NixOS<->Windows
status: resolved
summary: BBG pull 16h stale via Tailscale blip; watchdog self-DoS'd with 9 duplicate HARD-FAIL alerts
tags: [bbg, tailscale, watchdog, alert-fatigue]
opened_by: agent
opened_at: 2026-05-21T13:55:00Z
resolved_at: 2026-05-21T15:00:00Z
---
# Incident — BBG Pull Stale 16h via Tailscale Blip + Watchdog Self-DoS (May 20-21)

**Discovered:** 2026-05-21 13:55 UTC, when the `rcg-screener-watchdog` Slack alert "HARD-FAIL — 3 restarts in last hour" hit `#infra-ops`.
**Component:** `rcg-screener-watchdog.service` + `rcg-sentiment-refresh.service` + Tailscale NixOS↔Windows path.
**Severity:** `bloomberg_prices.json` was 16.4 hours stale across the May 21 market open (13:30 UTC). Dashboards showed prior-day prices. No trades depended on it (we're still pre-execution), but it was a textbook "watchdog can't fix what's broken upstream" failure.
**Resolution:** Tailscale path self-healed by ~14:00 UTC; one manual `/refresh` re-pulled the file. Followups now wired: upstream-aware watchdog (v29.12) + alert cooldown.

---

## Timeline (UTC)

| Time | Event |
|---|---|
| May 20 21:30 | Last successful BBG pull. File mtime frozen here. |
| May 20 22:00 → May 21 13:00 | Tailscale path NixOS → `100.86.90.78` (rcg-base / Windows BBG box) silently broken. Local cron + timers kept firing; SSH attempts to Windows timed out (`Connection timed out`). Outside RTH so the watchdog stayed quiet. |
| May 21 13:00:02 | First post-open watchdog tick. File 929 min stale → restart #1. `/refresh` triggered. Inside the refresh server, the SSH to Windows timed out again. Local file not updated. |
| May 21 13:05:00 | Restart #2. Same outcome. |
| May 21 13:10:00 | Restart #3. Same outcome. |
| May 21 13:15:02 | Restart budget exhausted → **HARD-FAIL** alert to `#infra-ops`. |
| May 21 13:15-13:55 | Watchdog kept firing every 5 min, each time emitting the same HARD-FAIL alert. 9 duplicate Slack messages. |
| May 21 ~14:00 | Tailscale path quietly recovered (no event we have logs for). |
| May 21 14:50 | Manual `curl /refresh` → SSH to Windows OK → BBG pull OK → file mtime jumped to fresh. |
| May 21 15:00+ | Watchdog auto-resumed normal restart-on-staleness cycle. |

---

## Root cause

Two-layer failure:

**Layer 1 — Tailscale.** The NixOS → Windows direct connection went silent for ~16 hours. We don't have proof of the cause (Tailscale logs the box as `direct 136.28.124.175:42610` now, healthy). Candidates: Windows TS service restart, sleep/hibernation, ISP CG-NAT rebind, router reboot. No alerts because nothing actively probes the cross-host SSH.

**Layer 2 — Watchdog assumed every failure was a local-server wedge.** `screener_watchdog.sh` treats stale `bloomberg_prices.json` as a single symptom and applies one repair: restart `rcg-sentiment-refresh` + re-trigger `/refresh`. When the actual breakage was upstream (the SSH-to-Windows leg of the refresh), restarts couldn't help. The watchdog burned its 3-restart budget in 10 minutes, hit HARD-FAIL, and then alerted every 5 minutes for 40 minutes.

The honest read: this was a "monitor" failure per `docs/OBSERVABILITY_STANDARD.md` — we monitor the **symptom** (file age) but not the **failure mode** (upstream vs local).

## Fix (v29.12)

`src/screener_watchdog.sh` rewritten:

1. **Upstream TCP probe before counting a restart.** `timeout 3 bash -c '</dev/tcp/100.86.90.78/22'` cleanly separates "network broken" from "auth/local-server broken" — no SSH key in the loop to confuse the signal.

2. **Two named failure modes, two alert keys.**
   - `UPSTREAM-DOWN` → log + alert + exit 0. Does NOT burn restart budget. Tells the human exactly what to fix.
   - `HARD-FAIL` → only fires when upstream is reachable but local server stays wedged after 3 restarts. Now a real "the local stack is broken" signal.

3. **Alert cooldown.** Each failure-mode key gets 1 alert per hour via `/tmp/screener_watchdog_alerts` state file. The 9-duplicate flood from this incident becomes 1 alert per hour.

4. **Less code.** Down from 130 to 156 lines but the logic is half as branchy: helpers (`log`, `slack_alert`, `should_alert`, `upstream_alive`, `recent_restart_count`, `trim_restart_counter`) read top-down, the main flow is gate → check → classify → act.

Also installed: backup at `src/screener_watchdog.sh.bak-<epoch>`, syntax-checked, dry-run exit 0 on healthy state.

## Detection lessons

- **Don't alert the same condition forever.** Cooldown by key. The 1/hr knob is conservative but reduces Slack noise to "I'm telling you once an hour that this is still broken" — exactly the right tempo for a thing requiring manual action.
- **Symptom-based watchdogs need failure-mode classification.** "File is stale" is one symptom with at least two causes (upstream down, local wedged). The repair only works for one of them. The diagnostic — "is the upstream actually reachable?" — is cheap; spend the 3 seconds before burning a restart.
- **Cross-host probes deserve their own monitoring.** Nothing on either side noticed Tailscale dropped between hosts. Followup below.

## Hardening followups

- **Tailscale liveness probe.** Add `rcg-tailscale-probe.timer` (5min) that runs `tailscale ping --timeout=3s --tsmp <peer>` for each critical peer and alerts to `#infra-ops` on consecutive failures. The probe should NOT be on the same box as the peer it monitors (a peer can't tell you it's gone). Run from NixOS for all Windows peers and vice-versa.
- **Heartbeat on Windows BBG box.** Have `bloomberg_prices.py` (or a sibling) write a `last_heartbeat.txt` file via SCP every 10 min independent of the BBG terminal being open. Even if BBG isn't logged in, we'd know the Windows side + Tailscale + SSH layer is healthy.
- **NixOS-declarative migration.** `rcg-screener-watchdog.service` lives in `/etc/systemd/system/...` (root-installed) but the script is in `/home/nixos/Prod/V1/src/`. The unit should be in `claude-finance.nix` so the pair is reproducible. Same followup we deferred from the May 19 + May 20 incidents — needs to land in one batch.
