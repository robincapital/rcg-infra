# Bloomberg DAPI Compliance — MM Override Log

**Date:** 2026-05-28
**Logged by:** RCG Quant Agent (transcript: markout dashboard / BBG expansion thread)
**MM decision:** Proceed with expansion under reduced scope; defer formal remediation
**Source document:** `Guidelines for developing 3rd party applications using Desktop API` (Bloomberg, March 2025) — provided by MM 2026-05-28

---

## The finding

Bloomberg's Desktop API (DAPI) terms-of-use document, surfaced to the agent on 2026-05-28, prohibits several behaviors the current RCG production architecture relies on. Specific structural conflicts identified:

| DAPI rule | Current behavior |
|---|---|
| "Bloomberg Data may not leave the desktop of the Bloomberg terminal users... server-side component / persistent database / network drive" | SCP from Windows BBG box → NixOS; `live_price` persisted in Postgres `signals` table (~364K rows); upload to `gs://rcg-prod-data/bloomberg/intraday/` |
| "User must be logged into the Bloomberg Terminal on the same device in which the application is running" | Dashboard runs on NixOS; Terminal on Windows |
| "Cannot be used in a black-box application" / "automated validation activities" | Cron-driven `predictions_capture`, model-score capture, screener, markout backtest |
| "Cannot be accessed by the end user from multiple devices simultaneously" / "cannot be exported" | Tailscale-served dashboard accessible from any device; automated GCS archives |
| "Applications must be registered with Bloomberg" (API 3.20+) | `blpapi.Session()` starts without registration token |

The expansion spec written immediately prior (`bloomberg_expansion_spec.md` v1) would have multiplied these violations (subscription stream, 500 tickers, 1-min snapshots, persistent storage of all of it).

---

## MM decision (verbatim direction)

> "we need the current workflow to keep data on the rcg side safe. We dont really need to store all of the data, only the parts that are being used on trade, markout when signals trigger... the dashboard ticking more often is a great addition even if not stored. perhaps that alleviates some of the compliance breaches. But im inclined to move forward with more tickers for now and deal with bloomberg flags later. an enterprise license is out of the question for now as we experiment with this. If we get strong enough alpha to cover the costs down the road we can revisit, there are surely cheaper data sources out there for these tickers."

### Decision summary
1. **Scope reduced** from the v1 spec: 350 tickers (not 500), 1-min display tick on the dashboard, trigger-only persistence.
2. **Existing storage preserved as-is** (do not delete `live_price` history; existing dashboards keep working).
3. **No new bulk persistence** of BBG-derived data going forward — only signal-trigger events get written to Postgres / GCS.
4. **Enterprise license not pursued** under current alpha posture.
5. **Migration target acknowledged**: when alpha is provable, swap automated/persistent BBG dependency for a properly-licensed data source (Polygon.io, Alpaca, IEX Cloud, etc.).
6. **Status:** known non-compliance, accepted by MM. Deferred remediation.

---

## Compliance posture

Per `rcg_policy.md` standing reference: RCG operates at full Managing Member discretion. MM has absolute veto override authority within regulatory bounds. This is a vendor-terms-of-use matter, not a regulator/MNPI/client-data matter; falls within MM discretion.

CCO Ashley Schott has **not** been notified of this finding yet. Recommended escalation triggers (per policy §24, vendor oversight):
- If MM elects to ship the expansion to any user beyond Nick → escalate to CCO before deploy
- If RCG begins generating client-facing material derived from BBG signals → escalate (already covered by §17 advertising rule)
- If audit/inquiry from Bloomberg is received → immediate CCO + outside counsel

---

## Conditions under which to revisit

1. **Alpha clears \$50K/year incremental P&L** attributable to BBG-derived signals → revisit data-source migration (Polygon ~\$200/mo, others similar)
2. **Bloomberg sends any inquiry, audit notice, or registration request** → halt the automated/persistent uses immediately + escalate to CCO
3. **Firm AUM grows past current single-client-equivalent posture** → re-review under §24 vendor oversight
4. **API version forces registration-token requirement** (BBG API 3.20+ change) → halt or comply

---

## What the agent will and will not do under this override

**Will:**
- Build the 350-ticker subscription stream
- Refresh dashboard live (no incremental persistence)
- Persist only at signal-trigger events (model fires, trade entries/exits)
- Preserve current `live_price` / model-score / screener captures (existing workflow unchanged)
- Continue the markout GCS archive shipped 2026-05-27 (per MM "move forward... deal with flags later")
- Document data-source migration candidates when MM gives the signal

**Will not (without further MM direction):**
- Expand persistence beyond signal-trigger events
- Add new GCS archive paths for BBG-derived raw data
- Increase ticker count past 350 ("keep testing before making further data calls")
- Register the application with Bloomberg unilaterally (changes auth/entitlements — MM call)
- Notify Ashley without MM go-ahead (per the agent's standing escalation rules, this is below the hard-trigger threshold but the MM should still own the decision)

---

## 2026-05-28 mid-morning — extended-hours streaming + persistence decision

### Decisions
- **bloomberg_stream.py extended** to 4:00 AM – 8:00 PM ET M-F (was 9:30-16:00 ET). Live in production 9:21 ET, observed 17,838 field updates in the first minute of subscription, 118 / 120 tickers reporting LAST_PRICE.
- **Capture timers NOT extended yet** — MM declined to extend predictions-capture / models-capture / forward-returns / finnhub-signals into pre-market. Reason: tournament models assume RTH bar conventions; pre-market signals may be noise.
- **Per-model pre-market audit** opened (separate task) before extending persistence.

### What this means for backtesting (as of now)
- RTH backtests: fully honest — every fire, every input, every realized return captured with TIMESTAMPTZ + asof_date + horizon_days.
- Pre-market backtests: not possible. No persistence pre-9 AM ET. Stream is display-only.
- Extended after-hours backtests (4-8 PM ET): partial — model_score captures still fire until 17:30 ET (covers the 16:00-17:30 post-close window). Nothing after 17:30 ET.

### Conditions to revisit timer extension
- Pre-market audit shows ≥ 3 tournament models behave reasonably outside RTH
- OR MM elects to capture-and-filter-later (cheaper) rather than gate at capture time

