#!/usr/bin/env bash
#
# screener_watchdog.sh — keep the live-price feed alive.
#
# Runs every 5 min during RTH (13:00-21:30 UTC). Checks if
# /home/nixos/Prod/V1/src/bloomberg_prices.json was last written within
# the last STALE_MINUTES (15). If not, restarts the refresh server and
# logs the recovery to /home/nixos/screener_watchdog.log.
#
# The refresh server (rcg-sentiment-refresh.service on port 8085) is the
# single point of failure for live prices — it pushes refresh requests
# to the Windows desktop. As of v28.2 it also self-watchdogs via
# sd_notify, BUT this external watchdog stays as belt-and-suspenders:
# it checks the SYMPTOM (file freshness) rather than the mechanism.
#
# Restart policy: at most 3 restarts per hour. If we hit 3 in an hour
# we stop trying, log a HARD-FAIL marker, AND post a Slack alert to
# #infra-ops (v28.3 — closes the detection loop).
#
# Runs in two modes:
#   - root (via systemd unit rcg-screener-watchdog.service): no sudo
#   - nixos user (via crontab fallback): uses sudo -n (NOPASSWD)
set -euo pipefail

PRICES_FILE=/home/nixos/Prod/V1/src/bloomberg_prices.json
LOG=/home/nixos/screener_watchdog.log
STALE_MINUTES=15
MAX_RESTARTS_PER_HOUR=3
RESTART_COUNTER=/tmp/screener_watchdog_restarts
SLACK_TOKENS=/home/nixos/.slack_tokens.json
SLACK_CHANNEL=C0B4HRTEJ3G    # #infra-ops

# Conditional sudo prefix — empty when already root
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo -n"
fi

# ── Slack alert helper ─────────────────────────────────────────────
# Posts a message to #infra-ops via the agent's bot token. Best-effort:
# never fails the script if Slack is unreachable (we still want the
# local recovery actions to run).
slack_alert() {
    local msg="$1"
    # Token file is mode 600 nixos:users; if running as root via systemd,
    # we can still read it. If neither path works, silently no-op.
    if [ ! -r "$SLACK_TOKENS" ]; then
        # As root with restrictive perms, try with -u to see if we should
        # fallback. For now just no-op.
        return 0
    fi
    local token
    token=$(python3 -c "import json; print(json.load(open('$SLACK_TOKENS')).get('bot_token',''))" 2>/dev/null || true)
    if [ -z "$token" ]; then return 0; fi
    # Build payload safely with python (handles quotes/escapes in $msg)
    local payload
    payload=$(python3 -c "import json,sys; print(json.dumps({'channel':'$SLACK_CHANNEL','text':sys.argv[1]}))" "$msg" 2>/dev/null) || return 0
    curl -sf -m 10 \
         -H "Authorization: Bearer $token" \
         -H "Content-Type: application/json; charset=utf-8" \
         -d "$payload" \
         https://slack.com/api/chat.postMessage > /dev/null 2>&1 || true
}

# ── RTH gate ─────────────────────────────────────────────────────────
# Only watchdog during regular trading hours, weekdays.
# 13:30 UTC = 9:30 ET (EDT). DST drift: 1 hr difference in winter,
# which means we'd start watching at 8:30 ET in winter — that's fine
# (pre-market). Acceptable behavior.
hour_utc=$(date -u +%H)
mins_utc=$(date -u +%M)
dow=$(date -u +%u)   # 1=Mon, 7=Sun
mins_since_midnight=$((10#$hour_utc * 60 + 10#$mins_utc))

# RTH window in UTC (covers both EDT and EST):
#   EDT: 13:30-20:00 UTC (9:30-16:00 ET)
#   EST: 14:30-21:00 UTC (9:30-16:00 ET)
# Watch the broader 13:00-21:30 UTC window, weekdays only.
if [ "$dow" -gt 5 ]; then
    exit 0   # weekend, nothing to do
fi
if [ "$mins_since_midnight" -lt 780 ] || [ "$mins_since_midnight" -gt 1290 ]; then
    exit 0   # outside RTH window
fi

# ── Freshness check ─────────────────────────────────────────────────
if [ ! -f "$PRICES_FILE" ]; then
    echo "$(date -u +%FT%TZ) ERROR: $PRICES_FILE missing" >> "$LOG"
    exit 1
fi
file_age_sec=$(( $(date +%s) - $(stat -c %Y "$PRICES_FILE") ))
file_age_min=$(( file_age_sec / 60 ))

if [ "$file_age_min" -lt "$STALE_MINUTES" ]; then
    # Fresh enough — silent exit. (We don't log "OK" every 5 min;
    # that would balloon the log file. A daily summary cron could be
    # added later if useful.)
    exit 0
fi

# ── Stale: check restart budget ─────────────────────────────────────
now_epoch=$(date +%s)
restart_count=0
if [ -f "$RESTART_COUNTER" ]; then
    # Read recent restarts (lines = epoch timestamps), drop entries older than 1h
    while IFS= read -r ts; do
        if [ -n "$ts" ] && [ $((now_epoch - ts)) -lt 3600 ]; then
            restart_count=$((restart_count + 1))
        fi
    done < "$RESTART_COUNTER"
fi

if [ "$restart_count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    msg="🚨 *Screener HARD-FAIL* — $restart_count restarts in last hour, refusing to retry. \`bloomberg_prices.json\` is *${file_age_min} min stale*. Manual intervention required on rcg-nixos."
    echo "$(date -u +%FT%TZ) HARD-FAIL: $restart_count restarts in last hour, refusing to retry. $PRICES_FILE age=${file_age_min}min. Manual intervention required." >> "$LOG"
    slack_alert "$msg"
    exit 2
fi

# ── Restart the refresh server ──────────────────────────────────────
echo "$(date -u +%FT%TZ) STALE: $PRICES_FILE is ${file_age_min}min old (threshold ${STALE_MINUTES}min). Restarting rcg-sentiment-refresh..." >> "$LOG"
if $SUDO systemctl restart rcg-sentiment-refresh.service; then
    echo "$(date -u +%FT%TZ) OK: rcg-sentiment-refresh restarted" >> "$LOG"
    # Record the restart
    echo "$now_epoch" >> "$RESTART_COUNTER"
    # Trim the counter to only the last hour's worth (avoids unbounded growth)
    awk -v cutoff=$((now_epoch - 3600)) '$1 >= cutoff' "$RESTART_COUNTER" > "$RESTART_COUNTER.tmp"
    mv "$RESTART_COUNTER.tmp" "$RESTART_COUNTER"

    # Wait briefly + trigger a fresh pull so we don't wait 30min for the
    # next bloomberg-pull timer to fire.
    sleep 5
    if curl -sf -m 30 -o /dev/null "http://localhost:8085/refresh"; then
        echo "$(date -u +%FT%TZ) OK: immediate refresh triggered" >> "$LOG"
        slack_alert "⚠️ *Screener auto-recovered* — \`bloomberg_prices.json\` was ${file_age_min} min stale, sentiment-refresh restarted and \`/refresh\` re-triggered. Restart #${restart_count} this hour."
    else
        echo "$(date -u +%FT%TZ) WARN: refresh server restarted but /refresh still failed" >> "$LOG"
        slack_alert "⚠️ *Screener restart partial* — restarted sentiment-refresh but \`/refresh\` still failed. \`bloomberg_prices.json\` was ${file_age_min} min stale."
    fi
else
    echo "$(date -u +%FT%TZ) ERROR: systemctl restart failed" >> "$LOG"
    exit 3
fi
