"""
agent_core.py — Anthropic Messages API loop with tool use.

The heart of the agent. Given an inbound message, this:
  1. Loads the persona for the active hat (system prompt + allowed tools)
  2. Calls Anthropic with the conversation history
  3. If the model returns tool_use blocks, dispatches them through tool_wrapper
  4. Loops until the model is done (no more tool calls)
  5. Posts the final text response back via the Slack adapter callback
  6. Persists everything to conversation_state for resume

Each tool call streams a brief status message to Slack so the user can see
progress in real time.

Designed to be invoked by slack_adapter.on_message(). Returns nothing —
side effects happen via slack_post_fn callback and conversation_state.
"""
from __future__ import annotations

import os
import json
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

import anthropic

from cost_tracker import CostTracker
from conversation_state import ConversationState
from personas import build_system_prompt, get_allowed_tools, detect_verb, get_hat_display_name
from tool_wrapper import execute as tool_execute
from tool_wrapper import get_schemas_for_hat


MAX_TOOL_LOOPS = 30        # safety cap on the agentic loop per task
DEFAULT_MODEL  = "claude-sonnet-4-5"
MAX_TOKENS_OUT = 4096


def run_turn(
    hat:            str,
    state:          ConversationState,
    cost_tracker:   CostTracker,
    slack_post_fn:  Callable[[str], None],   # callback to post status / final message
    user_text:      str,
    config:         dict,
) -> None:
    """
    Run one full "turn" of the agent. A turn = one inbound user message → some
    amount of tool use → one final assistant response.

    Calls slack_post_fn() multiple times during the loop to surface progress.
    """
    model = config.get("anthropic_model") or DEFAULT_MODEL
    api_key = _load_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    # Detect approval-gate verbs
    verb = detect_verb(user_text)

    # Compose system prompt + tool schemas for this hat
    system_prompt = build_system_prompt(hat)
    allowed_tools = get_allowed_tools(hat)
    tool_schemas  = get_schemas_for_hat(allowed_tools)

    # Add the inbound message to state
    state.add_user_message(user_text)
    state.set_hat(hat)

    # Task lifecycle bookkeeping
    if state.state() == "IDLE":
        task_id = state.new_task()
        state.set_state("RESEARCHING")
        cost_tracker.reset_task()
    else:
        task_id = state.task_id() or state.new_task()

    # Handle special verbs that bypass the main loop
    if verb == "cancel":
        state.set_state("IDLE")
        slack_post_fn("🛑 Cancelled. Task reset to IDLE.")
        return
    if verb == "status":
        slack_post_fn(f"State: `{state.state()}`  ·  Hat: `{get_hat_display_name(hat)}`  ·  Cost: {cost_tracker.summary()}")
        return
    if verb == "cost":
        slack_post_fn(f"💰 {cost_tracker.summary()}")
        return

    # ── The main agentic loop ────────────────────────────────────────
    for loop_iter in range(MAX_TOOL_LOOPS):
        # Cost cap check before every API call
        ok, reason = cost_tracker.check_caps()
        if not ok:
            slack_post_fn(f"🛑 Budget cap hit: {reason}. Reply `override` to continue or `cancel` to stop.")
            state.set_state("IDLE")
            return

        # Call Anthropic
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS_OUT,
                system=system_prompt,
                tools=tool_schemas,
                messages=state.messages_for_api(),
            )
        except anthropic.APIError as e:
            slack_post_fn(f"⚠️ Anthropic API error: {e}")
            state.set_state("IDLE")
            return
        except Exception as e:
            slack_post_fn(f"⚠️ unexpected error calling Anthropic: {type(e).__name__}: {e}")
            state.set_state("IDLE")
            return

        # Record cost
        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else dict(response.usage.__dict__)
        cost = cost_tracker.record(model=model, usage=usage, task_id=task_id, hat=hat)
        state.add_cost(cost)

        # Persist the assistant message exactly as returned
        assistant_blocks = [_block_to_dict(b) for b in response.content]
        state.add_assistant_message(assistant_blocks)

        # Stop conditions
        if response.stop_reason == "end_turn":
            # Final answer — extract text and post
            text = _extract_text(response.content)
            if text:
                slack_post_fn(text)
            state.set_state("IDLE")
            return

        if response.stop_reason == "max_tokens":
            slack_post_fn("⚠️ hit max_tokens — response truncated. Reply to continue or `cancel`.")
            state.set_state("IDLE")
            return

        # Tool use phase
        if response.stop_reason != "tool_use":
            slack_post_fn(f"⚠️ unexpected stop_reason: {response.stop_reason}")
            state.set_state("IDLE")
            return

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            # Shouldn't happen — model said tool_use but no tool_use blocks
            slack_post_fn("⚠️ model returned tool_use stop without tool blocks. Stopping.")
            state.set_state("IDLE")
            return

        # Surface intermediate progress + execute each tool
        for tu in tool_uses:
            tname = tu.name
            tin = tu.input or {}
            # Brief status message
            label = _format_tool_call_label(tname, tin)
            slack_post_fn(f"🔧 `{tname}`: {label}")

            result = tool_execute(tname, tin, allowed_tools)
            # Truncate large outputs for the model context too (saves cost)
            if len(result) > 50_000:
                result = result[:50_000] + f"\n... (truncated, {len(result)-50_000} bytes more)"
            state.add_tool_result(tool_use_id=tu.id, result=result,
                                  is_error=result.startswith("ERROR") or result.startswith("REFUSED"))

        # Trim history if it's getting big (keeps cost in check on long sessions)
        if len(state.data["messages"]) > 60:
            state.trim_history(max_messages=40)

    # If we fall out of the loop without end_turn:
    slack_post_fn(f"⚠️ hit MAX_TOOL_LOOPS ({MAX_TOOL_LOOPS}) — stopping. Reply to continue if needed.")
    state.set_state("IDLE")


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def _load_api_key() -> str:
    """Read Anthropic API key from ~/.anthropic_api_key or env."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key: return key.strip()
    p = Path.home() / ".anthropic_api_key"
    if p.exists():
        return p.read_text().strip()
    raise RuntimeError("ANTHROPIC_API_KEY not set and ~/.anthropic_api_key missing")


def _block_to_dict(block) -> Dict:
    """Convert an Anthropic SDK content block to a JSON-serializable dict."""
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if isinstance(block, dict):
        return block
    # Fallback: pull common attrs
    out = {"type": getattr(block, "type", "unknown")}
    if hasattr(block, "text"): out["text"] = block.text
    if hasattr(block, "name"): out["name"] = block.name
    if hasattr(block, "input"): out["input"] = block.input
    if hasattr(block, "id"): out["id"] = block.id
    return out


def _extract_text(blocks) -> str:
    parts = []
    for b in blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
        elif isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _format_tool_call_label(tool_name: str, tool_input: dict) -> str:
    """One-line description of a tool call for Slack status."""
    if tool_name in ("read", "edit", "write"):
        return f"`{tool_input.get('path', '?')}`"
    if tool_name == "grep":
        return f"`{tool_input.get('pattern', '?')}`" + (f" in {tool_input.get('path','.')}" if tool_input.get('path') else "")
    if tool_name == "glob":
        return f"`{tool_input.get('pattern', '?')}`"
    if tool_name in ("bash", "ssh"):
        cmd = tool_input.get("command") or tool_input.get("remote_command", "?")
        return f"`{cmd[:120]}`"
    if tool_name == "git":
        return f"git {tool_input.get('subcommand', '?')}"
    if tool_name == "postgres_query":
        sql = tool_input.get("sql", "?")
        return f"`{sql[:120]}`"
    return json.dumps(tool_input)[:120]
