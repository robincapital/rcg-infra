"""tests/test_verb_detection.py — guard against APPROVAL_VERBS false positives.

Background: on 2026-05-27 the Slack agent silently self-cancelled a Quant
Research task because the user's message mentioned "stop loss" and the
APPROVAL_VERBS["cancel"] list contained "stop" as a standalone pattern.
See docs/incidents/2026-05-27-agent-stop-loss-false-cancel.md.

Rule: any single-word cancel/approve/etc. pattern that also appears in
legitimate trading terminology MUST be tested here. If a new pattern is
added that fails one of these cases, fix the pattern — not the test.

Run with:
    cd /home/nixos/Prod/V1
    /home/nixos/Prod/V1/var/agent_venv/bin/python -m pytest tests/test_verb_detection.py -v

Or as a smoke test from the repl:
    /home/nixos/Prod/V1/var/agent_venv/bin/python tests/test_verb_detection.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the agent package importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "agent"))

from personas import detect_verb  # noqa: E402


# (message_text, expected_verb_or_None, reason)
CASES = [
    # ── False-positive regressions (the actual 2026-05-27 bug) ──────────
    ("we will introduce a stop loss on a trade",
     None,
     "trading-terminology 'stop loss' must not match cancel"),
    ("set a stop order at 5% below entry",
     None,
     "trading-terminology 'stop order' must not match cancel"),
    ("are we close to stop hunting territory?",
     None,
     "trading-terminology 'stop hunting' must not match cancel"),
    ("stop the agent",
     None,
     "bare 'stop' alone is not a cancel signal (too ambiguous); "
     "use 'cancel' or 'stop task' / 'stop it' if you mean it"),
    ("please stop the bleeding on QBTS",
     None,
     "'stop the bleeding' is trading commentary, not a cancel verb"),

    # ── Cancel: legitimate cancels still work ─────────────────────────
    ("cancel", "cancel", "bare cancel"),
    ("cancel task", "cancel", "cancel task phrase"),
    ("cancel it", "cancel", "cancel it phrase"),
    ("abort", "cancel", "abort still works as cancel synonym"),
    ("nope", "cancel", "nope still works"),
    ("kill it", "cancel", "kill it still works"),
    ("stop task", "cancel", "stop task is intentional cancel phrase"),
    ("stop it", "cancel", "stop it is intentional cancel phrase"),

    # ── Other verbs: spot-check the rest of the table ─────────────────
    ("approve", "approve", "bare approve"),
    ("approved", "approve", "approved past tense"),
    ("ship it", "approve", "ship it phrase"),
    ("LGTM", "approve", "LGTM all caps"),
    ("👍", "approve", "thumbs-up emoji"),
    ("looks good", "approve", "looks good"),

    ("deploy", "deploy", "bare deploy"),
    ("ship to main", "deploy", "ship to main"),
    ("push to main", "deploy", "push to main"),

    ("open pr", "pr", "open pr"),
    ("pull request", "pr", "pull request"),

    ("status", "status", "bare status"),
    ("what's going on", "status", "what's going on"),
    ("where are we", "status", "where are we"),

    ("cost", "cost", "bare cost — known ambiguity but preserved for now"),
    ("spend", "cost", "spend"),
    ("budget", "cost", "budget"),

    ("override", "override", "bare override"),
    ("i acknowledge", "override", "i acknowledge phrase"),

    # ── Negative cases that should match NOTHING ──────────────────────
    ("how is the market today?", None, "neutral question"),
    ("can you check the markouts dashboard?", None, "task request"),
    ("what's the sharpe on arima_20?", None, "data question"),
    ("", None, "empty string"),
    ("   ", None, "whitespace only"),

    # ── Word-boundary protection (should NOT false-match) ─────────────
    ("approval-rate calculation", None,
     "'approve' must not match inside 'approval'"),
    ("stopwatch test", None,
     "'stop' must not match inside 'stopwatch' (also we removed bare stop)"),
    ("nopeak detected", None, "'nope' must not match inside 'nopeak'"),
]


def run_all() -> int:
    failures: list[str] = []
    for msg, expected, reason in CASES:
        got = detect_verb(msg)
        if got != expected:
            failures.append(
                f"  FAIL: detect_verb({msg!r}) → got {got!r}, expected {expected!r}\n"
                f"        reason: {reason}"
            )

    total = len(CASES)
    if failures:
        print(f"\n❌ {len(failures)} / {total} cases failed:\n")
        print("\n".join(failures))
        return 1
    print(f"\n✅ All {total} verb-detection cases pass.")
    return 0


# pytest-compatible test functions (so this can run under pytest too)
def test_no_false_positive_on_stop_loss():
    assert detect_verb("we will introduce a stop loss on a trade") is None


def test_legitimate_cancel_still_works():
    assert detect_verb("cancel") == "cancel"
    assert detect_verb("cancel task") == "cancel"
    assert detect_verb("stop task") == "cancel"


def test_word_boundaries_respected():
    assert detect_verb("approval-rate calculation") is None
    assert detect_verb("stopwatch test") is None


def test_all_cases():
    """Parametric-ish — runs every CASES entry as one assertion."""
    fails = []
    for msg, expected, reason in CASES:
        got = detect_verb(msg)
        if got != expected:
            fails.append(f"{msg!r}: got {got!r}, expected {expected!r} ({reason})")
    assert not fails, "\n".join(fails)


if __name__ == "__main__":
    sys.exit(run_all())
