"""
slack_adapter.py — Socket Mode listener for the RCG agent.

Listens for DMs + channel messages, authorizes by user_id (only Nick for
Phase 1), maps channel name → hat, then hands off to agent_core.run_turn().

Uses slack_sdk's SocketModeClient for the long-lived WebSocket; no public
HTTPS endpoint needed. All replies go back to the same channel + thread
the inbound message came from.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, Optional

from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from agent_core import run_turn
from conversation_state import ConversationState
from cost_tracker import CostTracker
from personas import get_hat_for_channel, get_hat_display_name


logger = logging.getLogger("rcg.agent.slack")
logger.setLevel(logging.INFO)


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────
class SlackAdapter:
    def __init__(self, config: dict, tokens: dict):
        self.config = config
        self.bot_token = tokens["bot_token"]
        self.app_token = tokens["app_token"]
        self.web = WebClient(token=self.bot_token)
        self.socket = SocketModeClient(app_token=self.app_token, web_client=self.web)

        # Per-conversation queue lock so two messages in flight on the
        # same thread don't race on the state file. Lock per (channel, thread).
        self._convo_locks: Dict[str, threading.Lock] = {}
        self._convo_locks_guard = threading.Lock()

        # Shared cost tracker for all conversations
        self.cost_tracker = CostTracker(
            per_task_usd=config.get("budget_per_task_usd", 5.0),
            per_day_usd=config.get("budget_per_day_usd", 50.0),
        )

        # Cache channel ID → name for hat lookup
        self.channel_name_cache: Dict[str, str] = {}
        self._build_channel_cache()

        # Authorized senders (just Nick for Phase 1)
        self.allowed_user_ids = set(config.get("allowed_user_ids", []))
        if not self.allowed_user_ids:
            raise RuntimeError("config.allowed_user_ids is empty — no one can talk to the bot")

        self.bot_user_id = config.get("bot_user_id")

    # ── Channel cache ─────────────────────────────────────────────────
    def _build_channel_cache(self) -> None:
        try:
            r = self.web.conversations_list(types="public_channel,private_channel", limit=200)
            for c in r.get("channels", []):
                self.channel_name_cache[c["id"]] = c["name"]
        except Exception as e:
            logger.warning(f"channel cache build failed: {e}")

    def _channel_name(self, channel_id: str) -> str:
        if channel_id not in self.channel_name_cache:
            try:
                r = self.web.conversations_info(channel=channel_id)
                self.channel_name_cache[channel_id] = r["channel"]["name"]
            except Exception:
                self.channel_name_cache[channel_id] = "__unknown__"
        return self.channel_name_cache[channel_id]

    # ── Posting back to Slack ─────────────────────────────────────────
    def post_message(self, channel: str, text: str, thread_ts: Optional[str] = None) -> None:
        """Post a message to Slack. Chunks if > 3000 chars (Slack limit ~4000)."""
        if not text: return
        try:
            # Chunk long messages
            if len(text) <= 3500:
                self.web.chat_postMessage(
                    channel=channel, text=text,
                    thread_ts=thread_ts,
                    mrkdwn=True,
                )
                return
            # Split on newlines preferentially
            chunks = []
            current = ""
            for line in text.splitlines(keepends=True):
                if len(current) + len(line) > 3500 and current:
                    chunks.append(current)
                    current = line
                else:
                    current += line
            if current: chunks.append(current)
            for i, chunk in enumerate(chunks):
                hdr = f"_(part {i+1}/{len(chunks)})_\n" if len(chunks) > 1 else ""
                self.web.chat_postMessage(
                    channel=channel, text=hdr + chunk,
                    thread_ts=thread_ts,
                    mrkdwn=True,
                )
        except Exception as e:
            logger.error(f"post_message failed: {e}")

    # ── Conversation locking ──────────────────────────────────────────
    def _convo_lock(self, channel: str, thread: str) -> threading.Lock:
        key = f"{channel}:{thread or 'root'}"
        with self._convo_locks_guard:
            if key not in self._convo_locks:
                self._convo_locks[key] = threading.Lock()
            return self._convo_locks[key]

    # ── Message handler ───────────────────────────────────────────────
    def handle_message_event(self, event: Dict) -> None:
        """Process one Slack message event."""
        # Ignore bot's own messages
        if event.get("bot_id"):
            return
        if event.get("user") == self.bot_user_id:
            return
        # Ignore subtype changes (edits, reactions, etc.)
        if event.get("subtype"):
            return
        user_id = event.get("user")
        channel = event.get("channel")
        text = event.get("text", "").strip()
        thread_ts = event.get("thread_ts") or event.get("ts")
        channel_type = event.get("channel_type")    # "im" for DM, "channel" for public, "group" for private

        if not user_id or not channel or not text:
            return

        # Auth check
        if user_id not in self.allowed_user_ids:
            logger.warning(f"unauthorized user {user_id} tried to message bot in {channel}")
            # Don't reply — silent reject avoids loops
            return

        # Determine hat
        if channel_type == "im":
            hat = "orchestrator"
            channel_name = "__dm__"
        else:
            channel_name = self._channel_name(channel)
            hat = get_hat_for_channel(channel_name)

        logger.info(f"[{channel_name}] {user_id}: {text[:80]} → hat={hat}")

        # Process with per-conversation lock
        lock = self._convo_lock(channel, thread_ts)

        def _run():
            with lock:
                try:
                    state = ConversationState(channel=channel_name, thread_ts=thread_ts)
                    # Send "thinking" reaction (best-effort, ignore failures)
                    try:
                        self.web.reactions_add(channel=channel, name="thinking_face",
                                               timestamp=event["ts"])
                    except Exception:
                        pass

                    def slack_post(t: str):
                        self.post_message(channel=channel, text=t,
                                          thread_ts=thread_ts if channel_type != "im" else None)

                    # Header so the user knows which hat picked it up
                    slack_post(f"_🎩 {get_hat_display_name(hat)} — picking this up..._")

                    run_turn(
                        hat=hat,
                        state=state,
                        cost_tracker=self.cost_tracker,
                        slack_post_fn=slack_post,
                        user_text=text,
                        config=self.config,
                    )
                except Exception as e:
                    logger.error(f"handle_message_event crashed: {e}\n{traceback.format_exc()}")
                    try:
                        self.post_message(channel=channel,
                                          text=f"❌ agent crashed: `{type(e).__name__}: {e}`",
                                          thread_ts=thread_ts if channel_type != "im" else None)
                    except Exception:
                        pass

        # Run in a thread so the Socket Mode loop stays responsive
        threading.Thread(target=_run, daemon=True).start()

    # ── Socket Mode dispatch ──────────────────────────────────────────
    def on_socket_request(self, client: SocketModeClient, req: SocketModeRequest) -> None:
        # Always ACK first so Slack doesn't retry
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        if req.type != "events_api":
            return
        event = req.payload.get("event", {})
        etype = event.get("type")
        if etype == "message":
            self.handle_message_event(event)
        elif etype == "app_mention":
            # @mentions in channels are also messages — treat the same
            self.handle_message_event(event)

    # ── Start ─────────────────────────────────────────────────────────
    def start(self) -> None:
        self.socket.socket_mode_request_listeners.append(self.on_socket_request)
        self.socket.connect()
        logger.info("⚡ Socket Mode connected — agent live")
        # Keep the process alive
        while True:
            time.sleep(60)


# ────────────────────────────────────────────────────────────────────────
# CLI entrypoint hook
# ────────────────────────────────────────────────────────────────────────
def load_tokens_and_config() -> tuple[dict, dict]:
    tokens_path = Path.home() / ".slack_tokens.json"
    config_path = Path.home() / ".rcg_agent_config.json"
    tokens = json.loads(tokens_path.read_text())
    config = json.loads(config_path.read_text())
    return tokens, config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s | %(message)s",
    )
    tokens, config = load_tokens_and_config()
    adapter = SlackAdapter(config=config, tokens=tokens)
    adapter.start()


if __name__ == "__main__":
    main()
