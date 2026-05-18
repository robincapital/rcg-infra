"""
conversation_state.py — per-conversation persistence on disk.

One JSON file per Slack thread (or DM). Captures:
  · Message history (for Anthropic API continuity)
  · Current hat
  · Current state (IDLE / RESEARCHING / SPEC_PENDING / BUILDING / etc.)
  · Pending spec text (when waiting for approval)
  · Cost accumulator
  · Last-message timestamp (for cleanup)

Files live at /home/nixos/Prod/V1/var/agent_conversations/<channel>-<thread>.json.
Threads are durable — agent can resume across restarts.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path("/home/nixos/Prod/V1/var/agent_conversations")
STATE_DIR.mkdir(parents=True, exist_ok=True)


# Valid states — see approval_gates.py
STATES = (
    "IDLE",
    "RESEARCHING",
    "SPEC_PENDING_APPROVAL",
    "BUILDING",
    "VERIFICATION_PENDING_APPROVAL",
    "DEPLOYING",
    "PR_OPENED",
    "DONE",
)


class ConversationState:
    """Disk-backed conversation state for one Slack channel+thread."""

    def __init__(self, channel: str, thread_ts: str):
        self.channel = channel
        # Slack uses the parent message's ts as the thread root; if missing
        # (top-level DM), we use a sentinel "root"
        self.thread_ts = thread_ts or "root"
        self.path = STATE_DIR / f"{channel}-{self.thread_ts}.json"
        self._lock = threading.Lock()
        self.data = self._load()
        # Repair any orphaned tool_use blocks left by prior crashes / aborts.
        # Anthropic rejects messages arrays where a tool_use has no matching
        # tool_result in the next message. We synthesize stub error results
        # for orphans so the conversation can continue.
        n_repairs = self._repair_orphaned_tool_uses()
        if n_repairs > 0:
            self.save()

    # ─── Persistence ──────────────────────────────────────────────────
    def _load(self) -> Dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return {
            "channel":     self.channel,
            "thread_ts":   self.thread_ts,
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "updated_at":  datetime.now(timezone.utc).isoformat(),
            "hat":         "orchestrator",
            "state":       "IDLE",
            "messages":    [],          # Anthropic-format messages
            "pending_spec": None,       # text of the spec awaiting approval
            "task_id":     None,        # current task identifier
            "task_count":  0,           # tasks completed in this thread
            "total_cost_usd": 0.0,
        }

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.path.write_text(json.dumps(self.data, indent=2))

    # ─── State machine ────────────────────────────────────────────────
    def set_state(self, new_state: str) -> None:
        if new_state not in STATES:
            raise ValueError(f"invalid state: {new_state}")
        with self._lock:
            self.data["state"] = new_state
        self.save()

    def state(self) -> str:
        return self.data["state"]

    def set_hat(self, hat: str) -> None:
        with self._lock:
            self.data["hat"] = hat
        self.save()

    def hat(self) -> str:
        return self.data["hat"]

    # ─── Message log (Anthropic-format) ───────────────────────────────
    def add_user_message(self, text: str, slack_user_id: str = None) -> None:
        """Append a user message to the conversation."""
        with self._lock:
            self.data["messages"].append({
                "role": "user",
                "content": [{"type": "text", "text": text}],
                # Sidecar metadata for audit (Anthropic API ignores extra keys)
                "_meta": {"slack_user": slack_user_id, "ts": datetime.now(timezone.utc).isoformat()},
            })
        self.save()

    def add_assistant_message(self, content_blocks: List[Dict]) -> None:
        """Append an assistant message (model output) — full content blocks
        including any tool_use entries."""
        with self._lock:
            self.data["messages"].append({
                "role": "assistant",
                "content": content_blocks,
                "_meta": {"ts": datetime.now(timezone.utc).isoformat()},
            })
        self.save()

    def add_tool_result(self, tool_use_id: str, result: str, is_error: bool = False) -> None:
        """Append a tool result. Goes inside a user message with tool_result block."""
        with self._lock:
            self.data["messages"].append({
                "role": "user",
                "content": [{
                    "type":         "tool_result",
                    "tool_use_id":  tool_use_id,
                    "content":      result,
                    "is_error":     is_error,
                }],
                "_meta": {"ts": datetime.now(timezone.utc).isoformat()},
            })
        self.save()

    def messages_for_api(self) -> List[Dict]:
        """Return messages stripped of _meta sidecar, ready for the API."""
        out = []
        for m in self.data["messages"]:
            out.append({k: v for k, v in m.items() if not k.startswith("_")})
        return out

    # ─── Bookkeeping ──────────────────────────────────────────────────
    def set_pending_spec(self, spec_text: Optional[str]) -> None:
        with self._lock:
            self.data["pending_spec"] = spec_text
        self.save()

    def pending_spec(self) -> Optional[str]:
        return self.data.get("pending_spec")

    def new_task(self) -> str:
        """Start a new task. Returns task_id."""
        with self._lock:
            self.data["task_count"] += 1
            task_id = f"{self.channel}-{self.thread_ts}-{self.data['task_count']}"
            self.data["task_id"] = task_id
        self.save()
        return task_id

    def task_id(self) -> Optional[str]:
        return self.data.get("task_id")

    def add_cost(self, dollars: float) -> None:
        with self._lock:
            self.data["total_cost_usd"] = self.data.get("total_cost_usd", 0) + dollars
        self.save()

    def trim_history(self, max_messages: int = 40) -> None:
        """Trim old messages so the Anthropic API context doesn't balloon.
        Always keeps the most recent N messages, never strips mid-tool-call."""
        with self._lock:
            msgs = self.data["messages"]
            if len(msgs) <= max_messages: return
            # Keep last N, but never start with a tool_result (orphan)
            trimmed = msgs[-max_messages:]
            while trimmed and self._is_tool_result(trimmed[0]):
                trimmed.pop(0)
            self.data["messages"] = trimmed
        self.save()

    @staticmethod
    def _is_tool_result(msg: Dict) -> bool:
        if msg.get("role") != "user": return False
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
        return False

    def _repair_orphaned_tool_uses(self) -> int:
        """
        Scan the message history for assistant tool_use blocks that lack a
        corresponding tool_result in the immediately-following user message.
        Inserts stub tool_result blocks for each orphan so the Anthropic API
        accepts the messages array. Returns the number of repairs made.

        Called automatically on every conversation load. Idempotent — running
        twice is a no-op.
        """
        msgs = self.data.get("messages", [])
        n_repairs = 0
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            if msg.get("role") != "assistant":
                i += 1
                continue
            # Find tool_use ids in this assistant message
            tool_use_ids = []
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_use_ids.append(block.get("id"))
            if not tool_use_ids:
                i += 1
                continue
            # Check the next message for matching tool_results
            next_msg = msgs[i + 1] if i + 1 < len(msgs) else None
            result_ids = set()
            if next_msg and next_msg.get("role") == "user":
                for block in next_msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result_ids.add(block.get("tool_use_id"))
            missing = [tid for tid in tool_use_ids if tid not in result_ids]
            if missing:
                # Build a stub user message with synthetic tool_results
                stub_blocks = [
                    {
                        "type":         "tool_result",
                        "tool_use_id":  tid,
                        "content":      "ERROR: prior tool call was aborted (agent restart, max_tokens, or crash). Continuing without this result.",
                        "is_error":     True,
                    }
                    for tid in missing
                ]
                if next_msg and next_msg.get("role") == "user":
                    # Augment existing user message with stub results
                    existing_content = next_msg.get("content", [])
                    if not isinstance(existing_content, list):
                        existing_content = [{"type": "text", "text": str(existing_content)}]
                    next_msg["content"] = stub_blocks + existing_content
                    n_repairs += len(missing)
                else:
                    # Insert a brand-new user message after the orphan
                    repair_msg = {
                        "role":    "user",
                        "content": stub_blocks,
                        "_meta":   {"repair": True,
                                    "ts":     datetime.now(timezone.utc).isoformat()},
                    }
                    msgs.insert(i + 1, repair_msg)
                    n_repairs += len(missing)
            i += 1
        self.data["messages"] = msgs
        return n_repairs
