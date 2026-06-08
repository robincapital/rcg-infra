# RCG Compliance Queue — Pending CCO Review

**Maintained By:** Compliance Hat (agent-assisted)  
**CCO:** Ashley Schott — aschott@robincapitalgroup.com  
**Last Updated:** 2026-05-21  

---

## Purpose

This file tracks items requiring **CCO (Chief Compliance Officer) review** before implementation. Items land here when:
1. Agent flags a compliance rule violation (hard refuse or soft flag)
2. Managing Member requests a build that triggers vendor due-diligence
3. Execution phase / trading wiring is proposed
4. External distribution of performance claims is requested

**Escalation Channels:**
- **Slack DM:** `@ashley` in `rcg-hac9149` workspace (business hours, real-time)
- **Email:** `aschott@robincapitalgroup.com` (after-hours, formal records)

---

## Active Queue (Awaiting CCO Review)

### 1. Bloomberg Terminal — 5-Min Data Frequency Upgrade

**Date Queued:** 2026-05-21  
**Requestor:** Nick Diaz (Managing Member)  
**Flagged By:** Trading & Risk Hat (agent)  
**Status:** ⏸️ **AWAITING CCO VENDOR DUE-DILIGENCE**  

**Request Summary:**
- Upgrade from 30-min → 5-min intraday data refresh cadence for trading signals
- Current: 118 tickers × hourly Bloomberg bars, 30-min refresh (18 fires/day)
- Proposed: 118 tickers × 5-min Bloomberg bars, 5-min refresh (85 fires/day)
- **Estimated IC Uplift:** +5–10% for 5min/10min horizons (unvalidated)
- **Infrastructure Impact:** 5× Postgres writes (27K → 137K signals/day), ~20% Bloomberg API quota usage

**Compliance Question:**
> Does Bloomberg Terminal Terms of Service (ToS) allow **programmatic extraction of 5-min intraday bars** for internal advisory signal generation?

**Background:**
- Bloomberg Terminal is on RCG's approved vendor list (`rcg_policy.md`)
- Current hourly bar extraction via `xbbg` Python library has operated without issue for 6+ months
- However, ToS may have frequency-based restrictions (tick data = restricted; unclear if 5-min falls under that)
- If ToS restricts programmatic 5-min: would require **Bloomberg News/Data API license** (~$2k–5k/month incremental cost)

**Documentation:**
- Full technical spec: `/home/nixos/Prod/V1/docs/5min_frequency_spec.md`
- Bloomberg news insights: `/home/nixos/Prod/V1/docs/bbg_news_insights.md`
- Current implementation: `C:\Users\ndiaz\Downloads\bloomberg_prices.py` (Windows) + NixOS timers

**Action Required:**
1. [ ] **Nick Diaz:** Email Bloomberg Account Manager to confirm ToS compliance for 5-min programmatic extraction
   - **Contact:** [Bloomberg account manager name/email — Nick to fill in]
   - **Ask:** "Our advisory firm (RCG, SEC-registered) uses Terminal data via `xbbg` Python library to generate internal trading signals. We currently pull hourly intraday bars. Does ToS permit upgrading to 5-minute bars for the same purpose?"
2. [ ] **CCO (Ashley):** Review Bloomberg's response
   - **If ToS allows:** Sign off on Path A implementation (limited 5-min rollout for validation)
   - **If ToS restricts:** Vendor due-diligence review for Bloomberg News/Data API license alternative
   - **If ambiguous:** Legal review (external counsel or Bloomberg's compliance desk)

**Risk Assessment:**
- **Low Risk:** 5-min bars are NOT high-frequency (HFT) — Bloomberg's restriction target is tick-level data
- **Medium Risk:** "Programmatic extraction" clause may be interpreted broadly; Bloomberg may require Enterprise API for automated polling
- **Mitigation:** Current hourly extraction has operated 6+ months without ToS violation notice; 5-min is incremental change, not fundamental pivot

**Next Review Date:** June 1, 2026 (or upon Bloomberg response, whichever is earlier)

---

### 2. IB Model Account API Integration — Operational Automation

**Date Queued:** 2026-05-21  
**Requestor:** Nick Diaz (Managing Member)  
**Flagged By:** Trading & Risk Hat (agent)  
**Status:** ⚠️ **SOFT FLAG — CCO REVIEW REQUIRED (NOT BLOCKED)**  

**CRITICAL CONTEXT REVISION:**
RCG is **already live** with client trading on IB. Nick actively manages 3 strategies (Rates, Global Macro, Equities) across multiple client accounts using **IB Model Accounts** functionality. This is NOT a "future execution phase" request — it's **operational tooling** to automate existing manual workflow.

**Current Workflow (Manual):**
- Nick sets target % weights in IB TWS (Trader Workstation) manually
- IB calculates per-account allocation (pro-rata by NAV)
- Nick submits orders via TWS UI → IB splits across accounts automatically
- Nick manually adds/removes tickers, rebalances daily/weekly

**Proposed Workflow (Automated via API):**
- Signal system computes target weights programmatically
- Python script queries IB API for model NAV + current positions
- Script calculates rebalance orders, runs pre-trade checks
- Script submits orders to IB API (model-level or account-level)
- IB handles allocation same as manual workflow (no change to client experience)

**Why Original Hard-Refuse Was Wrong:**
- ❌ Agent assumed "execution phase" = new capability (wrong — already live)
- ❌ ROADMAP Phase G refers to **new strategy development**, not existing operations
- ✅ This is **operational tooling** for active trading (different compliance category)

**IB API Capabilities Needed:**

**✅ Already Allowed (Existing Operations):**
- IB Model Account API queries (model NAV, positions, account lists)
- Pre-trade checks (max position, sector cap, leverage)
- Order calculation logic (target weights → shares)
- Order submission for **existing strategies** (Rates, Macro, Equities) on **existing client accounts**

**⚠️ Requires CCO Review:**
- **Automated order submission** vs. manual TWS orders (operational risk assessment)
- **Error handling procedures** (what happens if API fails mid-rebalance?)
- **Client disclosure** (do clients know orders may be submitted via automated system vs. manual entry?)
- **Best execution policy update** (does current policy cover API orders, or only manual TWS?)
- **Trade error log integration** (API failures, order rejections must be logged per §11.D)

**🚫 Still Blocked (New Capabilities, Not Part of Current Operations):**
- ❌ Automated trading for **new strategies** not yet approved by clients (e.g., "Inflection 2.0" from ROADMAP)
- ❌ Account transfers, withdrawals, margin changes via API
- ❌ Cross-trading between accounts (prohibited per §11.C regardless of API vs. manual)

**Timeline (Revised — Operational Automation):**
| Date | Milestone | Action |
|------|-----------|--------|
| **May 21** | Context clarification (done) | Trading Hat drafts IB Model Account spec |
| **May 22** | Nick answers open questions | Model codes, account counts, workflow preferences |
| **May 23-24** | Build `IBModelTrader` class (read-only) | Query NAV, positions, test IB API connection |
| **May 27** | CCO reviews spec + answers open questions | Best execution, error handling, client disclosure |
| **May 28-29** | Add order submission methods (dry-run) | Calculate orders, log pre-trade checks (no live submission) |
| **May 30-31** | Test on IB paper account | Validate allocation logic, error handling |
| **June 3** | CCO approves (if satisfied) | Sign-off on automated order submission |
| **June 4+** | Deploy to live (Equities model first) | Monitor vs. manual execution for 1 week, then roll out to Rates/Macro |

**Action Required:**
1. [ ] **Nick (Managing Member):** Answer open questions in `/home/nixos/Prod/V1/docs/ib_model_accounts_spec.md` Section 10:
   - IB model codes (exact strings for API calls)
   - Account counts per model
   - Current workflow (% weights vs. shares)
   - Rebalance frequency (daily/weekly/event-driven)
   - Order type preference (limit vs. market)
   - Account-specific restrictions (if any)
   - Execution preferences (immediate vs. batch, approval gate vs. full auto)
   - Price data source (IB real-time vs. Bloomberg vs. other)
   - P&L tracking needs (real-time vs. EOD)

2. [ ] **CCO (Ashley):** Review IB Model Account spec + answer compliance questions:
   - **Does current best execution policy cover automated API orders?** (vs. manual TWS orders only)
   - **Does trade error log process handle API failures?** (network errors, order rejections logged within 2 business days per §11.D)
   - **Do clients know orders may be submitted via automated system?** (disclosure requirement — check ADV Part 2A, client agreements)
   - **Are there additional risk controls needed?** (e.g., daily order volume limits, emergency kill-switch)
   - **Do you want to review logs from paper account testing before live deployment?** (recommended)

3. [ ] **Trading Hat (me):** Build `IBModelTrader` class (read-only methods first):
   - Connect to IB TWS/Gateway
   - Query model NAV, positions, account lists
   - Calculate rebalance orders (dry-run, no submission)
   - Test on paper account, log results for CCO review

**Risk Assessment (Revised):**
- **Low-Medium Risk:** This is operational tooling for **existing trading workflow**. Manual process already vetted + approved. Automation adds:
  - ✅ **Lower operational risk** (no fat-finger errors, consistent pre-trade checks)
  - ⚠️ **New technical risk** (API failures, network errors, order submission bugs)
  - ⚠️ **Compliance risk** (need to document that API orders = same best execution as manual)
- **Mitigation:** Paper account testing + CCO review before live deployment. Start with 1 model (Equities), monitor for 1 week, then expand.

**Approval Path (No Override Needed):**
This is **operational improvement**, not new capability. CCO reviews for:
1. Best execution policy compliance
2. Trade error log integration
3. Client disclosure (if needed)
4. Paper account test results

If CCO approves: proceed to live. If CCO requests changes: address + re-submit.

**Next Review Date:** May 27, 2026 (after Nick answers questions + CCO initial review)

---

## Resolved Queue (Archive)

*No items yet — this is the inaugural Compliance Queue document.*

---

## Compliance Escalation Procedures

### When to Add Item to This Queue

**Automatic (Agent-Triggered):**
- Agent flags a **🚫 Hard-refuse trigger** (7 triggers defined in `rcg_policy.md`)
- Agent flags a **⚠️ Soft-flag trigger** requiring MM acknowledgment
- New vendor/data source/API proposed (Manual §24 requires CCO due-diligence)

**Manual (MM-Requested):**
- MM asks agent to build something that involves execution, external distribution, or client data
- MM proposes a change to strategy limits (position size, sector cap, leverage)
- MM requests an exception to standing compliance policy

### How CCO Reviews

**For Each Queued Item:**
1. **Read full context** — documentation links, agent reasoning, risk assessment
2. **Consult source docs if needed:**
   - RCG Compliance Manual 2026 FINAL (48-page master policy)
   - ADV Part 2A (public disclosure)
   - Relevant vendor ToS / contracts
3. **Decision options:**
   - ✅ **Approve** — item moves from queue → backlog with target date
   - ⚠️ **Conditional approval** — approve with additional requirements (e.g., "only after Stage 4 validates")
   - ❌ **Deny** — item stays blocked, CCO provides alternative path or explanation
   - 🔄 **Escalate externally** — CCO consults external counsel, auditor, or vendor compliance desk
4. **Document decision** — CCO replies via Slack/email with decision + reasoning
5. **Agent updates queue** — move item to "Resolved Queue" with CCO decision logged

### Response Time Expectations

| Priority | Response Time | Example |
|----------|---------------|---------|
| **Urgent (blocks production)** | 24 hours | Vendor ToS violation discovered in live system |
| **High (blocks active build)** | 3 business days | New API integration proposed mid-sprint |
| **Medium (planning item)** | 1 week | Execution phase planning 2+ months out |
| **Low (informational)** | 2 weeks | Annual calendar items, policy clarifications |

**Current Queue Items:**
- Bloomberg 5-min ToS: **High** (blocks active build path, but Path A deferred)
- IB Execution API: **Medium** (planning item, not urgent — blocked until July+)

---

## Contact Information

**CCO (Ashley Schott):**
- **Slack:** `@ashley` in `rcg-hac9149` workspace (preferred for business hours)
- **Email:** `aschott@robincapitalgroup.com` (after-hours, formal records)
- **Phone:** [CCO phone — Nick to fill in if needed]

**Managing Member (Nick Diaz):**
- **Slack:** Primary contact (this channel)
- **Email:** [Nick's email — on file]

**Agent (Compliance Hat):**
- Monitors every build proposal, flags violations automatically
- Updates this queue file when new items require CCO review
- Logs all escalations in `decision_log/YYYY-MM-DD/<task-id>/compliance.json`

---

**Document Created:** 2026-05-21  
**Last Reviewed by CCO:** [Pending — awaiting initial CCO acknowledgment]  
**Next Scheduled Review:** Monthly (first Monday of each month, starting June 1)
