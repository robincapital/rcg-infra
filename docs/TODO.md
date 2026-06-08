# RCG Alpha Engine — TODO List

**Last updated:** 2026-05-21  
**Maintained by:** Trading & Risk Hat (agent-assisted)  
**Owner:** Nick Diaz (Managing Member)  

---

## Active (In Progress This Week)

| Item | Assigned | Target | Blocker |
|------|----------|--------|---------|
| Stage 1 meta-model gate hit (needs 7 days data, currently 6) | Quant Hat | May 22 | None — 1 trading day away |
| 5-min data frequency spec review | Trading Hat | — | **Compliance: Bloomberg ToS verification required** |
| IB execution API planning | Trading Hat | — | **Compliance: Execution phase not approved — blocked until July+** |

---

## Queued for Compliance Review

### 1. Bloomberg News API — 5-Min Data Frequency Upgrade

**Request Date:** 2026-05-21  
**Requestor:** Nick Diaz (Managing Member)  
**Reviewed By:** Trading & Risk Hat (agent)  
**Status:** ⏸️ Awaiting CCO vendor due-diligence sign-off  

**Summary:**
- Upgrade from 30-min → 5-min intraday data frequency for screener, trading signals, markouts
- Current: 118 tickers × hourly bars, 30-min refresh cadence
- Proposed: 118 tickers × 5-min bars, 5-min refresh cadence (85 fires/day vs. current 18)
- **Compliance Question:** Does Bloomberg Terminal ToS allow programmatic extraction of 5-min bars for internal signal generation?

**Documentation:**
- Full spec: `/home/nixos/Prod/V1/docs/5min_frequency_spec.md`
- Cost-benefit analysis: ~5× Postgres writes, +10% estimated IC uplift for 5min/10min horizons
- Recommendation: Defer full rollout; ship Path A (limited scope) for validation first

**Action Required:**
- [ ] **Nick:** Email Bloomberg Account Manager to confirm ToS compliance
- [ ] **CCO (Ashley):** Vendor due-diligence review if ToS is ambiguous
- [ ] **If Approved:** Trading Hat drafts Path A implementation spec
- [ ] **If Denied:** Research Bloomberg News API license ($2k–5k/mo) as alternative

**Next Review:** After Bloomberg ToS confirmation (target: June 1)

---

### 2. IB Execution API — Execution Phase Planning

**Request Date:** 2026-05-21  
**Requestor:** Nick Diaz (Managing Member)  
**Reviewed By:** Trading & Risk Hat (agent)  
**Status:** 🚫 BLOCKED — Execution phase not approved per ROADMAP  

**Compliance Rule:**
- `rcg_policy.md` Hard-refuse trigger #7: "Code change touches IB ordering / execution wiring without explicit 'we're entering execution phase' approval from MM"
- ROADMAP Phase G: "Trade execution wiring — 🔴 Only after paper backtest validates"

**Current State (May 21):**
- ✅ Stage 1 meta-model (linear blending) — 1 day away from gate (needs 7 days data)
- 🔵 Stage 2 (logistic conviction) — starts June 8 (4 weeks away)
- 🔵 Stage 3 (gradient boost) — starts June 22 (6 weeks away)
- 🔵 Stage 4 (regime-conditional weights) — starts July 7 (8 weeks away)
- 🔴 **No positive IC validation across regime shift yet** (the gate for IB integration)

**Required Prerequisites Before ANY IB Wiring:**
1. ✅ Stage 1 meta-model proves IC > 0 (next week)
2. ✅ Stage 2–4 prove IC uplift across regime shift (June 8 – July 7)
3. ✅ Backtest harness validates allocation decisions (Phase F)
4. ✅ Paper-trade validation period (2–4 weeks)
5. ✅ **CCO escalation:** Execution procedures documented (Compliance Manual §10–11)
   - Best execution policy updated
   - Trade error log process set up
   - Block trade allocation procedures
6. ✅ **MM explicit declaration:** "We're entering execution phase"

**What CAN Be Built Now (No Compliance Block):**
- ✅ Order-sizing math module (`src/execution/sizing.py`) — Kelly fraction, position limits (pure math)
- ✅ Slippage modeling (`src/execution/slippage.py`) — market impact estimates (research only)
- ✅ Pre-trade check framework (`src/risk/pretrade_checks.py`) — dry-run gates (max position, sector cap)
- ✅ Portfolio simulator expansion — "what if I'd traded top-N daily" backtest
- ✅ Trade blotter UI mockup — simulated fills only

**What is BLOCKED (Cannot Build Yet):**
- 🚫 Any code calling IB's `placeOrder()` method (even paper account)
- 🚫 Live market data subscriptions via IB
- 🚫 Account modification API calls

**Timeline (Earliest Possible):**
- **June 22:** Stage 3 ships (if Stages 1–2 validate)
- **July 7:** Stage 4 ships (if Stage 3 validates)
- **July 14+:** CCO escalation meeting — execution phase go/no-go
- **July 21+:** IB paper account setup (read-only connection first)
- **August+:** Paper-trade validation period (if CCO approves)
- **September+:** Live client execution (if paper period validates)

**Action Required:**
- [ ] **Compliance Hat:** Log this escalation attempt in decision_log (done automatically)
- [ ] **CCO (Ashley):** Acknowledge receipt of execution-phase planning request
- [ ] **Nick:** Explicit acknowledgment that IB wiring is deferred until July+ gates clear
- [ ] **Trading Hat:** Build execution math layer (sizing, slippage, checks) as pre-work

**Next Review:** After Stage 1 meta-model ships & proves IC > 0 (target: May 28)

---

## Backlog (Prioritized)

### Immediate (Next 1–2 Weeks)

| Item | Owner | Target | Blocker |
|------|-------|--------|---------|
| Stage 1 meta-model re-fit after gate clears | Quant Hat | May 22 | None |
| Earnings calendar integration (Finnhub) | Risk Hat | May 28 | None |
| Order-sizing math module (`src/execution/sizing.py`) | Trading Hat | May 28 | None |
| Slippage modeling (`src/execution/slippage.py`) | Trading Hat | May 30 | None |
| Pre-trade check framework (`src/risk/pretrade_checks.py`) | Risk Hat | June 1 | None |

### Infra Hardening Followups (rolling, not blocking releases)

Carried forward from incident postmortems (`docs/incidents/`). Each item closes a "Known gap" in `docs/OBSERVABILITY_STANDARD.md`.

| Item | Owner | Target | Source incident |
|------|-------|--------|-----------------|
| **Laptop BBG-host install** — follow `docs/LAPTOP_SETUP.md` to copy scripts + register Task Scheduler entries when laptop is in hand | Nick | Before summer travel | Multi-host v29.14 |
| **Re-auth gcloud on rcg-base** — `gcloud auth login` (currently breaks GCS backup of bloomberg_prices.json; SCP to NixOS still works) | Nick | This week | Surfaced during v29.14 testing |
| Migrate user-systemd units (`rcg-agent`, `rcg-infra-probe`, watchdog) into `claude-finance.nix` declarative config | Infra Hat | June 2 | 2026-05-19/20/21 |
| Agent startup self-check: import psycopg + spawn bash at boot, fail loud | Infra Hat | June 2 | 2026-05-20 agent-no-bash |
| Tool-error → `#infra-ops` alert on `ImportError` / `No such file` from tool calls | Infra Hat | June 2 | 2026-05-20 agent-no-bash |
| Wrap `markout_eval_publish.py` cron in failure-alerting systemd job | Infra Hat | June 5 | OBS known-gap |
| Postgres signal-capture row-count delta watchdog | Infra Hat | June 8 | OBS known-gap |
| Add new peers to `var/active_peers.conf` as additional Windows boxes come online | Infra Hat | rolling | Multi-host v29.14 |
| ~~**Unit tests for `detect_verb()`**~~ — DONE 2026-05-27: 40-case test suite at `tests/test_verb_detection.py`, all passing | ~~Infra Hat~~ | ~~June 1~~ | 2026-05-27 stop-loss false-cancel ✅ |
| **Audit `APPROVAL_VERBS` for similar single-word ambiguity** — patterns to check: "approve", "deploy", "merge", "cost". Replace single common words with compound phrases. | Infra Hat | June 1 | 2026-05-27 stop-loss false-cancel |
| **Echo detected verb in agent response** — when verb-shortcircuit fires, agent posts e.g. "_(interpreted as `cancel` based on the word "stop" — reply 'continue' to override)_" so user can spot false positives | Infra Hat | June 8 | 2026-05-27 stop-loss false-cancel |
| ~~**Consolidate dashboard versions**~~ — DONE 2026-05-27: v29 chosen canonical, v31 archived to `src/_archive_dashboards/`, outputs/ cleaned to only have v29 files. | ~~Quant Hat~~ | ~~June 1~~ | Dashboard v29.16 ✅ |
| **MM decision: NVDA / TSLA / SERV** — were in old static `src/watchlist.json` but daily screener doesn't carry them. Either add to screener's macro list, pin via `user_pinned.json`, or formally drop. | Nick | This week | 2026-05-26 pinned-tickers incident |
| **Watchlist-divergence alert** — infra probe should compare `factor_signals_bbg.json.watchlist` size vs `bloomberg_prices.json.watchlist` size; alert on >20% drift | Infra Hat | June 5 | 2026-05-26 pinned-tickers incident |
| **Consolidate watchlist files** — kill the `src/watchlist.json` ↔ `outputs/watchlist.json` dual pattern (currently bridged by symlink). One file, one path. | Infra Hat | June 8 | 2026-05-26 pinned-tickers incident |
| ~~**Migrate `markout_eval_publish` from cron → systemd timer with `Persistent=true`**~~ — DONE 2026-05-27: `~/.config/systemd/user/rcg-markout-publish.{service,timer}`, fires daily 06:00 UTC with `Persistent=true`. Crontab entry removed. | ~~Infra Hat~~ | ~~THIS WEEK~~ | 2026-05-26 post-reboot incident ✅ |
| ~~**Boot-catch-up race fix for sharadar↔screener**~~ — DONE 2026-05-27 via post-reboot reconciler (`src/rcg_post_reboot_reconcile.sh` + user-systemd timer, fires 25min after boot, detects + heals stale screener output). Workaround — see below for proper declarative fix. | ~~Infra Hat~~ | ~~This week~~ | 2026-05-26 post-reboot incident ✅ |
| **Declarative `After=sharadar-download.service`** in claude-finance.nix — proper fix for the boot-race, makes the reconciler workaround unnecessary. Needs `nixos-rebuild switch` so deferred. | Infra Hat | Next NixOS rebuild | 2026-05-26 post-reboot incident |
| **Fix polars Int64↔Float64 crash in `apply_blended_targets`** — `dynamic_factor_screener_v3.py` line 1611 blows up on `pl.Series("analyst_target_mean", analyst_means)` when Finnhub returns floats. Add `strict=False` or `dtype=pl.Float64`. Latent — original 09:00 run succeeded; manual re-runs may hit it. | Quant Hat | June 1 | 2026-05-26 post-reboot incident |

### Near-Term (June 1–15)

| Item | Owner | Target | Blocker |
|------|-------|--------|---------|
| Stage 2 meta-model (logistic conviction) | Quant Hat | June 8 | Need 4 weeks data |
| Options chain data (IV, put-call ratio) | Data Hat | June 10 | Source TBD (Tradier/Polygon/IBKR) |
| Rate curves (FRED: 2s10s slope, credit spread) | Data Hat | June 12 | None (FRED API free) |
| Economic data (ISM, PMI, unemployment) | Data Hat | June 15 | None (FRED API free) |
| Backtest harness (replay signals, score allocator) | PM Hat | June 15 | Need Stage 2 to validate first |

### Medium-Term (June 15 – July 7)

| Item | Owner | Target | Blocker |
|------|-------|--------|---------|
| Stage 3 meta-model (gradient boost + regime interactions) | Quant Hat | June 22 | Need 6 weeks data |
| Historical 5-min backfill (if Path A validates) | Data Hat | June 25 | Pending Bloomberg ToS approval |
| Stage 4 meta-model (regime-conditional weights) | Quant Hat | July 7 | Need 8 weeks data + Stage 3 done |
| Walk-forward champion auto-promotion | Quant Hat | July 1 | Need 2 weeks stable data per variant |

### Long-Term (July+ — Execution Phase Planning)

| Item | Owner | Target | Blocker |
|------|-------|--------|---------|
| CCO escalation meeting (execution phase go/no-go) | CCO + MM | July 14 | Stage 4 must validate first |
| Best execution policy update | CCO | July 18 | Awaiting execution phase approval |
| Trade error log process setup | CCO | July 18 | Awaiting execution phase approval |
| IB paper account setup (read-only connection) | Infra Hat | July 21 | Awaiting CCO approval |
| Paper-trade validation period (2–4 weeks) | PM Hat | Aug 1–31 | Awaiting paper account setup |
| Live client execution wiring | Infra Hat | Sept+ | Awaiting paper validation |

---

## Completed (Archive)

| Item | Completed | Version |
|------|-----------|---------|
| Per-ticker growth assumptions + valuation report | May 11 | v13–v15 |
| Regime tagging (vol + trend regime per fire) | May 12 | v17 |
| Hyperparameter sweep families (31 entrants, 11 families) | May 12 | v18 |
| PT consistency audit + fix (ARQ dimension, 3y history) | May 18 | v22 |
| Tier 1+2 quant signals (Hurst, Kalman, PCA MR) | May 18 | v24 |
| Stage 1 meta-blend (OLS linear, 45 features) | May 18 | v26 |
| Daily markouts (1d/5d/20d horizons) | May 19 | v27 |
| Markout dashboard (P&L curve, drawdown, IC panels) | May 19 | v28 |
| Bloomberg news insights document | May 21 | — |
| 5-min data frequency spec | May 21 | — |
| Agent execution bug fix (psycopg + PATH in user-systemd) | May 20 | v29.10 |
| Agent loop runaway fix (MAX_TOOL_LOOPS=50 + dup-call detector) | May 20 | v29.11 |
| Screener watchdog: upstream-aware + alert cooldown | May 21 | v29.12 |
| Agent prompt caching (system + tools, ~78% input savings) | May 21 | v29.12 |
| Agent tool-result cap tightened (50K → 20K bytes) | May 21 | v29.12 |
| Tailscale liveness probe (cross-host, 2-consecutive-fail debounce) | May 21 | v29.13 |
| Windows BBG-side heartbeat (SCP every 10 min via Task Scheduler) | May 21 | v29.13 |
| Multi-host BBG puller: fail-fast on non-BBG hosts + atomic SCP (`.tmp.<HOST>` then ssh-mv) | May 21 | v29.14 |
| Per-host heartbeat files + aggregate-glob probe (config-driven peers) | May 21 | v29.14 |
| `docs/LAPTOP_SETUP.md` step-by-step doc for onboarding new BBG-host boxes | May 21 | v29.14 |
| WSL2 post-reboot user-bus recovery: `loginctl enable-linger nixos` + CONTEXT_infra recovery row | May 26 | v29.14 |
| Starred/pinned ticker signal-pipeline fix: `src/watchlist.json` → symlink → `outputs/watchlist.json` | May 26 | v29.15 |
| BBG-pull intraday window bumped 3 → 7 days (was too thin over Memorial Day weekend) | May 26 | v29.15 |
| Markouts 3-day staleness manual backfill (cron didn't replay missed fires during outage) | May 26 | v29.15 |
| Markout dashboard v29.16 — Best Ticker + Worst Ticker columns (per_ticker.cum_pnl max/min), metrics-window banner showing rolling-90d window, removed CSV export | May 27 | v29.16 |
| Agent verb-detector false-positive fix — dropped `"stop"` from cancel patterns; agent no longer self-cancels when user mentions "stop loss" or similar trading terms | May 27 | v29.16 |
| `detect_verb()` unit tests — 40-case `tests/test_verb_detection.py`, all passing, locks in the fix | May 27 | v29.16 |
| Dashboard v29/v31 consolidation — v29 kept canonical, v31 + backups archived to `src/_archive_dashboards/` | May 27 | v29.16 |
| Markout publisher migrated cron → systemd user-timer with `Persistent=true` (`rcg-markout-publish.timer`, fires daily 06:00 UTC, catches up after downtime) | May 27 | v29.16 |
| Post-reboot reconciler — `src/rcg_post_reboot_reconcile.sh` + user-systemd timer, fires 25min after boot, detects sharadar↔screener race + re-fires screener if stale | May 27 | v29.16 |

---

## How to Use This File

**When adding a new item:**
1. Determine priority tier (Immediate / Near / Medium / Long)
2. Add to appropriate section with Owner, Target, Blocker columns
3. If Compliance-sensitive: add to "Queued for Compliance Review" section first
4. Git commit with message: `TODO: added <item> to <section>`

**When completing an item:**
1. Move from Backlog → Completed with completion date + version tag
2. Update ROADMAP.md status (⚪ → 🟢)
3. Git commit with message: `TODO: completed <item> (v<N>)`

**When blocked:**
1. Update Blocker column with explicit gate
2. If Compliance block: escalate to CCO via Slack/email
3. If data block: add calendar reminder for when gate clears

**Compliance Escalation Path:**
- 🚫 Hard refuse → cannot proceed without MM override + CCO sign-off
- ⚠️ Soft flag → proceed after MM acknowledgment, log decision
- ⏸️ Awaiting approval → queue in "Queued for Compliance Review" section
- ✅ Approved → move to Backlog with target date

---

**Maintained By:** Trading & Risk Hat (agent-assisted)  
**Next Review:** May 28 (after Stage 1 meta-model ships)  
**Compliance Contact:** Ashley Schott (CCO) — aschott@robincapitalgroup.com
