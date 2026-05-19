#!/usr/bin/env bash
#
# screener_watchdog.sh — keep the live-price feed alive.
#
# Runs every 5 min during RTH (13:30-21:00 UTC = 9:30 AM - 5 PM ET).
# Checks if /home/nixos/Prod/V1/src/bloomberg_prices.json was last
# written within the last STALE_MINUTES (15). If not, restarts the
# refresh server and logs the recovery to /home/nixos/screener_watchdog.log.
#
# The refresh server (rcg-sentiment-refresh.service on port 8085) is the
# single point of failure for live prices — it pushes refresh requests
# to the Windows desktop. When it hangs (which it did May 18 → May 19,
# 26h silent dark), nothing prompts a recovery without this watchdog.
#
# Restart policy: at most 3 restarts per hour. If we hit 3 in an hour
# we stop trying and log a hard-failure marker so a human knows to look.
set -euo pipefail

PRICES_FILE=/home/nixos/Prod/V1/src/bloomberg_prices.json
LOG=/home/nixos/screener_watchdog.log
STALE_MINUTES=15
MAX_RESTARTS_PER_HOUR=3
RESTART_COUNTER=/tmp/screener_watchdog_restarts

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
    echo "$(date -u +%FT%TZ) HARD-FAIL: $restart_count restarts in last hour, refusing to retry. $PRICES_FILE age=${file_age_min}min. Manual intervention required." >> "$LOG"
    exit 2
fi

# ── Restart the refresh server ──────────────────────────────────────
echo "$(date -u +%FT%TZ) STALE: $PRICES_FILE is ${file_age_min}min old (threshold ${STALE_MINUTES}min). Restarting rcg-sentiment-refresh..." >> "$LOG"
if sudo -n systemctl restart rcg-sentiment-refresh.service; then
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
    else
        echo "$(date -u +%FT%TZ) WARN: refresh server restarted but /refresh still failed" >> "$LOG"
    fi
else
    echo "$(date -u +%FT%TZ) ERROR: systemctl restart failed" >> "$LOG"
    exit 3
fi
