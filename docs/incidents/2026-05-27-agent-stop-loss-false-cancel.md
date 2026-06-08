---
date: 2026-05-27
severity: P2
category: production-tool
component: src/agent/personas.py (detect_verb / APPROVAL_VERBS)
status: resolved
summary: Slack agent silently dropped a user task because the message contained "stop loss" — verb detector matched "stop" as a cancel synonym
tags: [agent, verb-detection, false-positive, slack]
opened_by: nick
opened_at: 2026-05-27T12:06:00Z
resolved_at: 2026-05-27T12:10:00Z
---

# Incident — Slack agent false-positive cancel on "stop loss" mention

**Discovered:** 2026-05-27 ~12:06 UTC, when Nick sent a Quant Research task to the Slack agent that mentioned "stop loss" in the context of a future feature. Agent picked up the task, then immediately self-cancelled with "🛑 Cancelled. Task reset to IDLE." before any tool calls were made.
**Component:** `/home/nixos/Prod/V1/src/agent/personas.py`, `APPROVAL_VERBS["cancel"]` pattern list.
**Severity:** P2 — user-visible degradation but quickly self-evident from the Slack output. No data loss; no missed trades. User easily re-sent the request after the fix. Slightly worse than a normal P3 because the agent did this *silently* — no warning that "stop" was being interpreted as a cancel verb — which is the kind of unintuitive behavior that erodes trust.

---

## Timeline (UTC)

| Time | Event |
|---|---|
| 12:06:31 | User posts message in `#quant-research` channel containing the phrase "we will introduce a stop loss on a trade" |
| 12:06:31 | Agent picks up message, posts "🎩 Quant Research — picking this up..." |
| 12:06:31 | `detect_verb()` returns `"cancel"` because `re.search(r"(?<!\w)stop(?!\w)", message)` matches "stop" as a whole word in "stop loss" |
| 12:06:31 | Agent posts "🛑 Cancelled. Task reset to IDLE." and returns without making any API calls |
| 12:06 → 13:09 | User opens a separate Claude direct-chat to investigate. Conversation file `var/agent_conversations/quant-research-1779829591.725639.json` confirms: state=IDLE, 1 message, $0 cost — agent never reached the Anthropic API |
| 13:09 | Root cause identified: `APPROVAL_VERBS["cancel"]` contained `"stop"` as a standalone pattern |
| 13:10 | Patch applied: dropped `"stop"`; added more specific alternatives `"stop task"`, `"stop it"` |
| 13:10 | 8-case smoke test in `personas.detect_verb()` passes (including "stop loss" → None, "stop the agent" → None, "stop it" → cancel) |
| 13:11 | Agent restarted, Socket Mode reconnected |

## Root cause

`detect_verb()` does case-insensitive whole-word matching against `APPROVAL_VERBS`. The "cancel" verb's pattern list was:

```python
"cancel": ["cancel", "stop", "abort", "nope", "kill it", "cancel task"]
```

The pattern `"stop"` is a common word that legitimately appears in compound trading terminology — "stop loss", "stop order", "stop hunting", "stop run", "stop the trade", etc. Matching it standalone as a cancel verb produces false positives on any message that uses "stop" in its normal trading meaning.

The detector is technically working as designed (whole-word boundary check via `r"(?<!\w)stop(?!\w)"` correctly identifies "stop" as a separate token in "stop loss"). The design was wrong, not the implementation.

## Why it wasn't caught earlier

Three factors:

1. No automated test coverage for `detect_verb()`. The patterns are added by hand and tested by feel.
2. The agent has been running for weeks without anyone happening to type a trading term containing "stop" (most operational queries use "cancel" or "abort" naturally).
3. The cancel-and-go-quiet behavior is intentional for actual cancels, so the silent failure didn't trip any monitor.

## Fix

`APPROVAL_VERBS["cancel"]` updated to:

```python
"cancel": ["cancel", "abort", "nope", "kill it", "cancel task", "cancel it", "stop task", "stop it"]
```

Dropped the bare `"stop"`. Added more specific phrases (`"stop task"`, `"stop it"`) that preserve the natural cancellation intent without colliding with trading terminology.

Smoke-tested with 8 cases covering both positive and negative paths.

Agent restarted via `systemctl --user restart rcg-agent.service`; Socket Mode reconnected.

## Hardening followups

- **Unit tests for `detect_verb()`.** A 15-line `tests/test_verb_detection.py` with the 8 cases used in this fix's smoke test. Run on every commit that touches `personas.py`. Tracked in `docs/TODO.md`.
- **Surface verb detection in the agent's response.** When the agent detects a verb that would short-circuit normal handling (cancel, status, cost, override), it could echo back "_(interpreted as `cancel` based on the word "stop" in your message — reply 'continue' to override)_". Gives the user a chance to correct false positives instead of silent failure. Lower priority — needs UX design.
- **Audit the rest of `APPROVAL_VERBS` for similar ambiguity.** Patterns to suspect: `"approve"` (vs "approved the trade for client X"), `"deploy"` (less common in chat but possible), `"merge"` (the trading-meaning of "merger arbitrage"), `"cost"` (very common in trading chat — could trigger the cost-summary handler when user is just discussing transaction costs).

## Detection lessons

When a verb-detector pattern is a single short common word, default to suspicion. Compound phrases ("cancel task", "kill it") are safer than single words ("stop", "abort", "cost"). The agent should fail loudly, not silently — every verb match should be observable in the response so the user can spot mis-detections immediately.
