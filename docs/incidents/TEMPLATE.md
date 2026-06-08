---
date: 2026-MM-DD                # ISO date the incident was DISCOVERED (not necessarily occurred)
severity: P2                    # P0=data loss / regulatory exposure / >24h outage
                                # P1=user-visible outage or material degradation
                                # P2=quality degradation, no missed trades / no user complaint
                                # P3=internal only, caught by monitoring before any impact
category: production-tool       # production-tool | development | client | infra | security | compliance
component: <file or system>     # e.g. bloomberg_prices.py, rcg-agent.service, IB integration, etc.
status: open                    # open | investigating | resolved | wontfix
summary: <one line, ≤120 chars> # plain-English; this appears in Slack + INDEX
tags: [tag1, tag2]              # free-form, lowercase, hyphens. examples: bbg, post-reboot, cron, race
opened_by: <nick | agent | claude | other>
opened_at: 2026-MM-DDTHH:MM:SSZ # UTC ISO timestamp
resolved_at:                    # fill in when status flips to resolved
---

# Incident — <short descriptive title>

**Discovered:** <UTC datetime + who/how>
**Component:** <component path + brief role>
**Severity:** <P-level + 1-sentence justification>
**Resolution:** <1-sentence outcome if known, "TBD" if still open>

---

## Timeline (UTC)

| Time | Event |
|---|---|
| HH:MM | <event> |

## Root cause

<one or more paragraphs>

## Why it wasn't caught earlier

<what monitor / process should have surfaced this — and didn't — and why>

## Fix

<what was changed, where, by whom>

## Hardening followups

- [ ] <followup 1 — preferably referenced in `docs/TODO.md`>
- [ ] <followup 2>

## Detection lessons

<what we'd build / change to catch this category of issue faster next time>
