"""
cost_tracker.py — Anthropic API spend tracking + budget enforcement.

Tracks per-task and per-day spend. Hard-stops the agent when caps are hit.
Logs every API call's cost to a JSONL file for audit.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

# Sonnet 4.5 pricing as of 2026-05 (per million tokens)
# Update when model/pricing changes
PRICING = {
    "claude-sonnet-4-5":               {"in": 3.0,  "out": 15.0},
    "claude-sonnet-4-5-20250929":      {"in": 3.0,  "out": 15.0},
    "claude-opus-4-5":                 {"in": 15.0, "out": 75.0},
    "claude-haiku-4-5":                {"in": 1.0,  "out": 5.0},
    "claude-haiku-4-5-20251001":       {"in": 1.0,  "out": 5.0},
    # Default fallback
    "_default":                        {"in": 3.0,  "out": 15.0},
}

LOG_PATH = Path("/home/nixos/Prod/V1/var/agent_cost_log.jsonl")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class CostTracker:
    """Thread-safe per-task + per-day spend tracking with hard caps."""

    def __init__(self, per_task_usd: float = 5.0, per_day_usd: float = 50.0):
        self.per_task_usd = per_task_usd
        self.per_day_usd  = per_day_usd
        self.current_task_usd = 0.0
        self._lock = threading.Lock()

    # ─── Cost computation ──────────────────────────────────────────────
    def cost_for_usage(self, model: str, in_tokens: int, out_tokens: int,
                       cache_read: int = 0, cache_create: int = 0) -> float:
        price = PRICING.get(model, PRICING["_default"])
        # Cache pricing per Anthropic's published rates:
        #   cache_create = 1.25x input price
        #   cache_read   = 0.1x input price
        in_cost     = (in_tokens / 1_000_000) * price["in"]
        out_cost    = (out_tokens / 1_000_000) * price["out"]
        cache_create_cost = (cache_create / 1_000_000) * price["in"] * 1.25
        cache_read_cost   = (cache_read / 1_000_000) * price["in"] * 0.1
        return in_cost + out_cost + cache_create_cost + cache_read_cost

    # ─── Record + check ────────────────────────────────────────────────
    def record(self, model: str, usage: Dict, task_id: str, hat: str) -> float:
        """Record one API call's spend. Returns the dollar amount."""
        cost = self.cost_for_usage(
            model,
            in_tokens=usage.get("input_tokens", 0),
            out_tokens=usage.get("output_tokens", 0),
            cache_read=usage.get("cache_read_input_tokens", 0),
            cache_create=usage.get("cache_creation_input_tokens", 0),
        )
        with self._lock:
            self.current_task_usd += cost
            log_entry = {
                "ts":          datetime.now(timezone.utc).isoformat(),
                "task_id":     task_id,
                "hat":         hat,
                "model":       model,
                "in_tokens":   usage.get("input_tokens", 0),
                "out_tokens":  usage.get("output_tokens", 0),
                "cache_read":  usage.get("cache_read_input_tokens", 0),
                "cache_create": usage.get("cache_creation_input_tokens", 0),
                "cost_usd":    round(cost, 6),
            }
            with open(LOG_PATH, "a") as fh:
                fh.write(json.dumps(log_entry) + "\n")
        return cost

    def reset_task(self) -> None:
        with self._lock:
            self.current_task_usd = 0.0

    def task_remaining(self) -> float:
        with self._lock:
            return max(0.0, self.per_task_usd - self.current_task_usd)

    def day_spent(self) -> float:
        """Sum today's cost from the log file (UTC day)."""
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        if not LOG_PATH.exists(): return 0.0
        for line in LOG_PATH.read_text().splitlines():
            try:
                e = json.loads(line)
                if e.get("ts", "").startswith(today):
                    total += e.get("cost_usd", 0)
            except Exception:
                continue
        return total

    def check_caps(self) -> tuple[bool, str]:
        """Returns (ok, reason). ok=False if we've hit a cap."""
        with self._lock:
            task_used = self.current_task_usd
        if task_used >= self.per_task_usd:
            return False, f"per-task cap hit: ${task_used:.2f} ≥ ${self.per_task_usd:.2f}"
        day_used = self.day_spent()
        if day_used >= self.per_day_usd:
            return False, f"per-day cap hit: ${day_used:.2f} ≥ ${self.per_day_usd:.2f}"
        return True, ""

    def summary(self) -> str:
        return (f"task: ${self.current_task_usd:.3f} / ${self.per_task_usd:.2f}"
                f"  ·  today: ${self.day_spent():.3f} / ${self.per_day_usd:.2f}")
