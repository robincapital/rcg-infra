#!/usr/bin/env bash
#
# screener_watchdog.sh — keep the live-price feed alive.
# v29.12 (2026-05-21) — distinguishes upstream-down from local wedge,
#                       1-per-hour alert cooldown, less code.
#
# Runs every 5 min during RTH (13:00-21:30 UTC). Checks if
# /home/nixos/Prod/V1/src/bloomberg_prices.json was written within the
# last STALE_MINUTES. If stale, classifies the failure mode and acts:
#
#   - UPSTREAM-DOWN : TCP to the Windows BBG box (rcg-base:22) is
#     unreachable. Restarting our local refresh server CANNOT help.
#     Log + alert (1/hr cooldown), exit 0 (no systemd "failed" noise).
#
#   - LOCAL-WEDGE   : upstream is reachable but file is stale. Restart
#     rcg-sentiment-refresh + trigger /refresh. Budget MAX_RESTARTS_PER_HOUR.
#
#   - HARD-FAIL     : exhausted the restart budget while upstream stays up.
#     Means the local server is broken in a way restarts do not fix.
#     Alert (1/hr cooldown), exit 2.
#
# Exit codes:   0 = healthy or upstream-down (we did our job)
#               2 = HARD-FAIL after budget
#               3 = systemctl restart failed (internal error)
#               1 = prices file missing
#
# Runs in two modes:
#   - root (via systemd unit rcg-screener-watchdog.service): no sudo
#   - nixos user (via crontab fallback): uses sudo -n (NOPASSWD)

set -euo pipefail

# --- Config -----------------------------------------------------------
PRICES_FILE=/home/nixos/Prod/V1/src/bloomberg_prices.json
LOG=/home/nixos/screener_watchdog.log
STALE_MINUTES=15
MAX_RESTARTS_PER_HOUR=3
RESTART_COUNTER=/tmp/screener_watchdog_restarts
ALERT_STATE=/tmp/screener_watchdog_alerts
ALERT_COOLDOWN_SEC=3600    # 1 alert per hour per failure-mode key

UPSTREAM_HOST=100.86.90.78   # rcg-base (Windows / BBG terminal)
UPSTREAM_PORT=22

SLACK_TOKENS=/home/nixos/.slack_tokens.json
SLACK_CHANNEL=C0B4HRTEJ3G    # #infra-ops

if [ "${EUID:-$(id -u)}" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi

# --- Helpers ----------------------------------------------------------
log()  { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# Best-effort Slack post. Silently no-ops if token unreadable or curl fails.
slack_alert() {
    local msg="$1"
    [ ! -r "$SLACK_TOKENS" ] && return 0
    local token
    token=$(python3 -c "import json; print(json.load(open('$SLACK_TOKENS')).get('bot_token',''))" 2>/dev/null) || return 0
    [ -z "$token" ] && return 0
    local payload
    payload=$(python3 -c "import json,sys; print(json.dumps({'channel':'$SLACK_CHANNEL','text':sys.argv[1]}))" "$msg" 2>/dev/null) || return 0
    curl -sf -m 10 \
         -H "Authorization: Bearer $token" \
         -H "Content-Type: application/json; charset=utf-8" \
         -d "$payload" \
         https://slack.com/api/chat.postMessage >/dev/null 2>&1 || true
}

# Returns 0 if it's OK to alert for $1 (and stamps now); 1 if cooled-down.
should_alert() {
    local key="$1" now last
    now=$(date +%s)
    last=0
    if [ -f "$ALERT_STATE" ]; then
        last=$(awk -F= -v k="$key" '$1==k {print $2}' "$ALERT_STATE")
        last=${last:-0}
    fi
    if [ "$last" -gt 0 ] && [ $((now - last)) -lt "$ALERT_COOLDOWN_SEC" ]; then
        return 1
    fi
    # Rewrite state file: drop existing line for this key, append fresh stamp.
    {
        [ -f "$ALERT_STATE" ] && grep -v "^${key}=" "$ALERT_STATE" 2>/dev/null || true
        echo "${key}=${now}"
    } > "${ALERT_STATE}.tmp"
    mv "${ALERT_STATE}.tmp" "$ALERT_STATE"
    return 0
}

# Pure-TCP probe (no SSH auth involved) so we cannot confuse permission
# denial with network unreachability. Exit 0 = port open, else closed/timeout.
upstream_alive() {
    timeout 3 bash -c "</dev/tcp/${UPSTREAM_HOST}/${UPSTREAM_PORT}" 2>/dev/null
}

# Count restart events from the budget file that are inside the last hour.
recent_restart_count() {
    local now=$(date +%s) c=0
    [ -f "$RESTART_COUNTER" ] || { echo 0; return; }
    while IFS= read -r ts; do
        [ -n "$ts" ] && [ $((now - ts)) -lt 3600 ] && c=$((c + 1))
    done < "$RESTART_COUNTER"
    echo "$c"
}

# Trim the budget file to entries within the last hour.
trim_restart_counter() {
    local cutoff=$(( $(date +%s) - 3600 ))
    [ -f "$RESTART_COUNTER" ] || return 0
    awk -v cutoff="$cutoff" '$1 >= cutoff' "$RESTART_COUNTER" > "${RESTART_COUNTER}.tmp"
    mv "${RESTART_COUNTER}.tmp" "$RESTART_COUNTER"
}

# --- RTH gate ---------------------------------------------------------
# Watch the broader 13:00-21:30 UTC window, Mon-Fri.
hour_utc=$(date -u +%H)
mins_utc=$(date -u +%M)
dow=$(date -u +%u)
mins_since_midnight=$((10#$hour_utc * 60 + 10#$mins_utc))
[ "$dow" -gt 5 ] && exit 0
if [ "$mins_since_midnight" -lt 780 ] || [ "$mins_since_midnight" -gt 1290 ]; then
    exit 0
fi

# --- Freshness check --------------------------------------------------
if [ ! -f "$PRICES_FILE" ]; then
    log "ERROR: $PRICES_FILE missing"
    exit 1
fi
file_age_min=$(( ( $(date +%s) - $(stat -c %Y "$PRICES_FILE") ) / 60 ))

if [ "$file_age_min" -lt "$STALE_MINUTES" ]; then
    exit 0   # silent — file is fresh
fi

# --- Classify failure mode -------------------------------------------
# If we can't even reach the upstream box on TCP, no amount of restarting
# our local server will help. Don't burn the restart budget; alert once
# per hour and exit clean.
if ! upstream_alive; then
    log "UPSTREAM-DOWN: TCP ${UPSTREAM_HOST}:${UPSTREAM_PORT} unreachable. File age=${file_age_min}min. Local restart skipped."
    if should_alert "upstream_down"; then
        slack_alert "🛰️ *Screener UPSTREAM-DOWN* — TCP \`${UPSTREAM_HOST}:${UPSTREAM_PORT}\` (rcg-base / Windows BBG box) unreachable. \`bloomberg_prices.json\` is *${file_age_min} min stale*. Check Tailscale + OpenSSH on rcg-base. Local refresh service intentionally NOT restarted (would not help). Cooldown: 1h."
    fi
    exit 0
fi

# Upstream is reachable — this is a local-side problem. Check budget.
restart_count=$(recent_restart_count)
if [ "$restart_count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    log "HARD-FAIL: ${restart_count} restarts in last hour, refusing to retry. File age=${file_age_min}min."
    if should_alert "hard_fail"; then
        slack_alert "🚨 *Screener HARD-FAIL* — ${restart_count} restarts in last hour but \`bloomberg_prices.json\` is still *${file_age_min} min stale* (upstream IS reachable). Local sentiment-refresh is wedged in a way restarts do not fix. Investigate on rcg-nixos. Cooldown: 1h."
    fi
    exit 2
fi

# --- Restart local refresh server + trigger an immediate /refresh ----
log "STALE: file is ${file_age_min}min old (threshold ${STALE_MINUTES}min). Restarting rcg-sentiment-refresh..."
if ! $SUDO systemctl restart rcg-sentiment-refresh.service; then
    log "ERROR: systemctl restart failed"
    exit 3
fi
log "OK: rcg-sentiment-refresh restarted"
echo "$(date +%s)" >> "$RESTART_COUNTER"
trim_restart_counter

sleep 5
if curl -sf -m 30 -o /dev/null "http://localhost:8085/refresh"; then
    log "OK: immediate refresh triggered"
    slack_alert "⚠️ *Screener auto-recovered* — \`bloomberg_prices.json\` was ${file_age_min} min stale, sentiment-refresh restarted + \`/refresh\` re-triggered. Restart #${restart_count} this hour."
else
    log "WARN: refresh server restarted but /refresh still failed"
    slack_alert "⚠️ *Screener restart partial* — restarted sentiment-refresh but \`/refresh\` still failed. \`bloomberg_prices.json\` was ${file_age_min} min stale."
fi
