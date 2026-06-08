---
date: 2026-05-20
severity: P1
category: production-tool
component: rcg-agent.service
status: resolved
summary: Slack agent could not execute commands (missing psycopg + bash PATH gap in user-systemd)
tags: [agent, slack, systemd, nixos]
opened_by: nick
opened_at: 2026-05-20T11:00:00Z
resolved_at: 2026-05-20T16:30:00Z
---
# Incident — Agent "Cannot Execute Commands" (May 19-20)

**Discovered:** 2026-05-20 morning, after the user reported the previous night's Slack agent session insisted it couldn't run code.
**Component:** `rcg-agent.service` (user-systemd) + `tool_wrapper.py`
**Severity:** Agent appeared functional (Slack messages flowed, hat-switching worked) but every postgres/bash tool call returned `[Errno 2] No such file or directory: 'bash'`. The agent then "explained" this to the user as a NixOS-dependency problem and asked them to fix it manually — looked like learned helplessness, actually was a real subprocess failure.
**Resolution:** Two fixes — `psycopg[binary]` into agent venv + explicit `PATH=` in the systemd unit.

---

## What happened

1. The user pinged `#quant-research` to continue a markout-dashboard build. The agent (correctly) tried `postgres_query` to inspect data.
2. `_tool_postgres_query()` does `import psycopg` first, falls back to `_tool_bash("psql ...")` if not available.
3. The agent's venv (`/home/nixos/Prod/V1/var/agent_venv/`) had `anthropic` + `slack_sdk` but **not `psycopg`**, so the fallback fired.
4. `_tool_bash()` calls `subprocess.run(["bash", "-lc", ...])`. NixOS user-systemd doesn't inherit the login-shell PATH, so `bash` (which lives at `/run/current-system/sw/bin/bash`) wasn't findable → `[Errno 2] No such file or directory: 'bash'`.
5. The error came back to the model. The model then composed an "I can't do this, please fix your environment" response and lost the rest of the session.

## Why it wasn't caught earlier

- This particular session was the first one trying `postgres_query` with non-trivial SQL after a recent restart (v25.x slack agent shipped + restarts after various deploys).
- The agent's other tools (`read`, `grep`, `glob`, `edit`, `write`) are pure-Python — they don't subprocess out. Worked fine.
- Bash + git + ssh tools also subprocess out, but mostly aren't used by the agent in normal flow; `postgres_query` is the only tool routinely needing subprocess fallback.

## Fix (v29.10)

1. **Install psycopg into the agent venv** (so the fallback isn't needed):
```
/home/nixos/Prod/V1/var/agent_venv/bin/pip install "psycopg[binary]"
```

2. **Add explicit PATH to the systemd unit** (so even if bash IS needed it works):
```
Environment="PATH=/run/current-system/sw/bin:/home/nixos/Prod/V1/var/agent_venv/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"
```

3. `systemctl --user daemon-reload && systemctl --user restart rcg-agent`

## Smoke test post-fix

```
PATH=...same as unit... python -c "
from tool_wrapper import _tool_postgres_query, _tool_bash
print(_tool_postgres_query(\"SELECT COUNT(*) FROM signals\"))   # → 26681 (real data)
print(_tool_bash('which bash && which psql'))
"
```
Both returned valid output.

## Hardening followups

- **Move user-systemd units to NixOS-declarative** (claude-finance.nix) so PATH is reproducibly set as part of the config. Same followup we deferred from the May 19 screener-watchdog incident.
- **Agent self-check on startup**: have `slack_adapter.py` import psycopg + subprocess.run("bash") in a try/except at boot; if either fails, log a hard error to journal + Slack alert. We'll know within minutes if a future deploy regresses this.
- **Tool error → Slack alert**: when any tool returns a stderr like "No such file" or "ImportError", surface to `#infra-ops` so the human notices before the agent spirals into helpless explanations.

## Detection lessons

The user only noticed because they were in an active session. The agent's behavior — saying "you need to fix this manually" — was the exact wrong response. A healthy agent on a tool failure should:
- Surface the actual error message (we do this)
- Try alternative tools (we don't yet)
- Alert the user something is structurally broken vs "follow these manual steps"

Cf. `docs/OBSERVABILITY_STANDARD.md` track/monitor/repair principle — this incident is a "track" failure (we couldn't see tool failures until a human looked at the conversation state file).
