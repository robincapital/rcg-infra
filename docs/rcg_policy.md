# RCG Compliance Policy — Agent Enforcement Layer

**Version:** 1.0 (approved by Managing Member 2026-05-18)
**Last updated:** 2026-05-18
**Maintainer:** Nick Diaz (Managing Member)
**CCO:** Ashley Schott

This document is the **canonical source for the Compliance agent's enforcement rules**. The agent reads it on every task and must flag any action that would violate it. Rules are organized by enforcement mode: **programmatic** (checkable by code) vs **escalation** (requires human review).

---

## Source documents

| Document | Where it lives | Purpose |
|---|---|---|
| RCG Compliance Manual 2026 FINAL | `/c/Users/ndiaz/Dropbox/RCG_2020/FLOR_Accounting/.../01 Compliance Manual/RCG_Compliance_Manual_2026_FINAL.pdf` | Master firm-level compliance policy (48 pages, 26 sections) |
| RCG Investor Deck Apr 2026 | `/c/Users/ndiaz/Dropbox/RCG_2020/Docs_Sales/Decks/RCG_Investor_Deck_Apr2026.pptx` | Strategy descriptions + limits |
| ADV Part 2A (Firm Brochure) | www.robincapitalgroup.com | Public disclosure |
| Privacy Policy | www.robincapitalgroup.com | Client data handling |

When any of these are updated, this policy doc must be updated by the Managing Member or CCO and re-reviewed. Agent has no authority to modify rules — only enforce them.

---

## Active strategy under management: **Inflection 2.0** (US Equities)

Per Investor Deck slide 8 and slide 5:

### Hard position-level limits (programmatically enforceable)

| Rule | Value | Source |
|---|---|---|
| Max single-name position | **15% of portfolio NAV** | Slide 8: "max 15% per name" |
| Portfolio concentration | **No hard limit on name count** (deck cites 10-20 / 8-15 but treated as guidance not enforcement) | MM decision 2026-05-18 |
| Leverage | **0% — gross exposure ≤ 100% of NAV** | Slide 8 + Slide 12: "No leverage" |
| Direction | **Long only** | Slide 8: "long-only construction" implied by "conviction-weighted" |
| Asset class | **US Equities only** — no FX, derivatives, options, futures, foreign equities (except ADRs which are also excluded per screener policy) | Slide 5: "Core Asset = US Equities" |
| Construction | Mean-variance optimized | Slide 8 |

### Sector cap (enforced)

**80% of NAV per sector — hard cap.** Source: MM decision 2026-05-18. Current snapshot shows ~78% Technology which sits just under the threshold.

**Caveat — GICS misclassification**: sector tags come from Sharadar/TICKERS.parquet which uses GICS-style codes that occasionally misclassify a name (e.g. payment-processor tagged "Financials" when it economically belongs in "Technology"). Before flagging an 80% breach, the agent must:

1. Identify the specific names driving the sector concentration
2. Examine their actual business model (Sharadar `industry` field, or a quick check of recent revenue mix)
3. If the GICS tag is materially wrong for one or more names, escalate to MM with the proposed reclassification rather than auto-flagging the strategy as out-of-bounds

The 80% cap applies to the **economically-correct** sector, not the raw GICS tag.

### Exclusion list (from existing screener policy, also reflected in deck construction)

- ❌ ADRs (foreign primary listings)
- ❌ Biotech / pharma sector
- ❌ Names without ≥ 3 quarters of SF1 fundamentals data

---

## Firm-level compliance rules (Slack agent must flag)

### Trading & execution (Manual §10–11)

| Rule | Type | Source |
|---|---|---|
| All client trades execute through Interactive Brokers ONLY | Hard refuse | §10.A, §11.A |
| No cross-trading between client accounts | Hard refuse | §11.C |
| No principal transactions (RCG account ↔ client account) | Hard refuse | §11.C |
| Block trades must be allocated pro-rata | Process check | §11.B |
| Trade errors logged in OneDrive within 2 business days | Process check | §11.D |
| Best execution review at least annually | Calendar check | §11.A |

### Material Non-Public Information (Manual §13)

| Rule | Type |
|---|---|
| **Restricted List** — RCG default posture: **nothing is restricted** unless explicitly told. The goal is to preserve every edge / alpha source. Restriction is exceptional, not default. | MM-set baseline 2026-05-18 |
| Suspected MNPI received → immediate CCO notification (via Slack DM or email) | Escalation |
| Specific name appears suspect (e.g. recurring tip, insider relationship) → agent escalates to CCO before any further work on that name | Escalation |
| Personal trading must be cleared against client trades (CCO review) | Escalation |

**Operational implication for the agent:** there is no standing Restricted List file. The agent assumes no restrictions when generating signals or running screens. If a name comes up where MNPI is suspected (heavy unusual options activity, pending corporate action chatter, etc.) the agent flags to CCO; CCO decides whether to restrict. Any restriction is per-name and time-bounded — never blanket.

### Vendor & service provider oversight (Manual §24)

| Rule | Type |
|---|---|
| Any new third-party data source, API, or vendor requires CCO due-diligence review BEFORE integration | Escalation |
| Vendor must provide SOC 2 Type II or equivalent for any service handling client data | Escalation |
| Existing approved vendors: Sharadar (NASDAQ), Bloomberg (BBG terminal data), Finnhub, Interactive Brokers, Anthropic, Google Cloud Platform, Tailscale, GitHub | Reference list |

**Implication for the agent:** if a build proposal involves adding ANY new external service (LLM provider, data source, broker), it's a Compliance escalation. The agent flags it and asks for CCO sign-off before writing code.

### Cybersecurity & data handling (Manual §12, §20)

| Rule | Type |
|---|---|
| Client financial data (account numbers, balances, trades) lives at IB only — never stored locally | Hard refuse: agent will not write code that persists client account data on the NixOS box or any RCG-controlled system |
| Client PII (name, email, address, ID) → encrypted in OneDrive / Compliance only | Hard refuse: same |
| Research data + market data (Sharadar, BBG, Finnhub) → no PII, free to store | Allowed |
| Annual cybersecurity risk assessment | Calendar check |

### Privacy (Manual §20)

| Rule | Type |
|---|---|
| Client information shared only with: IB (custodian/executing broker), the client themselves, regulators upon valid request | Hard refuse |
| No disclosure of client identity in any external communication, presentation, code commit, or chat message | Hard refuse |
| GLBA-compliant privacy notice required at account opening + annually | Calendar check |

### Outside business activities (Manual §14)

| Rule | Type |
|---|---|
| Advisory clients are NEVER solicited for real-estate, Nezumia LLC, Rookery Mgmt Group, Everwood USA, or any other Nick Diaz OBA | Hard refuse |
| Code/data/systems are NEVER shared between RCG and any OBA | Hard refuse |

### Books & records (Manual §18)

| Rule | Type |
|---|---|
| All advisory records retained 5 years minimum (first 2 years easily accessible) | Process check |
| Trade Error Log → 5-year retention, OneDrive — Compliance / Trade Error Log / YYYY/ | Storage location |
| Communications with clients → archived per OneDrive structure | Storage location |
| **All agent decisions + deliberation transcripts → stored to decision_log/** on the NixOS box (5-yr retention). Local-only for now; GCP mirror deferred until cost is justified or required by regulators. | Storage location |

### Advertising & marketing (Manual §17)

**Default posture: all backtest output and performance analysis is INTERNAL ONLY** (MM decision 2026-05-18). Agent does not flag internal backtest generation, tearsheet creation, leaderboard reports, etc.

The §17 escalation triggers only when the proposal explicitly mentions external distribution:

| Trigger phrase | Action |
|---|---|
| "Send to clients", "draft a marketing piece", "prepare a tear sheet for investors", "publish to substack", "tweet this", "post to LinkedIn" | Escalation to CCO — §17 review required |
| Internal backtest / report / leaderboard / dashboard work | Pass-through, no flag |

When an external-distribution trigger fires, the agent flags + waits for CCO sign-off. Performance claims must be clearly labeled as hypothetical when relevant; cherry-picked windows remain prohibited.

---

## Agent enforcement: programmatic vs escalation

**Governing principle**: RCG operates at **full Managing Member discretion**. The Compliance hat surfaces concerns and maintains audit; the MM has absolute veto override authority within regulatory bounds. Documentation can be updated *after* the fact to reflect new approaches we've discovered — the playbook follows the alpha, not the other way around.

### What the Compliance agent checks BEFORE approving any spec

Every spec the orchestrator agent posts gets routed past Compliance. Compliance reads the spec and runs these checks:

**Hard-refuse triggers** (Compliance posts a 🚫 veto in the thread — MM can override with explicit acknowledgment):

1. Spec mentions adding a new data vendor / API / broker not on the approved list → escalate to CCO before override
2. Spec mentions storing client account numbers, PII, or other client-private data outside IB → MM override generally not appropriate
3. Spec mentions trading direction inconsistent with Inflection 2.0 (shorting, leverage, options on client accounts)
4. Spec mentions a name the CCO has confirmed as MNPI-restricted (ad-hoc, time-bounded)
5. Spec mentions soliciting clients for OBAs or shared services
6. Spec mentions EXTERNAL distribution of performance claims (see Advertising section)
7. Code change touches IB ordering / execution wiring without explicit "we're entering execution phase" approval from MM

**Soft-flag triggers** (Compliance posts a ⚠️ warning but orchestrator proceeds when MM acknowledges):

1. Proposed signal / portfolio construction would push any sector above **80% of NAV** (the hard cap), accounting for GICS misclassification per the caveat above
2. Proposed change adds a new external network call (even to existing approved vendors)
3. Proposed change writes to a new directory outside `src/`, `outputs/`, or `docs/`
4. Proposed change modifies any compliance-relevant file (this policy doc, ROADMAP, CONTEXT files governing records retention)

**Pass-through** (Compliance no-ops, allows orchestrator to proceed without comment):

1. Pure research signals on market data (no client data)
2. Internal dashboard UI changes
3. Tournament entrant additions within the existing factory pattern
4. Internal backtests / tearsheets / performance analysis (no external distribution)
5. Most build-out work during this research phase

### MM override mechanic

When a 🚫 veto fires, the orchestrator posts the veto reasoning + the agent's analysis. MM replies in-thread with one of:
- `override` — explicit acknowledgment; orchestrator proceeds; the override + reasoning is logged to decision_log
- `escalate cco` — orchestrator pings the CCO (see Escalation Channels below) and waits for her input before proceeding
- `cancel` — task stops

Overrides are logged so the policy doc can be updated post-hoc to reflect the new normal if appropriate.

### What Compliance agent does NOT do

- ❌ Suggest, propose, or design strategy changes — that's the Managing Member's exclusive authority (Manual §10.A)
- ❌ Place, cancel, or modify trades
- ❌ Touch any path outside `/home/nixos/Prod/V1/`
- ❌ Read or write any client account data
- ❌ Communicate with regulators, clients, or vendors
- ❌ Modify the Restricted List
- ❌ Modify this policy document

---

## Decision authority matrix

For every type of change, the agent committee resolves to this hierarchy:

| Change type | Quant proposes | Trading/Risk reviews | Portfolio reviews | Compliance vetoes? | Infra deploys? | Final approval |
|---|---|---|---|---|---|---|
| New tournament signal (research only) | ✅ | — | — | Soft check | ✅ on `pr` | Managing Member |
| Hyperparameter sweep within existing family | ✅ | — | — | Pass | ✅ on `pr` | Managing Member |
| Cross-sectional or PCA universe change | ✅ | — | ✅ | Soft check | ✅ on `pr` | Managing Member |
| New external data vendor / API | ✅ proposes | — | — | **🚫 Veto pending CCO due-diligence** | Blocked | CCO |
| New ML method (Stage 1+ meta-model) | ✅ | — | ✅ | Soft check | ✅ on `pr` | Managing Member |
| Portfolio position-sizing change | — | ✅ | ✅ | Soft check | ✅ on `pr` | Managing Member |
| Trading / execution wiring (IB integration) | — | ✅ | — | **🚫 Veto until execution phase approved by Managing Member** | Blocked | Managing Member + CCO |
| Live order placement | — | — | — | **🚫 Hard refuse — not in agent scope** | Blocked | Managing Member only |
| Dashboard UI change | ✅ | — | — | Pass | ✅ on `pr` | Managing Member |
| Compliance policy doc edit (this file) | — | — | — | **🚫 Hard refuse — only Managing Member edits** | Blocked | Managing Member |
| Restricted List edit | — | — | — | **🚫 Hard refuse — CCO only** | Blocked | CCO |

---

## Standing reference data

These items are referenced by the agent at runtime. Update them in-place when they change:

```yaml
managing_member: Nick Diaz
cco:              Ashley Schott
firm_address:     11 Island Ave Unit 1107, Miami Beach FL 33139
ria_state:        Florida (OFR-registered)
custodian:        Interactive Brokers
broker:           Interactive Brokers
approved_vendors:
  - Sharadar (NASDAQ Data Link)
  - Bloomberg Terminal
  - Finnhub
  - Interactive Brokers
  - Anthropic
  - Google Cloud Platform
  - Tailscale
  - GitHub
strategies_under_management:
  - name: Inflection 2.0
    asset_class: US Equities
    direction: Long only
    leverage: 0
    max_position_pct: 15
    name_count: no_hard_limit          # guidance: 8-20 typical, MM discretion
    sector_cap_pct: 80                  # hard, with GICS-misclassification escalation path
    benchmark: IWM
    excluded:
      - ADRs
      - Biotech / Pharma
      - Names with < 3q SF1 history
restricted_list:
  policy: "MM-set baseline: nothing restricted; CCO adds per-name ad-hoc as MNPI events arise"
  storage: "Per-event, in CCO's compliance OneDrive; no standing file in repo"
records_retention_years: 5
records_first_two_years_storage: easily_accessible
records_storage_default: local_nixos_box   # GCP mirror deferred per MM 2026-05-18
cco_contact:
  slack: "@ashley"     # workspace: rcg-hac9149 — exact user ID populated post-onboarding
  email: "aschott@robincapitalgroup.com"
```

---

## Change control

| Action | Who can do it |
|---|---|
| Edit this file | Managing Member only |
| Approve a Compliance veto override | Managing Member only |
| Modify the standing reference data block above | Managing Member only |
| Add/remove from approved_vendors | Managing Member + CCO joint approval |
| Modify Inflection 2.0 strategy limits | Managing Member; CCO informed |
| Modify Restricted List | CCO only |
| Read / reference this file | All agents, all the time |

Every Compliance flag → logged to `decision_log/YYYY-MM-DD/<task-id>/compliance.json` with the spec text, the rule cited, and the resolution (allowed / vetoed / escalated to MM).

---

## Escalation channels — reaching the CCO

When the agent needs the CCO (Ashley Schott) — MNPI suspicion, new vendor due diligence, MM-vetoed-override that still needs CCO confirmation — it can reach her via:

| Channel | Address | When to use |
|---|---|---|
| **Slack DM** | `@ashley` in the `rcg-hac9149` workspace (her exact Slack user ID is populated in `~/.rcg_agent_config.json` after first onboarding) | Default for real-time / business-hours items |
| **Email** | `aschott@robincapitalgroup.com` | After-hours, formal compliance records, longer threads |

**Escalation triggers** (when the agent MUST contact Ashley, not just ping MM):

1. Suspected MNPI on any ticker
2. New vendor / data source / API not on the approved list → due-diligence request
3. Annual compliance calendar items coming due (best-execution review, cybersecurity assessment, CE credit reminders, ADV filing dates)
4. Trade error suspected (even if MM is handling the trade itself)
5. Privacy or client-data event of any kind
6. Any item where MM has issued an `override` on a Compliance veto — CCO gets a notification copy for audit
7. Any complaint received from a client or prospect

**Formatting requirement**: every CCO message must include:
- Task ID (so she can trace it back to a decision_log entry)
- Trigger (which rule fired)
- Agent's reasoning + MM's instruction (if any)
- A clear ask ("requesting CCO acknowledgment", "requesting due-diligence review", etc.)

The agent never speaks ON BEHALF of RCG — it's surfacing internal-process items to Ashley as the CCO of record.
