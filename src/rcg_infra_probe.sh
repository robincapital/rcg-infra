#!/usr/bin/env bash
#
# rcg_infra_probe.sh — rcg-nixos's view of upstream infra health.
# v29.14 (2026-05-21) — config-driven peers + multi-host heartbeat glob.
#
# Runs every 5 min, all hours. Two independent probes:
#
#   1. Tailscale liveness  — `tailscale ping --c 1` against critical peers.
#                            Catches Tailscale-layer drops BEFORE downstream
#                            symptoms (e.g. May 21 BBG-stale-16h incident).
#
#   2. Heartbeat freshness — Windows-side heartbeat file age. Tells us the
#                            Windows box has scripts running, can SCP via
#                            Tailscale — independent of Bloomberg Terminal.
#
# Each probe tracks consecutive failures (so a single 5min flap doesn't
# page) and alerts on threshold (default 2 consecutive fails) with 1/hr
# cooldown per failure-mode key. Posts a "recovered" message when it
# heals so the human knows the page is closed.
#
# This script does NOT try to repair anything — by design. The failure
# modes it watches (network upstream, peer machine off) are not repairable
# from this box. The purpose is differentiated DIAGNOSIS, so the alert
# tells the human exactly where to look.

set -uo pipefail   # no -e — keep running through probe failures

# --- Config -----------------------------------------------------------
LOG=/home/nixos/rcg_infra_probe.log
STATE_DIR=/home/nixos/.local/state/rcg-infra-probe
ALERT_COOLDOWN_SEC=3600
CONSECUTIVE_FAIL_THRESHOLD=2

# Critical Tailscale peers loaded from var/active_peers.conf (format below).
# Comment out a line in that file when a box is in a drawer / off the road.
PEERS_CONF=/home/nixos/Prod/V1/var/active_peers.conf
declare -a PEERS=()
if [ -r "$PEERS_CONF" ]; then
    while IFS= read -r line; do
        # strip comments + whitespace
        line="${line%%#*}"
        line="$(echo "$line" | tr -d '[:space:]')"
        [ -n "$line" ] && PEERS+=("$line")
    done < "$PEERS_CONF"
fi
# Hardcoded fallback if the conf file is missing — keeps the probe useful.
[ ${#PEERS[@]} -eq 0 ] && PEERS=("rcg-base:100.86.90.78")

# Multi-host heartbeats: each Windows box writes /var/heartbeat_<HOSTNAME>.txt.
# v29.14 — we monitor the AGGREGATE: alert only if NO heartbeat is fresh.
# That mirrors the BBG license model — only one box has BBG at a time, so
# at most one heartbeat will typically be fresh, and that's enough.
HEARTBEAT_GLOB="/home/nixos/Prod/V1/var/heartbeat_*.txt"
HEARTBEAT_STALE_MIN=60   # Bumped from 30: laptops legitimately sleep + roam.

SLACK_TOKENS=/home/nixos/.slack_tokens.json
SLACK_CHANNEL=C0B4HRTEJ3G    # #infra-ops

mkdir -p "$STATE_DIR"

# --- Helpers ----------------------------------------------------------
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

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

# Cooldown gate. Returns 0 if OK to alert (and stamps), 1 if cooled-down.
should_alert() {
    local key="$1"
    local stamp="${STATE_DIR}/alert_${key}.epoch"
    local now
    now=$(date +%s)
    local last=0
    [ -f "$stamp" ] && last=$(cat "$stamp" 2>/dev/null || echo 0)
    last=${last:-0}
    if [ "$last" -gt 0 ] && [ $((now - last)) -lt "$ALERT_COOLDOWN_SEC" ]; then
        return 1
    fi
    echo "$now" > "$stamp"
    return 0
}

# Track consecutive failures for a probe. If outcome=pass, reset and echo
# "recovered" iff we were previously at-or-above threshold, else "ok".
# If outcome=fail, increment and echo the new count.
record_probe() {
    local key="$1"
    local outcome="$2"
    local counter="${STATE_DIR}/fail_${key}.count"
    local n=0
    [ -f "$counter" ] && n=$(cat "$counter" 2>/dev/null || echo 0)
    n=${n:-0}
    if [ "$outcome" = "pass" ]; then
        if [ "$n" -ge "$CONSECUTIVE_FAIL_THRESHOLD" ]; then
            echo "0" > "$counter"
            echo "recovered"
        else
            echo "0" > "$counter"
            echo "ok"
        fi
    else
        n=$((n + 1))
        echo "$n" > "$counter"
        echo "$n"
    fi
}

# --- Probe 1: Tailscale peer liveness --------------------------------
for peer_spec in "${PEERS[@]}"; do
    name="${peer_spec%%:*}"
    ip="${peer_spec##*:}"
    if timeout 6 tailscale ping --c 1 "$ip" >/dev/null 2>&1; then
        result=$(record_probe "ts_${name}" pass)
        if [ "$result" = "recovered" ]; then
            log "TS-RECOVERED: $name ($ip) reachable again"
            slack_alert "✅ *Tailscale recovered* — \`${name}\` (\`${ip}\`) is reachable again."
        fi
    else
        n=$(record_probe "ts_${name}" fail)
        log "TS-FAIL: $name ($ip) unreachable (consecutive fail #$n)"
        if [ "$n" -ge "$CONSECUTIVE_FAIL_THRESHOLD" ] && should_alert "ts_${name}"; then
            slack_alert "🛰️ *Tailscale peer down* — \`${name}\` (\`${ip}\`) unreachable for ${n} consecutive probes (~$((n*5))min). Check Tailscale service + power on that box. Cooldown: 1h."
        fi
    fi
done

# --- Probe 2: Multi-host heartbeat freshness -------------------------
# Find the freshest heartbeat across all known Windows boxes. Alert only
# if NO box has checked in recently — at most one box is the active BBG
# host at a time, so a single fresh heartbeat means the pipeline is alive.
newest_age_min=99999
newest_host="(none)"
shopt -s nullglob
heartbeats=( $HEARTBEAT_GLOB )
shopt -u nullglob

for f in "${heartbeats[@]}"; do
    [ -f "$f" ] || continue
    age=$(( ( $(date +%s) - $(stat -c %Y "$f") ) / 60 ))
    if [ "$age" -lt "$newest_age_min" ]; then
        newest_age_min=$age
        newest_host=$(basename "$f" .txt | sed 's/^heartbeat_//')
    fi
done

if [ ${#heartbeats[@]} -eq 0 ]; then
    n=$(record_probe "heartbeat" fail)
    log "HEARTBEAT-MISSING: no heartbeat_*.txt files exist (consecutive fail #$n)"
    if [ "$n" -ge "$CONSECUTIVE_FAIL_THRESHOLD" ] && should_alert "heartbeat"; then
        slack_alert "💓 *Heartbeat missing* — no \`heartbeat_*.txt\` files have ever arrived in \`/home/nixos/Prod/V1/var/\`. Verify \`rcg_heartbeat.py\` is registered + running in Task Scheduler on at least one Windows box. Cooldown: 1h."
    fi
elif [ "$newest_age_min" -ge "$HEARTBEAT_STALE_MIN" ]; then
    n=$(record_probe "heartbeat" fail)
    log "HEARTBEAT-STALE: freshest=${newest_host} age=${newest_age_min}min (threshold ${HEARTBEAT_STALE_MIN}min, consecutive fail #$n)"
    if [ "$n" -ge "$CONSECUTIVE_FAIL_THRESHOLD" ] && should_alert "heartbeat"; then
        slack_alert "💓 *Heartbeat stale* — freshest heartbeat is from \`${newest_host}\` (*${newest_age_min} min old*, threshold ${HEARTBEAT_STALE_MIN}min). No active BBG-host box is currently writing. Check Task Scheduler + Tailscale on whichever box you intended as the BBG host. Cooldown: 1h."
    fi
else
    result=$(record_probe "heartbeat" pass)
    if [ "$result" = "recovered" ]; then
        log "HEARTBEAT-RECOVERED: freshest=${newest_host} age=${newest_age_min}min"
        slack_alert "✅ *Heartbeat recovered* — \`${newest_host}\` is checking in again (${newest_age_min}min old)."
    fi
fi

exit 0
