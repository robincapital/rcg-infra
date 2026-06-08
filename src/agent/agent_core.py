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


MAX_TOOL_LOOPS = 50         # v29.11 — exploratory sessions need 20-40
                            # iterations; 50 gives headroom without letting
                            # a true infinite loop run away.
MAX_DUPLICATE_TOOL_CALLS = 3  # Same (tool,input) 3x in a row → short-circuit.
TOOL_RESULT_CAP = 20_000    # v29.12 — was 50K (~12.5k tok). 20K (~5k tok)
                            # still fits real outputs (markouts.json head,
                            # postgres tables, big greps) without flooding
                            # the window every tool call. Hard cap as a
                            # belt-and-suspenders OOM guard sits in
                            # tool_wrapper._tool_bash at 50K bytes.
DEFAULT_MODEL  = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS_OUT = 16384   # Sonnet 4.5 supports up to 64k; 16k handles
                                  # comprehensive specs + multi-file diffs.


def _truncate_tool_result(s: str) -> str:
    """Cap tool output before it goes into the conversation state, so giant
    blobs don't bloat every subsequent API call. Token cost grows linearly
    with history; one verbose tool call shouldn't tax every turn after it."""
    if len(s) <= TOOL_RESULT_CAP:
        return s
    return s[:TOOL_RESULT_CAP] + f"\n... (truncated, {len(s) - TOOL_RESULT_CAP} bytes more)"


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
    max_tokens = config.get("max_tokens_out") or DEFAULT_MAX_TOKENS_OUT
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
    # v29.11 — track recent tool calls so we can short-circuit obvious
    # loops (e.g. the May 20 incident where the model issued essentially
    # the same `python3 << EOF` heredoc 30 times in a row, each time
    # exploring markouts.json slightly differently and hitting the cap).
    recent_tool_fingerprints: list[str] = []
    for loop_iter in range(MAX_TOOL_LOOPS):
        # Cost cap check before every API call
        ok, reason = cost_tracker.check_caps()
        if not ok:
            slack_post_fn(f"🛑 Budget cap hit: {reason}. Reply `override` to continue or `cancel` to stop.")
            state.set_state("IDLE")
            return

        # Call Anthropic.
        # v29.12 — prompt caching on system + tools. These are stable across
        # turns within a hat, so a `cache_control: ephemeral` marker on the
        # last system block + the last tool schema turns subsequent turns
        # into cache READS (0.1× input cost) instead of full re-billing.
        # On a typical 10-turn session this cuts input cost by ~50-70%.
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}
                ],
                tools=_with_tool_cache_marker(tool_schemas),
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
            # The model ran out of output budget. The response might contain
            # complete tool_use blocks that still need to be executed before
            # we can append a user message (otherwise we'd orphan them and
            # the next API call would error).
            partial_text = _extract_text(response.content)
            if partial_text:
                slack_post_fn(partial_text)

            # Execute any tool_use blocks that DID complete in this response
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            for tu in tool_uses:
                tname = tu.name
                tin = tu.input or {}
                label = _format_tool_call_label(tname, tin)
                slack_post_fn(f"🔧 `{tname}`: {label}")
                result = _truncate_tool_result(tool_execute(tname, tin, allowed_tools))
                state.add_tool_result(
                    tool_use_id=tu.id, result=result,
                    is_error=result.startswith("ERROR") or result.startswith("REFUSED"),
                )

            slack_post_fn(f"_(response was {max_tokens}-token capped — continuing in next iteration)_")
            # If no tool_use blocks fired, inject a continuation cue
            if not tool_uses:
                state.add_user_message(
                    "Your previous response hit max_tokens. Continue from where you "
                    "left off. Finish concisely without re-stating what's already above."
                )
            continue

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

            # v29.11 — Duplicate-call detection. Build a fingerprint from
            # tool name + first 200 chars of input. If we've issued the
            # same fingerprint N times in a row, short-circuit with a
            # coaching error instead of executing — this is almost always
            # the model stuck in a confused exploratory loop.
            fp = f"{tname}::{str(tin)[:200]}"
            recent_tool_fingerprints.append(fp)
            if len(recent_tool_fingerprints) > MAX_DUPLICATE_TOOL_CALLS:
                recent_tool_fingerprints = recent_tool_fingerprints[-MAX_DUPLICATE_TOOL_CALLS:]
            if (len(recent_tool_fingerprints) >= MAX_DUPLICATE_TOOL_CALLS
                    and len(set(recent_tool_fingerprints)) == 1):
                result = (
                    f"ERROR: identical tool call has fired "
                    f"{MAX_DUPLICATE_TOOL_CALLS} times in a row "
                    f"({tname} with same input). Stopping the loop. "
                    f"Change your approach: try a different tool, a "
                    f"different input, or summarize what you've learned "
                    f"and ask the user for direction."
                )
                slack_post_fn(f"⚠️ Same tool call fired {MAX_DUPLICATE_TOOL_CALLS}x — short-circuiting.")
                state.add_tool_result(tool_use_id=tu.id, result=result, is_error=True)
                continue

            result = _truncate_tool_result(tool_execute(tname, tin, allowed_tools))
            state.add_tool_result(tool_use_id=tu.id, result=result,
                                  is_error=result.startswith("ERROR") or result.startswith("REFUSED"))

        # Trim history if it's getting big (keeps cost in check on long sessions)
        if len(state.data["messages"]) > 60:
            state.trim_history(max_messages=40)

    # If we fall out of the loop without end_turn:
    # v29.11 — Include a useful summary so the user sees WHAT the agent
    # was doing instead of just "stopped." Last 5 tool calls + cost.
    last_calls = []
    try:
        for m in state.data["messages"][-12:]:
            if m.get("role") != "assistant": continue
            for b in m.get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    nm = b.get("name")
                    inp = b.get("input") or {}
                    label = _format_tool_call_label(nm, inp)
                    last_calls.append(f"`{nm}`: {label[:80]}")
    except Exception:
        pass
    summary = "\n".join("• " + c for c in last_calls[-5:]) if last_calls else "(no tool calls captured)"
    slack_post_fn(
        f"⚠️ hit MAX_TOOL_LOOPS ({MAX_TOOL_LOOPS}) — stopping.\n\n"
        f"Last 5 tool calls:\n{summary}\n\n"
        f"Reply with a more specific direction, or `continue` to keep going. "
        f"Cost so far: {cost_tracker.summary()}"
    )
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


def _with_tool_cache_marker(tools: List[Dict]) -> List[Dict]:
    """Mark the LAST tool schema with cache_control so the entire tools array
    becomes a cacheable prefix. Anthropic caches the prefix UP TO the
    cache_control marker, so a marker on the last tool covers them all."""
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


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
