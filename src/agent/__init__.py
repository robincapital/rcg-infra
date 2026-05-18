"""
src/agent/ — RCG Telegram/Slack agent (Phase 1)

Architecture (single brain, hat-switching by channel):

   Slack DM / channel
        │
        ▼
   slack_adapter   ── Socket Mode listener; routes by channel
        │
        ▼
   agent_core      ── Anthropic API loop with tool use
        │
        ├─► tool_wrapper      ── path-scoped read/edit/bash/etc
        ├─► personas          ── system prompt + tool scope per hat
        ├─► approval_gates    ── spec/build/deploy state machine
        ├─► conversation_state ── JSON-on-disk per (channel, thread)
        └─► cost_tracker      ── per-task + per-day budget enforcement

Phase 1 = ONE brain that adopts a different persona based on which Slack
channel the message came from. Phase 2 splits into separate bot identities.
"""
