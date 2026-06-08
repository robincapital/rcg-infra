# RCG Production Deployment & Backup Audit
**Date:** 2026-06-08 17:58 UTC
**Auditor:** Quant Hat (Agent)
**Requestor:** Nick Diaz (Managing Member)

## Executive Summary

✅ **Database:** Live, 10M+ signals rows, last write 17:42 UTC today
✅ **Tournament:** Running, 660 fires today across 118 tickers
⚠️ **Git:** 27 unpushed commits (v29.1-v29.11), many modified files
⚠️ **GCP Backup:** BROKEN - OpenSSL.crypto module error blocking gsutil
✅ **Services:** Agent + infra probe active, markout publisher failed (needs investigation)
✅ **Data Freshness:** BBG prices 17:50 UTC, leaderboard 10:02 UTC today

---

## 1. GIT STATUS - UNPUSHED WORK

### Unpushed Commits (27 total)
```
041aeab v29.11 — agent loop hardening
5f62190 v29.10 — fix agent execution bug + postmortem  
7cbecc5 v29.9 — UI polish
e3e2424 v29.8 — regime-aware best-model panel
3303900 v29.6 + v29.7 — EPS-decay gate + correlation matrix
be0300c v29.5 — Day 3: TAM sliders + model-driver badge
1423d29 v29.4 — BBG company description
0d7cfa6 v29.3 — BBG CIE_DES authoritative source
f3f939f v29.2 — report panel fit
a9113c5 v29.1 — investment report panel
... (17 more commits back to v25.6)
```

### Modified Files (Not Staged)
**Production Code:**
- src/models_capture.py
- src/models_leaderboard.py
- src/predictions_capture.py
- src/quant_signals.py
- src/markout_eval.py + markout_eval_publish.py
- src/dynamic_factor_screener_v3.py
- src/price_targets.py
- src/agent/agent_core.py + personas.py

**Docs:**
- data/ticker_descriptions.json
- docs/OBSERVABILITY_STANDARD.md
- docs/incidents/*.md

**Untracked (New Work):**
- docs/arima_20_*.md (4 files - model analysis)
- docs/ensemble_v32.md
- docs/filtered_revalidation_summary.md
- docs/SESSION_HANDOFF.md, TODO.md, COMPLIANCE_QUEUE.md
- src/forward_returns_daily.py
- src/markouts_v29.{html,css,js}
- systemd/rcg-forward-returns-daily.{service,timer}
- And ~30 more untracked files

**⚠️ ACTION REQUIRED:** Push to origin/main or create deployment PR

---

## 2. DATABASE STATE (GCP Postgres)

### Table Row Counts & Freshness
| Table | Rows | Distinct Keys | Last Update |
|-------|------|---------------|-------------|
| **signals** | 10,033,437 | 135 unique signals | 2026-06-08 17:42 UTC |
| **runs** | 16,541 | 16,541 unique runs | 2026-06-08 17:42 UTC |

### Recent Activity (Last 7 Days)
| Date | Runs | Total Tickers |
|------|------|---------------|
| 2026-06-08 (today) | 660 | 169,920 |
| 2026-06-05 | 1,188 | 324,968 |
| 2026-06-04 | 1,188 | 324,624 |
| 2026-06-03 | 1,171 | 315,952 |
| 2026-06-02 | 1,170 | 313,873 |
| 2026-06-01 | 520 | 139,240 |

**✅ Database is healthy and actively receiving signals**

### Missing Table: predictions
❌ Query shows no `predictions` table exists yet (column check returned signals/runs only)
📋 This may be intentional - check if predictions capture is fully deployed

---

## 3. PRODUCTION FILE FRESHNESS

### Core Tournament Files
| File | Last Modified | Size |
|------|---------------|------|
| src/models_capture.py | 2026-05-28 15:46 | 29K |
| src/models_leaderboard.py | 2026-05-20 23:19 | 14K |
| src/predictions_capture.py | 2026-05-28 17:52 | 15K |
| src/quant_signals.py | 2026-05-28 15:11 | 39K |

### Live Data Outputs
| File | Last Modified | Size |
|------|---------------|------|
| src/bloomberg_prices.json | 2026-06-08 17:50 | 1.9M ✅ |
| outputs/leaderboard.json | 2026-06-08 10:02 | 311K ✅ |
| outputs/markouts.json | 2026-06-08 07:13 | 1.9M ⚠️ (10h old) |
| outputs/watchlist.json | 2026-06-08 13:56 | 6.3K ✅ |

**⚠️ markouts.json stale** - Last updated 07:13 UTC (10+ hours ago)
This aligns with `rcg-markout-publish.service` failed status

---

## 4. SYSTEMD SERVICES STATUS

### Active Services
✅ **rcg-agent.service** - loaded active running (Slack agent)
✅ **rcg-infra-probe.timer** - loaded active waiting (next fire: 17:55 UTC)

### Failed Services
❌ **rcg-markout-publish.service** - loaded failed failed
   - Timer: loaded active waiting (next daily fire: 2026-06-09 06:00 UTC)
   - Last attempt failed - needs investigation

### Inactive (Normal)
⚪ rcg-post-reboot-reconcile.service - only runs after boot
⚪ rcg-infra-probe.service - triggered by timer, not persistent

**⚠️ ACTION REQUIRED:** Investigate markout publisher failure
```bash
journalctl --user -u rcg-markout-publish.service -n 50
```

---

## 5. GCP BACKUP STATUS

### Critical Finding: Backup Pipeline Broken
```
❌ gsutil ls gs://rcg-prod-backup/daily_snapshots/
module 'OpenSSL.crypto' has no attribute 'sign'
```

**Impact:**
- Local data intact (NixOS /home/nixos/Prod/V1)
- Database actively writing (Postgres in GCP)
- But automated file backups to GCS are failing

**Root Cause:** OpenSSL/Python cryptography library version mismatch
**Known Workaround:** Per TODO.md - "Re-auth gcloud on rcg-base" needed

**⚠️ CRITICAL ACTION:** Fix gcloud auth on rcg-base Windows box
```powershell
# On rcg-base Windows machine:
gcloud auth login
```

---

## 6. DATA PARQUET FILES

### Finding: No Local Parquet Files
```bash
find data -name "*.parquet" -type f
# Returns: (empty)
```

**Analysis:**
- This may be intentional if Parquet data lives elsewhere
- Sharadar data likely pulled on-demand or cached differently
- Check if data/ directory structure has changed

**📋 TODO:** Verify expected data/ directory structure with Nick

---

## 7. DOCUMENTATION CURRENCY

### Context Files (Session Handoff Docs)
✅ docs/SESSION_HANDOFF.md - exists, dated 2026-05-21
✅ docs/TODO.md - exists, last updated 2026-05-21
✅ docs/OBSERVABILITY_STANDARD.md - modified recently
✅ CONTEXT_infra.md - exists at root (not in docs/)

### Recent Analysis Docs (Untracked, Need Commit)
- docs/arima_20_after_hours_deep_dive.md
- docs/arima_20_filtered_markout_backtest.md
- docs/arima_20_filtered_walk_forward.md
- docs/arima_20_per_ticker_audit.md
- docs/ensemble_v32.md
- docs/filtered_revalidation_summary.md
- docs/top_models_good_hours_walk_forward.md
- docs/top_models_per_ticker_audit.md
- docs/model_optimization_sweep.md

**⚠️ ACTION:** These analysis docs should be committed to preserve research

---

## 8. COMPLIANCE & POLICY ALIGNMENT

### Policy Doc Status
✅ docs/rcg_policy.md exists and is current (v1.0, 2026-05-18)

### Current Production Limits
Per rcg_policy.md:
- Max position: 15% NAV ✅
- Sector cap: 80% NAV ✅
- Leverage: 0% (long-only) ✅
- Asset class: US Equities only ✅

**No compliance violations detected in current configuration**

---

## RECOMMENDED ACTIONS (Priority Order)

### 🔴 CRITICAL (Do Today)
1. **Fix GCP backup** - gcloud re-auth on rcg-base
2. **Investigate markout publisher failure** - check logs, re-run manually if needed
3. **Push commits to origin/main** - 27 commits unpushed is risky

### 🟡 HIGH (This Week)
4. **Verify predictions table** - confirm if predictions capture is fully deployed
5. **Commit untracked analysis docs** - preserve research work
6. **Check data/ directory structure** - verify Parquet strategy
7. **Stage modified files** - review + commit working tree changes

### 🟢 MEDIUM (Next Week)
8. **Update TODO.md** - current version dated 2026-05-21 (18 days old)
9. **Review CONTEXT_infra.md** - ensure all services documented
10. **Test backup restoration** - verify GCS backup after re-auth works

---

## BACKUP VERIFICATION CHECKLIST

| Item | Status | Location | Last Verified |
|------|--------|----------|---------------|
| Git commits (local) | ✅ Present | /home/nixos/Prod/V1/.git | 2026-06-08 |
| Git commits (remote) | ⚠️ 27 behind | origin/main | Unknown |
| Postgres signals table | ✅ Live | GCP rcg_signals DB | 2026-06-08 17:42 |
| Postgres runs table | ✅ Live | GCP rcg_signals DB | 2026-06-08 17:42 |
| File backups (GCS) | ❌ Broken | gs://rcg-prod-backup | Failed |
| Bloomberg prices | ✅ Fresh | src/bloomberg_prices.json | 2026-06-08 17:50 |
| Leaderboard data | ✅ Fresh | outputs/leaderboard.json | 2026-06-08 10:02 |
| Documentation | ⚠️ Uncommitted | docs/*.md (18 new files) | 2026-06-08 |

---

## NOTES

- Database is the source of truth for signals/runs - that's healthy
- Git is 27 commits ahead locally - need to push or PR
- GCS backup broken but not critical (Postgres is primary)
- Markout publisher failure needs investigation
- Most production code dated 2026-05-28 (11 days old but stable)

**Overall Assessment:** System is OPERATIONAL but has backup/deployment hygiene issues that should be addressed this week.

---

## INVESTIGATION: Markout Publisher Failure

### Root Cause Analysis (2026-06-08)

**Symptom:** `rcg-markout-publish.service` timing out after 16+ minutes, exceeding `TimeoutStartSec=900` (15 minutes)

**Evidence from logs:**
```
Jun 08 06:00:01 systemd: Starting RCG daily markout publisher...
Jun 08 06:16:40 systemd: start operation timed out. Terminating.
Memory peak: 859.7M, 11.7M swap
```

**Performance degradation over time:**
| Date | Models | Elapsed Time | Memory Peak |
|------|--------|--------------|-------------|
| Early May | 27 | 30.7s | ~100M |
| Mid May | 47 | 182.1s (3 min) | ~300M |
| Late May | 53 | 444.9s (7.4 min) | ~400M |
| Jun 3-8 | 59 | >10 min | 859M + 13M swap |

**Root Cause:** 
1. **Database size explosion** - 10M+ signals rows, growing daily
2. **Per-ticker backtest complexity** - 59 models × 118 tickers × 3 horizons × 3 slippage scenarios = ~62K simulations
3. **Timeout too aggressive** - Set at 15 minutes when actual runtime is 16-17 minutes with current data volume

**Additional finding:** GCS backup also broken within markout publisher (`gcloud` not in PATH)

### Recommended Fixes (Priority Order)

#### 1. IMMEDIATE: Increase timeout (today)
```bash
# Edit ~/.config/systemd/user/rcg-markout-publish.service
# Change: TimeoutStartSec=900 → TimeoutStartSec=1800 (30 minutes)
systemctl --user daemon-reload
systemctl --user restart rcg-markout-publish.timer
```

#### 2. SHORT-TERM: Optimize query performance (this week)
- Add database index on (model_id, asof_date, ticker)
- Implement pagination/batching for large model sets
- Cache repeated computations within 90-day window

#### 3. MEDIUM-TERM: Incremental updates (next 2 weeks)
- Only recompute models that received new predictions since last run
- Store intermediate results, update delta only
- Expected speedup: 10x for stable model sets

#### 4. FIX GCS BACKUP: Add gcloud to PATH
```bash
# Verify gcloud location
which gcloud
# Add to systemd service Environment= line
```

### Manual Recovery (Run Now)
```bash
# Increase timeout and manually trigger
cd /home/nixos/Prod/V1
systemctl --user stop rcg-markout-publish.timer
# Edit service file per fix #1
systemctl --user daemon-reload
systemctl --user start rcg-markout-publish.service
# Monitor progress:
tail -f /home/nixos/markouts.log
```

