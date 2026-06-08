#!/usr/bin/env python3
"""post_incident_to_slack.py — broadcast a postmortem to #rcg-postmortems.

Parses YAML front-matter from a postmortem markdown file and posts a
structured summary to Slack with a permalink back to the full file
(served via the local HTTP server at :8080 / outputs/incidents/<file>).

Usage:
    post_incident_to_slack.py docs/incidents/2026-05-26-foo.md

Exit codes:
    0  posted OK
    1  file missing or unparseable
    2  Slack API failure (network / scope)
    3  channel not joined (one-time setup: have a human invite the bot)

Standing policy reference: docs/OBSERVABILITY_STANDARD.md "Postmortem Policy".
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

CHANNEL_ID = "C0B673DJB0E"       # #rcg-postmortems
TOKENS = Path("/home/nixos/.slack_tokens.json")

# Where the file can be reached over Tailscale (rcg-nixos HTTP server :8080
# serves /home/nixos/Prod/V1/outputs/). We mirror incidents there so the
# Slack link is clickable from any browser on the tailnet.
HTTP_BASE = "http://rcg-nixos:8080/incidents"


def parse_front_matter(path: Path) -> dict | None:
    """Extract the YAML front-matter at the top of a postmortem .md file.
    Front-matter starts with `---` on line 1 and ends with `---`. Simple
    parser — no PyYAML dep needed because we control the format."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    block = text[4:end]
    out: dict = {}
    for line in block.splitlines():
        line = line.split("#", 1)[0].rstrip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        # Strip inline trailing comments after a value that didn't get caught
        # because they had no leading space (rare but possible).
        if v.startswith("[") and v.endswith("]"):
            v = [t.strip() for t in v[1:-1].split(",") if t.strip()]
        out[k] = v
    return out


def slack_post(token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.load(resp)


SEVERITY_ICON = {
    "P0": ":rotating_light:",
    "P1": ":warning:",
    "P2": ":large_yellow_circle:",
    "P3": ":information_source:",
}
STATUS_ICON = {
    "open":          ":red_circle:",
    "investigating": ":large_orange_circle:",
    "resolved":      ":large_green_circle:",
    "wontfix":       ":black_circle:",
}


def build_blocks(meta: dict, slug: str) -> list[dict]:
    sev = (meta.get("severity") or "P?").upper()
    cat = meta.get("category", "uncategorized")
    comp = meta.get("component", "?")
    status = meta.get("status", "open")
    summary = meta.get("summary", "(no summary in front-matter)")
    date = meta.get("date", "?")
    opened_by = meta.get("opened_by", "?")
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags_str = " ".join(f"`{t}`" for t in tags) if tags else "_none_"

    title = f"{SEVERITY_ICON.get(sev,':grey_question:')} *{sev}* — {summary}"
    detail = (
        f"*Date:* {date}   *Status:* {STATUS_ICON.get(status,':grey_question:')} {status}\n"
        f"*Category:* `{cat}`   *Component:* `{comp}`   *Opened by:* {opened_by}\n"
        f"*Tags:* {tags_str}"
    )
    link = f"{HTTP_BASE}/{slug}.md"
    footer = f":link: full postmortem: <{link}|{slug}.md> · :file_folder: `docs/incidents/{slug}.md`"

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": title}},
        {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
        {"type": "divider"},
    ]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("file", help="Path to docs/incidents/YYYY-MM-DD-<slug>.md")
    p.add_argument("--dry-run", action="store_true", help="Print the payload, don't actually post")
    args = p.parse_args()

    path = Path(args.file).resolve()
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        return 1

    meta = parse_front_matter(path)
    if meta is None:
        print(f"ERROR: no YAML front-matter found in {path}. "
              f"Add a `---`-delimited block at the top — see "
              f"docs/incidents/TEMPLATE.md", file=sys.stderr)
        return 1

    slug = path.stem
    blocks = build_blocks(meta, slug)
    text_fallback = f"[{meta.get('severity','?')}] {meta.get('summary','(no summary)')}"

    if args.dry_run:
        print(json.dumps({"channel": CHANNEL_ID, "text": text_fallback,
                          "blocks": blocks}, indent=2))
        return 0

    if not TOKENS.exists():
        print(f"ERROR: {TOKENS} missing — can't authenticate", file=sys.stderr)
        return 2
    token = json.loads(TOKENS.read_text()).get("bot_token", "").strip()
    if not token:
        print("ERROR: no bot_token in slack_tokens.json", file=sys.stderr)
        return 2

    try:
        r = slack_post(token, {"channel": CHANNEL_ID, "text": text_fallback,
                                "blocks": blocks, "unfurl_links": False})
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)
        return 2

    if not r.get("ok"):
        err = r.get("error", "unknown")
        if err == "not_in_channel":
            print(f"ERROR: bot is not a member of channel {CHANNEL_ID}. "
                  f"Invite @rcg_agent to #rcg-postmortems (one-time setup).", file=sys.stderr)
            return 3
        print(f"slack error: {err} (full: {r})", file=sys.stderr)
        return 2

    print(f"posted: ts={r.get('ts')}  channel={r.get('channel')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
