#!/usr/bin/env bash
#
# rcg-post-reboot-reconcile.sh — heal data-dependency races after a reboot.
# v29.16 (2026-05-27)
#
# Fires 25 min after boot via rcg-post-reboot-reconcile.timer. By then:
#   - sharadar-download.service has finished its post-boot catch-up
#     (it's a 2-3 min job that fires at OnBootSec=2min)
#   - rcg-screener-long.service may have ALSO finished its post-boot catch-up
#     in parallel, reading the OLD SEP.parquet → produced 1-day-stale
#     last_price in long_screener_results.csv. This is the 2026-05-26 bug.
#
# If we detect that long_screener_results.csv was written BEFORE SEP.parquet
# (i.e. it raced), trigger a fresh screener run so it picks up the new SEP.
#
# Pure user-systemd workaround until the underlying ordering can be fixed
# declaratively in /etc/nixos/claude-finance.nix (TODO).

set -uo pipefail

LOG=/home/nixos/post_reboot_reconcile.log
SEP_PATH=/var/sharadar/data/SEP.parquet
CSV_PATH=/home/nixos/Prod/V1/outputs/long_screener_results.csv

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# Both files exist?
if [ ! -f "$SEP_PATH" ]; then
    log "SKIP: $SEP_PATH missing (sharadar hasn't run yet?)"
    exit 0
fi
if [ ! -f "$CSV_PATH" ]; then
    log "SKIP: $CSV_PATH missing (screener hasn't run yet?)"
    exit 0
fi

sep_mtime=$(stat -c %Y "$SEP_PATH")
csv_mtime=$(stat -c %Y "$CSV_PATH")
sep_age_min=$(( ($(date +%s) - sep_mtime) / 60 ))
csv_age_min=$(( ($(date +%s) - csv_mtime) / 60 ))

# If SEP was written AFTER the CSV, the screener used stale data.
if [ "$sep_mtime" -gt "$csv_mtime" ]; then
    log "RACE-DETECTED: SEP.parquet (${sep_age_min}min old) is fresher than long_screener_results.csv (${csv_age_min}min). Re-firing screener-long..."
    if sudo systemctl restart rcg-screener-long.service ; then
        log "OK: rcg-screener-long restart triggered. Will read fresh SEP this time."
    else
        log "ERROR: systemctl restart failed (exit $?)"
        exit 1
    fi
else
    log "OK: long_screener_results.csv (${csv_age_min}min) ≥ SEP.parquet (${sep_age_min}min) — no race detected."
fi
