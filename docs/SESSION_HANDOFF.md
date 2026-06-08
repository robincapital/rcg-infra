# Session handoff — Multi-host BBG migration (2026-05-21)

> **For next-Claude:** Read this doc end-to-end before doing anything. It captures the state of an in-progress migration and the specific pending work. The user is **Nick Diaz** (MM at RCG). He has standing **blanket approval** to execute autonomously without asking permission for individual commands. The current session was on his desktop; the next session is on his laptop, hence this handoff.

---

## TL;DR — what we're doing and what's left

**Goal:** Make the Bloomberg price-pull "follow" wherever BBG is logged in across multiple Windows boxes (currently rcg-base at the office; soon-to-be the laptop for summer travel). Architecture: install the puller on every Windows box, each fails-fast if BBG isn't local, the one with BBG writes via atomic-rename SCP to rcg-nixos. Bloomberg's "one active session per user" license rule guarantees exactly one box succeeds at a time — no discovery logic needed.

**Shipped this session (live on rcg-base + rcg-nixos):**
- Multi-host BBG puller v29.14 (`bloomberg_prices.py`) — fail-fast clean exit when no local BBG, atomic SCP via `.tmp.<HOSTNAME>` → ssh-mv
- Per-host heartbeat v29.14 (`rcg_heartbeat.py`) — writes `heartbeat_<HOSTNAME>.txt` instead of single fixed path
- NixOS-side infra probe v29.14 (`rcg_infra_probe.sh`) — config-driven peers via `var/active_peers.conf`, globs `heartbeat_*.txt`, aggregate "is at least one box alive?" check
- Step-by-step doc `docs/LAPTOP_SETUP.md` for onboarding the laptop

**Pending tonight (laptop-side install):**
Follow `LAPTOP_SETUP.md` (same folder as this handoff). 9 steps, ~45 min:
1. Install BBG Terminal + log in (will kick rcg-base's session — expected)
2. Install Python + `pip install blpapi`
3. Generate SSH key, add public key to `nixos@rcg-nixos:~/.ssh/authorized_keys`
4. Copy `bloomberg_prices.py` + `rcg_heartbeat.py` from `Dropbox/RCG_2020/laptop_onboarding/` → `C:\Users\ndiaz\Downloads\`
5. Sanity-test both scripts manually
6. Register Task Scheduler entries (`schtasks /Create ...`) — exact commands in the doc
7. Edit `/home/nixos/Prod/V1/var/active_peers.conf` to uncomment the rcg-laptop line
8. Trigger the probe + verify both heartbeat files appear in NixOS `var/`
9. (Optional) Test BBG-host failover by logging into BBG on laptop while watching `bloomberg_prices.json` mtime

**One pre-existing followup we surfaced** (NOT caused by our changes, but flagged in TODO): `gcloud auth login` needed on rcg-base — GCS backup is failing with "Reauthentication failed". SCP to NixOS still works, so dashboards are fine; only the durable GCS archive is behind.

---

## Where to find supporting context

The Slack agent / RCG repo lives at `/home/nixos/Prod/V1/` on rcg-nixos. From the laptop you reach it via Tailscale (`ssh nixos@rcg-nixos` or `ssh nixos@100.78.59.48`).

**Read order for picking up the thread:**

| Path on rcg-nixos | What's in it |
|---|---|
| `CONTEXT_infra.md` | **Read first.** Stack diagram, symptom→file index, current versions of every component, agent token-consumption knobs, operational principles. Updated through v29.14. |
| `docs/LAPTOP_SETUP.md` | The 9-step procedural doc you'll follow tonight. Identical to the copy in this Dropbox folder. |
| `docs/OBSERVABILITY_STANDARD.md` | The "Track / Monitor / Repair" doctrine + the new "classify failure mode before applying a repair" sub-principle + "consecutive-fail vs budget" debouncing pattern reference. |
| `docs/TODO.md` | Live work list. "Completed Archive" section logs v29.10 → v29.14. "Infra Hardening Followups" lists open items (the laptop install is at the top). |
| `docs/incidents/2026-05-21-bbg-tailscale-blip.md` | The postmortem that birthed all this work. 16-hour BBG-stale incident from this morning. |
| `docs/incidents/2026-05-20-agent-no-bash.md` | The day before's postmortem — psycopg + PATH bug in the Slack agent. |
| `CONTEXT_price_targets.md`, `CONTEXT_signal_capture.md` | Older context, unrelated to this migration but the canonical refs for math + signal pipeline. |

**On the desktop (you may need to copy these too if the laptop will edit them):**

| Path | What |
|---|---|
| `C:\Users\ndiaz\Downloads\bloomberg_prices.py` | Patched v29.14 — already pre-staged in this Dropbox folder |
| `C:\Users\ndiaz\Downloads\rcg_heartbeat.py` | Patched v29.14 — same |

---

## Architecture decisions made this session (so next-Claude doesn't re-litigate)

**Q: Cloud-migrate to GCP for the BBG pull?**
A: No. Bloomberg Terminal is Windows-desktop-only software and `blpapi` only talks to the LOCAL BBG terminal on the same machine. GCP cannot host BBG. The bottleneck is "which Windows box has BBG logged in right now", not the home infra. GCP-eligible alternatives are B-PIPE ($15k+/mo, requires CCO sign-off) and BBG Anywhere (no programmatic API).

**Q: Active-host discovery from NixOS, or redundant pullers on each box?**
A: Redundant pullers. Bloomberg's "one active session per user" rule does the discovery for us — at most one box can succeed per fire. Redundant pullers are strictly simpler than building/maintaining a discovery probe.

**Q: How to debounce alerts — restart budget (watchdog style) or consecutive-fail count (probe style)?**
A: Both, depending on what the probe DOES. The watchdog (`screener_watchdog.sh`) uses a restart budget because each fire takes a real recovery action. The infra probe (`rcg_infra_probe.sh`) uses consecutive-fail count because it's read-only — no point penalizing a budget for transient flaps. Documented in `OBSERVABILITY_STANDARD.md` under "Pattern reference: consecutive-fail debouncing".

**Q: Single shared heartbeat file or per-host files?**
A: Per-host files (`heartbeat_<HOSTNAME>.txt`). Per-host gives diagnostic visibility into which boxes are currently online; shared would be last-writer-wins and lose that signal. The probe aggregates via glob — alerts only if NO box has fresh heartbeat.

**Q: How to handle a box going into a drawer for weeks?**
A: Config-driven peers via `var/active_peers.conf`. Comment out the box's line, the probe silently stops monitoring it. No code change required.

---

## Current versions (as of session end)

```
Agent (Slack):                v29.12  /home/nixos/Prod/V1/src/agent/agent_core.py
Screener watchdog:            v29.12  /home/nixos/Prod/V1/src/screener_watchdog.sh
Infra probe:                  v29.14  /home/nixos/Prod/V1/src/rcg_infra_probe.sh
BBG puller (Windows):         v29.14  C:\Users\ndiaz\Downloads\bloomberg_prices.py
Heartbeat (Windows):          v29.14  C:\Users\ndiaz\Downloads\rcg_heartbeat.py
```

---

## Live state snapshot (rcg-nixos, captured 18:38 UTC 2026-05-21)

```
/home/nixos/Prod/V1/var/heartbeat_DESKTOP-2L2313O.txt   60 B, mtime 18:35
/home/nixos/Prod/V1/src/bloomberg_prices.json       559,258 B, mtime 18:36
/home/nixos/Prod/V1/var/active_peers.conf              465 B
  contents:
    rcg-base:100.86.90.78
    # rcg-laptop:100.87.212.98     # ← uncomment when laptop online
```

After tonight, you should see TWO `heartbeat_*.txt` files (the desktop + the laptop) and one uncommented line in `active_peers.conf`.

---

## Resume prompt for the next session

Paste this verbatim at the top of the new Claude session on the laptop to bootstrap context:

```
I'm continuing a session from earlier today. Read the handoff doc at
C:\Users\ndiaz\Dropbox\RCG_2020\laptop_onboarding\SESSION_HANDOFF.md
end-to-end before doing anything — it captures the full architecture
context, what's already shipped, and what's pending.

Standing context that applies across sessions:
- I have blanket approval to execute autonomously — don't ask
  permission for individual commands during this work.
- My production stack runs at /home/nixos/Prod/V1/ on rcg-nixos
  (Tailscale IP 100.78.59.48, accessible from anywhere on my tailnet).
- The handoff doc lists three independent access paths to all context
  files (Dropbox, ssh, http) — use whichever is fastest.

Tonight's task: walk through LAPTOP_SETUP.md (same folder as the
handoff) to onboard this laptop as a second BBG-host box. Don't
re-design the architecture — the design decisions are documented in
the handoff, just execute the 9 steps.

Confirm you've read the handoff before starting.
```

That ensures the new session has the same mental model we built up here, without me having to re-litigate the GCP-vs-NixOS question or the multi-host pattern reasoning.

---

## Things to NOT do in the next session (lessons from this one)

- **Don't add an "active host discovery probe."** The license model is the discovery mechanism. Redundant pullers with fail-fast is the right pattern.
- **Don't put new systemd units in `/etc/systemd/system/`.** It's a read-only Nix-store symlink farm on this NixOS. Use `~/.config/systemd/user/` for new units until we get the declarative migration done.
- **Don't use `set -e` plus inline `local var1=$1 var2=...$var1...` in bash scripts.** Some bash versions treat the second var as unbound during the same `local` line. Split into separate `local` statements.
- **Don't migrate the BBG pull to GCP.** It cannot run there per Bloomberg's license. See "Architecture decisions" above.
- **Don't re-enable `markout_publish_simple.py`.** It was disabled 2026-05-20 because it wrote placeholder data over the real markouts.json. Use `markout_eval_publish.py` (the cron at 06:00 UTC).
