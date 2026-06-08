# RCG Infra Context

**Read this first** when working on the Slack agent, watchdogs, BBG pull, or any other "is the production stack alive?" question. Companion to `CONTEXT_price_targets.md` (math) and `CONTEXT_signal_capture.md` (signal pipeline).

**Last updated:** 2026-05-26 — Agent v29.12, screener watchdog v29.12, infra probe v29.14, BBG puller v29.15. Postmortem Policy formalized (v29.16) + `#rcg-postmortems` Slack channel + auto-index tooling.

---

## The stack in one diagram

```
                  ┌─────────────────────────┐
                  │  Windows desktop        │   ← rcg-base / 100.86.90.78
                  │  (DESKTOP-2L2313O)      │     Bloomberg Terminal lives here
                  │                         │     ndiaz@ runs bloomberg_prices.py
                  │  Task Scheduler hourly  │     hourly via Task Scheduler
                  │  + on-demand via SSH    │
                  └────────┬────────────────┘
                           │  SCP (Tailscale)
                           ▼
                  ┌─────────────────────────┐
                  │  NixOS                  │   ← rcg-nixos / 100.78.59.48
                  │  /home/nixos/Prod/V1/   │
                  │                         │
                  │  rcg-sentiment-refresh  │   :8085 HTTP, triggers BBG pull,
                  │  rcg-bloomberg-pull     │   computes sentiment, writes
                  │  rcg-screener-watchdog  │   bloomberg_prices.json + JSON
                  │  rcg-agent (Slack)      │   factors. Watchdog ensures
                  └────────┬────────────────┘   freshness during RTH.
                           │
                           ▼
                       Slack + dashboards + Postgres (rcg_signals)
```

Tailscale is the ONLY transport NixOS↔Windows. If ALL configured Windows boxes are offline in `tailscale status`, the BBG pull is dead until at least one recovers.

**Multi-host pattern (v29.14):** The BBG pull and heartbeat scripts are installed on EVERY Windows box that may host BBG. Each box's Task Scheduler fires `bloomberg_prices.py` independently; the script exits cleanly when it doesn't have a local BBG session (Bloomberg's "one active session per user" rule guarantees exactly one host succeeds at a time). The successful pull goes to `bloomberg_prices.json.tmp.<HOSTNAME>` then atomically renames to the canonical path — multiple writers can't corrupt the file mid-write. To onboard a new Windows box, follow `docs/LAPTOP_SETUP.md`.

---

## Where to look when something's wrong

| Symptom | First file to read | Then check |
|---|---|---|
| `bloomberg_prices.json` stale during RTH | `/home/nixos/screener_watchdog.log` | `rcg_infra_probe.log` for tailscale + heartbeat status |
| `Tailscale peer down` alert on Slack | `rcg_infra_probe.log` last `TS-FAIL` entry | `tailscale status` from BOTH ends; power-on the peer box |
| `Heartbeat stale` alert on Slack | `rcg_infra_probe.log` + Windows Task Scheduler "RCG Heartbeat" Last Result | If Tailscale probe is OK, the Windows scripts/python pipeline is broken |
| Slack agent stops responding | `journalctl --user -u rcg-agent.service -n 50` | conversation JSON in `var/agent_conversations/` |
| Slack agent silent right after rcg-nixos reboot + SSH shows `Failed to connect to user scope bus via local transport: No such file or directory` | `ls /run/user/1000/systemd/` — if missing, the WSL2 user-bus quirk has bitten. `sudo systemctl restart user@1000.service` rebuilds it. Confirm `loginctl show-user nixos \| grep Linger` returns `Linger=yes` so future reboots autostart cleanly (set with `sudo loginctl enable-linger nixos`). | First seen 2026-05-26; see "WSL2 user-bus quirk" note below |
| Slack agent in tool loop | `var/agent_conversations/<hat>-*.json` last 30 messages | look for repeated fingerprint — should be caught by `MAX_DUPLICATE_TOOL_CALLS=3` |
| HARD-FAIL alert on `#infra-ops` | `screener_watchdog.log` for the failure class | UPSTREAM-DOWN ≠ HARD-FAIL: see Repair sub-principle in OBS |
| `markouts.json` shows 0 models | `markout_eval_publish.py` cron log (last 06:00 UTC fire) | NOT `markout_publish_simple.py` (disabled 2026-05-20) |
| Starred/pinned ticker rows on `trade.html` show prices but no MR/sentiment signal | `factor_signals_bbg.json.watchlist` size vs `bloomberg_prices.json.watchlist` size | `src/watchlist.json` must be a symlink → `outputs/watchlist.json`. See incident 2026-05-26 for why. |

---

## Critical components (with current versions)

| Component | Path | Version | Role |
|---|---|---|---|
| Slack agent main loop | `src/agent/agent_core.py` | v29.12 | API loop, prompt caching, dup-call short-circuit |
| Agent tools (read/bash/sql/etc) | `src/agent/tool_wrapper.py` | v29.x | Safety-gated dispatchers |
| Sentiment + BBG pull server | `src/sentiment_refresh_server.py` | v28.2 | :8085 HTTP, SSH→Windows on `/refresh` |
| BBG puller (Windows side) | `C:\Users\ndiaz\Downloads\bloomberg_prices.py` | v29.14 | blpapi → JSON, atomic SCP via `.tmp.<HOSTNAME>` → mv. Installed on EVERY potential BBG box; only the active-session one writes. |
| Heartbeat (Windows side) | `C:\Users\ndiaz\Downloads\rcg_heartbeat.py` | v29.14 | Writes `heartbeat_<HOSTNAME>.txt` + SCPs to NixOS every 10 min (Task: "RCG Heartbeat"). Per-host file = visibility into which boxes are alive. |
| Screener watchdog | `src/screener_watchdog.sh` | v29.12 | RTH freshness check, upstream-aware, alert cooldowns |
| Infra probe (Tailscale + heartbeat) | `src/rcg_infra_probe.sh` | v29.14 | Config-driven peers (`var/active_peers.conf`), heartbeat glob — alerts only if NO Windows box is checking in. |
| Active-peers config | `var/active_peers.conf` | — | Single source of truth for which Windows boxes the probe monitors. Comment a line out to silence alerts about a stored-away box. |
| Markout publisher | `src/markout_eval_publish.py` | (cron 06:00 UTC) | Real tournament data → `outputs/markouts.json` |

`markout_publish_simple.py` is **disabled** — it wrote placeholder data over the real file. Do not re-enable.

---

## Agent token-consumption knobs

Set in `src/agent/agent_core.py`:

- `MAX_TOOL_LOOPS = 50` — runaway-loop ceiling.
- `MAX_DUPLICATE_TOOL_CALLS = 3` — same `(tool, input)` 3× in a row → ERROR with coaching message.
- `TOOL_RESULT_CAP = 20_000` bytes — per-tool-call result cap injected into conversation state.
- `DEFAULT_MAX_TOKENS_OUT = 16_384` — output budget; safety bound only, doesn't directly cost.
- **Prompt caching** (v29.12) — `cache_control: ephemeral` on system prompt + last tool schema. Confirmed working with `/tmp/test_cache_real.py` (~78% input savings on the cached prefix per session).

If the agent feels expensive, first check `var/agent_conversations/<hat>-*.json` `total_cost_usd` — that's the per-session truth. Then check the journal for cache_read_input_tokens > 0 in API response usage (the SDK logs at INFO).

---

## Operational principles (cross-reference)

The full doctrine is in `docs/OBSERVABILITY_STANDARD.md`. The short version:

1. **Track / Monitor / Repair.** Every service answers three questions: where can I see it's working, who alerts me when it's broken, what fixes it.
2. **Symptom vs failure-mode.** A symptom-based watchdog with ONE repair must classify the failure mode BEFORE applying the repair. The BBG May 21 incident is the canonical example — restarting our local server can't fix Tailscale.
3. **Alert cooldowns.** Every repeatable alert has a cooldown key. 1/hour is the default. No more 9-duplicate Slack floods.
4. **Less is more.** Each fix should make the codebase smaller or flatter, not bigger. If you're adding more than ~50 lines of "industrialize" code, you're probably building the wrong abstraction.
5. **Postmortem every failure.** Production-tool failures, user-visible outages, client-affecting issues, data quality issues, security events, and dev regressions ALL require a postmortem at `docs/incidents/YYYY-MM-DD-<slug>.md` with structured YAML front-matter. Auto-posts to `#rcg-postmortems` Slack channel + indexed in `docs/incidents/INDEX.md`. **This is a compliance-grade REQUIREMENT.** See `OBSERVABILITY_STANDARD.md § Postmortem Policy`.

---

## Incident archive

**Indexed at:** `docs/incidents/INDEX.md` (auto-generated, regulator-queryable) + Slack `#rcg-postmortems` channel (ID `C0B673DJB0E`) + git history of `docs/incidents/*.md`. Three independent records — see `OBSERVABILITY_STANDARD.md § Postmortem Policy` for the full taxonomy and workflow.

**Tooling:**
- `scripts/post_incident_to_slack.py <file>.md` — parses front-matter, posts structured summary to `#rcg-postmortems`
- `scripts/regen_incident_index.py` — rebuilds `INDEX.md` from all postmortem front-matter
- Template: `docs/incidents/TEMPLATE.md`

**Recent postmortems** (full list in `INDEX.md`):

- **2026-05-19** P0 — Screener dark for 26h, no detection. (Birthed the OBS standard.)
- **2026-05-20** P1 — Agent "can't execute" — psycopg missing + user-systemd PATH gap.
- **2026-05-21** P1 — BBG pull 16h stale + watchdog self-DoS during Tailscale blip.
- **2026-05-26** P2 — Pinned/starred tickers had no MR + sentiment signals — dual-watchlist pattern, stale static file source.
- **2026-05-26** P2 — Post-WSL-reboot replay gaps: cron didn't catch up, systemd timers did; intraday bars too thin.

Read these before adding new "industrializing" features — the postmortems already document what didn't work.

---

## Watchlist data flow — single source of truth

There is ONE `watchlist.json` that all consumers must agree on. After the 2026-05-26 incident, `src/watchlist.json` is a SYMLINK to `outputs/watchlist.json`. Do not break the symlink unless you intend to change the architecture.

```
                user_pinned.json         ── user pins via trade.html /pinned/<T> POST
                       │
                       ▼
   dynamic_factor_screener_v3.py  ── daily 09:00 UTC (rcg-screener-long.timer)
       │   merges: macro + pinned + TAM + top + cap_picks (capped at 120)
       │
       ├── writes /home/nixos/Prod/V1/outputs/watchlist.json   (the canonical file)
       │
       ├── SCPs to ndiaz@rcg-base:C:\Users\ndiaz\Dropbox\RCG_2020\watchlist.json
       │       │
       │       ▼
       │   bloomberg_prices.py (Windows) reads it, pulls BBG, SCPs JSON back
       │
       └── (via symlink) /home/nixos/Prod/V1/src/watchlist.json  ── consumed by:
                                                                       market_sentiment_bbg.py
                                                                       (factor_signals_bbg.json output)
```

**Invariant to monitor:** `factor_signals_bbg.json.watchlist` size should equal `bloomberg_prices.json.watchlist` size, within ~5 entries of slop. If they diverge by ≥20%, the symlink is broken or one consumer is reading a stale path.

Followup: kill the dual-file pattern entirely — see TODO "Consolidate watchlist files."

---

## Scheduler replay matrix — what catches up after downtime

After a reboot or extended outage, NOT EVERYTHING catches up. Different schedulers have different missed-fire semantics:

| Scheduler type | Replays missed fires? | What we run on it |
|---|---|---|
| **systemd `.timer` with `Persistent=true`** | ✅ Yes — fires at boot if it missed its window | All RCG `rcg-*` timers (bloomberg-pull, screener-long, sharadar-download, models-capture, predictions-capture, forward-returns, leaderboard, correlations, infra-health, infra-probe) |
| systemd `.timer` without `Persistent` | ❌ No — silent skip | (none currently — verify with `systemctl cat <unit>.timer \| grep Persistent` before deploying) |
| **`crontab`** | ❌ No — silent skip | `market_sentiment_bbg` (OK because intraday-frequent), jupyter self-heal (`*/5`). `markout_eval_publish` migrated off cron 2026-05-27 — now a Persistent=true timer. |
| `@reboot` cron | ✅ Once at boot | http server :8080 |
| user-systemd (linger enabled) | ✅ Yes | rcg-agent, rcg-infra-probe.timer, rcg-markout-publish.timer (daily 06:00 UTC), rcg-post-reboot-reconcile.timer (one-shot 25min after boot) |
| user-systemd (no linger) | ❌ No — needs SSH login | (none — linger is mandatory, see WSL2 section below) |

**Rule of thumb:** anything producing a daily artifact (markouts, EOD reports, models-train, weights-regen) MUST be a systemd timer with `Persistent=true`. Don't use cron for it.

**Companion concern — ordering:** When MULTIPLE timers all have `Persistent=true` and ALL missed fires during the outage, they ALL fire at boot — at roughly the same time. If one depends on data the other produces (e.g., screener-long reads SEP.parquet written by sharadar-download), the race is a real bug. Use `After=<other>.service` in the unit. We learned this the hard way 2026-05-26 — see incident.

### Post-reboot health-check one-liner

Drop into a fresh SSH session after a reboot/outage to confirm everything caught up:

```bash
loginctl show-user nixos | grep Linger   # must be Linger=yes
for f in /home/nixos/Prod/V1/outputs/{markouts,bloomberg_prices,factor_signals_bbg,long_screener_results,leaderboard,correlations}*.{json,csv}; do
    [ -e "$f" ] && stat -c '%y  %n' "$(readlink -f "$f")"
done | sort
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active rcg-agent.service
curl -s http://localhost:8085/status | python3 -m json.tool
```

Anything > 24h stale is a regression worth investigating.

---

## WSL2 user-bus quirk (post-reboot recovery)

rcg-nixos runs inside WSL2 (NixOS distro). When the WSL VM is reloaded (e.g. you log out + back into Windows, or `wsl --shutdown` + restart):

- **System services** (sentiment-refresh, screener-watchdog, bloomberg-pull timer, etc.) come up fine via systemd PID 1.
- **User services** (`rcg-agent`, `rcg-infra-probe.timer`) need `user@1000.service` to create its private bus socket at `/run/user/1000/systemd/`. Without that, the symptom is `systemctl --user ...` returning `Failed to connect to user scope bus via local transport: No such file or directory` — and the Slack agent silently never starts.

**Permanent fix (already applied 2026-05-26):** `sudo loginctl enable-linger nixos`. Verify with `loginctl show-user nixos | grep Linger` → must say `Linger=yes`. With linger enabled, the user manager properly initializes its bus on boot, so the agent + probe come up automatically.

**If it ever recurs (e.g. linger gets disabled, or a WSL update breaks it again):**
```bash
sudo systemctl restart user@1000.service           # rebuilds /run/user/1000/systemd/
sleep 3
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start rcg-agent.service
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start rcg-infra-probe.timer
sudo loginctl enable-linger nixos                  # re-arm the permanent fix
```

The screener side will be unaffected during the outage — `bloomberg_prices.json` keeps refreshing because the puller lives in system-systemd, not user-systemd. Only Slack DMs to the agent will appear to "vanish" because the agent process isn't running to receive them.
