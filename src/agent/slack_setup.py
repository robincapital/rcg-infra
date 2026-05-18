"""
slack_setup.py — one-shot Slack workspace setup for the RCG Agent.

Idempotent: safe to re-run. Does:
  1. Verifies bot + app tokens via auth.test
  2. Looks up the installer (you) + Ashley by email
  3. Lists existing channels
  4. Creates missing channels (#quant-research, #trading-risk, #portfolio-mgmt,
     #compliance, #infra-ops) with policy-doc-aligned topics
  5. Writes ~/.rcg_agent_config.json with everything the agent needs:
       bot_token / app_token paths, user IDs, channel IDs, policy doc path
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TOKENS_PATH = Path.home() / ".slack_tokens.json"
CONFIG_PATH = Path.home() / ".rcg_agent_config.json"
POLICY_PATH = Path("/home/nixos/Prod/V1/docs/rcg_policy.md")

ASHLEY_EMAIL = "aschott@robincapitalgroup.com"

CHANNELS = [
    # (name, topic, purpose, hat)
    ("quant-research",  "Quant research — signal design + IC analysis + backtests",
     "Quant hat: reads research data, builds signals, runs tournament + backtests. No deploy authority.", "quant"),
    ("trading-risk",    "Trading & risk — execution math + slippage + risk limits",
     "Trading/Risk hat: models execution + slippage + position-sizing math. No live order authority.", "trading_risk"),
    ("portfolio-mgmt",  "Portfolio management — sizing + capital allocation",
     "PM hat: portfolio construction + allocation between alpha sources. No capital movement authority.", "portfolio"),
    ("compliance",      "Compliance — policy review + audit",
     "Compliance hat: read-only across everything. References docs/rcg_policy.md. Veto authority on new vendors + execution wiring.", "compliance"),
    ("infra-ops",       "Infra/ops — deploys + git + systemd",
     "Infra hat: only hat allowed to push to main + restart services. Requires typed approval.", "infra"),
]


def call(method: str, token: str, **params):
    """Call Slack Web API. Returns dict."""
    url = f"https://slack.com/api/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    if not TOKENS_PATH.exists():
        print(f"FAIL: {TOKENS_PATH} missing. Run the token-paste step first.")
        sys.exit(1)
    tokens = json.loads(TOKENS_PATH.read_text())
    bot_token = tokens["bot_token"]
    app_token = tokens.get("app_token")

    # 1. Auth check
    # IMPORTANT: for a bot (xoxb-) token, auth.test returns the BOT's own
    # user_id — NOT the human installer's ID. We can't get the installer's
    # user_id from a bot token at all. The allowlist must be set explicitly
    # below (or via INSTALLER_USER_ID env var) or the agent will reject
    # everything including its real human owner. See v25.2 bugfix.
    print("=== 1. Verifying tokens ===")
    auth = call("auth.test", bot_token)
    if not auth.get("ok"):
        print(f"  FAIL: auth.test → {auth}")
        sys.exit(1)
    bot_user_id = auth["user_id"]   # this is the BOT's own user ID, not a human
    print(f"  ✓ bot installed in '{auth['team']}' as '{auth['user']}' (bot_uid={bot_user_id})")
    print(f"  ✓ bot_id={auth.get('bot_id')}  workspace_url={auth.get('url')}")
    bot_id = auth.get("bot_id")
    team_id = auth.get("team_id")

    # Human installer's ID — must be passed in via env var, never inferred
    # from the bot token. If missing, the agent has no allowed senders + will
    # reject all messages until the config is patched.
    import os as _os
    installer_user_id = _os.environ.get("INSTALLER_USER_ID")
    if not installer_user_id:
        print("  ⚠ INSTALLER_USER_ID env var not set — config will have empty allowlist")
        print("    Find your Slack user ID via /me commands or workspace settings,")
        print("    then re-run with: INSTALLER_USER_ID=U... python /tmp/slack_setup.py")

    # 2. Look up Ashley by email
    print(f"\n=== 2. Looking up Ashley ({ASHLEY_EMAIL}) ===")
    al = call("users.lookupByEmail", bot_token, email=ASHLEY_EMAIL)
    if al.get("ok"):
        ashley_user_id = al["user"]["id"]
        ashley_name = al["user"]["real_name"] or al["user"]["name"]
        print(f"  ✓ Ashley found: {ashley_name} (uid={ashley_user_id})")
    else:
        ashley_user_id = None
        ashley_name = None
        err = al.get("error")
        if err == "users_not_found":
            print(f"  ⚠ Ashley not in workspace yet — agent will use email only for escalation")
        else:
            print(f"  ⚠ lookup error: {err}")

    # 3. List existing channels
    print(f"\n=== 3. Listing existing channels ===")
    cl = call("conversations.list", bot_token, types="public_channel,private_channel", limit=200)
    if not cl.get("ok"):
        print(f"  FAIL: {cl}")
        sys.exit(1)
    existing = {c["name"]: c for c in cl["channels"]}
    print(f"  found {len(existing)} channels: {sorted(existing.keys())}")

    # 4. Create missing channels
    print(f"\n=== 4. Creating missing channels ===")
    channel_ids = {}
    for name, topic, purpose, hat in CHANNELS:
        if name in existing:
            cid = existing[name]["id"]
            print(f"  · #{name} already exists ({cid})")
            channel_ids[name] = cid
            continue
        cr = call("conversations.create", bot_token, name=name, is_private="false")
        if cr.get("ok"):
            cid = cr["channel"]["id"]
            print(f"  ✓ created #{name} ({cid})")
            channel_ids[name] = cid
            # Set topic + purpose
            tr = call("conversations.setTopic", bot_token, channel=cid, topic=topic)
            pr = call("conversations.setPurpose", bot_token, channel=cid, purpose=purpose)
            if not tr.get("ok"): print(f"     ⚠ setTopic: {tr.get('error')}")
            if not pr.get("ok"): print(f"     ⚠ setPurpose: {pr.get('error')}")
        else:
            print(f"  ✗ failed to create #{name}: {cr.get('error')}")

    # 5. Write agent config
    print(f"\n=== 5. Writing agent config ===")
    cfg = {
        "version": 1,
        "workspace_url": auth.get("url"),
        "team_id": team_id,
        "installer_user_id": installer_user_id,      # Nick — sole authorized sender for now
        "allowed_user_ids": [installer_user_id] if installer_user_id else [],
        "bot_user_id": bot_user_id,                  # the BOT's own user ID
        "bot_id": bot_id,
        "ashley_user_id": ashley_user_id,
        "ashley_email": ASHLEY_EMAIL,
        "channels": {
            name: {"id": channel_ids.get(name), "hat": hat, "topic": topic}
            for name, topic, _, hat in CHANNELS
        },
        "default_channel": "general",
        "policy_doc_path": str(POLICY_PATH),
        "tokens_path": str(TOKENS_PATH),
        "budget_per_task_usd": 5.0,
        "budget_per_day_usd": 50.0,
        "anthropic_model": "claude-sonnet-4-5",  # placeholder; set to actual model name on build
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)
    print(f"  ✓ wrote {CONFIG_PATH}")

    print("\n=== SETUP COMPLETE ===")
    print(f"Installer (you): {installer_user_id}")
    print(f"Ashley:          {ashley_user_id or 'NOT IN WORKSPACE YET'}")
    print(f"Channels ready:  {len(channel_ids)}/{len(CHANNELS)}")
    print(f"Config:          {CONFIG_PATH}")
    print(f"\nNext step: ping me in this conversation; Phase 1 agent build kicks off.")


if __name__ == "__main__":
    main()
