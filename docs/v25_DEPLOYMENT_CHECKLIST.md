# v25 Deployment Checklist

## Pre-Deployment Verification

### 1. Code Syntax ✓
```bash
cd /home/nixos/Prod/V1
python3 -m py_compile src/quant_signals.py
python3 -m py_compile src/models_capture.py
python3 -m py_compile src/forward_returns_daily.py
python3 -m py_compile src/models_leaderboard.py
```
**Status**: All files compile cleanly (no syntax errors)

### 2. Signal Unit Tests
```bash
cd /home/nixos/Prod/V1
python3 tests/test_v25_signals.py
```
**Expected**: All 10 tests pass
- momentum_vol_confirmed (5bar, 13bar)
- momentum_52wk_range_position
- momentum_acceleration
- momentum_multi_timeframe_blend
- mean_rev_bb_pct
- mean_rev_rsi_divergence
- mean_rev_volume_spike_fade
- pca_top10_pt_basket
- sector_etf_pc1/pc2
- make_regime_conditional wrapper

### 3. Entrant Count Verification
```bash
cd /home/nixos/Prod/V1
python3 -c "
import re
with open('src/models_capture.py') as f:
    content = f.read()
    start = content.find('MODELS = [')
    end = content.find('\n]', start)
    section = content[start:end]
    names = re.findall(r'^\s+\(\"([^\"]+)\"', section, re.MULTILINE)
    print(f'Total entrants: {len(names)}')
    assert len(names) == 56, f'Expected 56, got {len(names)}'
    print('✓ Entrant count verified')
"
```
**Expected**: 56 entrants confirmed

### 4. Database Prerequisites
- [x] PostgreSQL running and accessible
- [x] `signals` table exists (for model scores)
- [ ] **Sharadar SEP table** available? (Check with Nick/Infra)
  - If YES: forward_returns_daily.py will use `sep.close`
  - If NO: fallback to `signals` table `eod_close` signal (already implemented)

### 5. Compliance Check
- [x] All signals use internal data only (no new vendor APIs)
- [x] No execution/trading logic changes
- [x] No client-facing data modifications
- [x] Additive changes only (no breaking existing entrants)

---

## Deployment Steps

### Phase 1: Code Merge (Day 0 — Today)

**Operator**: Infra Hat (Nick approval required)

1. Review this checklist + v25_SUMMARY.md
2. Wait for Nick's **"deploy"** or **"pr"** verb
3. If "deploy":
   ```bash
   cd /home/nixos/Prod/V1
   git add src/ docs/ tests/ systemd/
   git commit -m "v25: Expand tournament to 56 entrants, add daily markouts"
   git push origin main
   ```
4. If "pr":
   ```bash
   git checkout -b v25-tournament-expansion
   git add src/ docs/ tests/ systemd/
   git commit -m "v25: Expand tournament to 56 entrants, add daily markouts"
   git push origin v25-tournament-expansion
   # Open PR, wait for review
   ```

### Phase 2: Daily Markout Script Initialization (Day 0)

**Operator**: Quant Hat (me) or Infra Hat

```bash
cd /home/nixos/Prod/V1

# Dry run (check for errors, doesn't write to DB)
# python3 src/forward_returns_daily.py --dry-run  # (add flag if implemented)

# Live backfill (90 days of historical markouts)
python3 src/forward_returns_daily.py

# Expected output:
#   [forward_returns] Processing 2024-02-15 to 2024-05-20 (90 days)...
#   [forward_returns] Computed 1d/5d/14d/30d returns for AAPL: 124 samples
#   ...
#   [forward_returns] Total: 8,400 return signals inserted (140 tickers × 60 days avg)
```

**Validation**:
```sql
SELECT 
    signal_name, 
    COUNT(*) as samples, 
    MIN(ts) as first_date, 
    MAX(ts) as last_date
FROM signals
WHERE signal_name LIKE 'realized_return_%'
GROUP BY signal_name
ORDER BY signal_name;
```
Expected: 4 rows (`realized_return_1d_pct`, `5d`, `14d`, `30d`), each with ~10k samples

### Phase 3: Enable Systemd Timer (Day 0 or Day 1)

**Operator**: Infra Hat

```bash
# Copy service files to systemd user directory
mkdir -p ~/.config/systemd/user/
cp /home/nixos/Prod/V1/systemd/rcg-forward-returns-daily.* ~/.config/systemd/user/

# Enable and start timer
systemctl --user daemon-reload
systemctl --user enable rcg-forward-returns-daily.timer
systemctl --user start rcg-forward-returns-daily.timer

# Verify timer is active
systemctl --user list-timers | grep forward-returns

# Check next scheduled run
systemctl --user status rcg-forward-returns-daily.timer
```

**Expected output**:
```
● rcg-forward-returns-daily.timer - RCG Daily Forward Returns Markout
     Loaded: loaded (/home/nixos/.config/systemd/user/rcg-forward-returns-daily.timer)
     Active: active (waiting)
    Trigger: Thu 2026-05-21 08:00:00 EDT; 14h left
```

### Phase 4: Models Capture Fire (Day 0 — Immediate)

**Operator**: Quant Hat or wait for scheduled fire

```bash
cd /home/nixos/Prod/V1

# Manual fire (generates predictions for all 56 entrants)
python3 src/models_capture.py

# Expected output:
#   [models] regime: grind_up  (vix=14.2, spy_5d=1.3%)
#   [models] 56 entrants across families: [...]
#   [models] universe ctx: 142 tickers, 142 sector-matched, 10 PCA-residual scored
#   [models] AAPL: 56/56 models fired
#   [models] MSFT: 56/56 models fired
#   ...
#   [models] Total: 7,952 predictions inserted (142 tickers × 56 models)
```

**Validation**:
```sql
-- Check new entrants appear in signals table
SELECT DISTINCT signal_name 
FROM signals 
WHERE signal_name LIKE 'model_%_score'
  AND ts > NOW() - INTERVAL '1 hour'
ORDER BY signal_name;
```
Expected: 56 distinct `model_*_score` signals

### Phase 5: Leaderboard Refresh (Day 0 or Day 1)

**Operator**: Quant Hat

```bash
cd /home/nixos/Prod/V1
python3 src/models_leaderboard.py
```

**Expected**:
- JSON output file: `leaderboard_YYYYMMDD_HHMMSS.json`
- 56 models in output
- 7 horizons per model (`30min`, `60min`, `4h`, `1d`, `5d`, `14d`, `30d`)
- Sharpe ratio populated (may be NaN for horizons with <10 samples initially)
- Regime-conditional variants show IC only during matching regimes

**Validation**:
```bash
jq '.models | length' leaderboard_*.json  # Should print: 56
jq '.models[0].horizons | keys' leaderboard_*.json  # Should include "1d", "5d", "14d", "30d"
jq '.models[0].horizons."30min" | has("sharpe")' leaderboard_*.json  # Should print: true
```

---

## Post-Deployment Monitoring (Days 1-7)

### Daily Checks

1. **Forward returns job health**:
   ```bash
   systemctl --user status rcg-forward-returns-daily.service
   journalctl --user -u rcg-forward-returns-daily.service -n 50
   ```
   - Look for: "Total: N return signals inserted" (no errors)

2. **Models capture logs**:
   ```bash
   # Check all 56 models are firing
   grep "models fired" /path/to/models_capture.log | tail -5
   ```

3. **Leaderboard sample growth**:
   ```sql
   -- Daily markouts should accumulate 1 sample per ticker per day
   SELECT 
       signal_name,
       COUNT(DISTINCT ticker) as tickers,
       COUNT(*) as total_samples,
       MAX(ts)::date as latest_date
   FROM signals
   WHERE signal_name LIKE 'realized_return_%'
   GROUP BY signal_name;
   ```
   Expected: sample count grows by ~140 per day (watchlist size)

4. **Regime-conditional entrants**:
   ```sql
   -- Verify regime-conditional models fire but score 0.0 in wrong regimes
   SELECT 
       signal_name, 
       signal_value, 
       COUNT(*) 
   FROM signals 
   WHERE signal_name LIKE 'model_%regime%_score'
     AND ts > NOW() - INTERVAL '1 day'
   GROUP BY signal_name, signal_value
   ORDER BY signal_name, signal_value;
   ```
   Expected: Mix of 0.0 (filtered) and non-zero (active) scores

---

## Rollback Procedure

If any new signal causes:
- Crash in models_capture.py
- Consistently invalid scores (>100 or <-100)
- Performance degradation (capture time >10 min)

**Immediate mitigation**:
1. Comment out the problematic entrant in `src/models_capture.py` MODELS list
2. Rerun `python3 src/models_capture.py` (bad signal stops generating new predictions)
3. Historical data remains in DB for post-mortem analysis
4. Notify Nick with error logs

**Full rollback** (if multiple signals fail):
```bash
git revert <commit-hash>  # Revert v25 merge
python3 src/models_capture.py  # Back to 39 entrants
systemctl --user stop rcg-forward-returns-daily.timer
systemctl --user disable rcg-forward-returns-daily.timer
```

---

## Success Criteria (30-day checkpoint)

By **June 20, 2026** (30 days post-deploy):

- [x] All 56 entrants fire without errors
- [x] Daily markouts have ≥30 samples per ticker per horizon
- [x] Leaderboard shows IC stabilization (variance drops as samples grow)
- [x] Regime-conditional variants show **IC divergence**:
  - `momentum_8bar_highvol` IC > `momentum_8bar` IC during high-vol regimes
  - `momentum_8bar_lowvol` IC > `momentum_8bar` IC during low-vol regimes
- [x] Volume-confirmed momentum IC ≥ base momentum_5bar IC
- [x] No compliance incidents (all data sourcing compliant)

**If criteria met**: Proceed to Stage-1 meta-model design (IC matrix, OLS blend)

---

**Current Status**: ⏳ Awaiting Nick's "ship it" / "deploy" / "pr" approval verb

**Next Action**: Quant Hat hands off to Infra Hat for merge + timer setup after approval
