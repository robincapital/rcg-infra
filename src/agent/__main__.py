"""
__main__.py — RCG agent entrypoint.

Loads tokens + config from disk, spins up the Slack adapter, blocks forever.
Run via systemd as user 'nixos' or for local testing:
    /home/nixos/Prod/V1/var/agent_venv/bin/python -m agent
"""
from slack_adapter import main

if __name__ == "__main__":
    main()
