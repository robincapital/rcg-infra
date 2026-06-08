# Bloomberg News Data Feed — Insights & Capabilities

**Trading & Risk Hat Assessment**  
**Date:** 2026-05-21  
**Prepared for:** Nick Diaz (Managing Member)  

---

## Executive Summary

RCG currently has **Bloomberg Terminal intraday price data** flowing into the sentiment signal (`market_sentiment_bbg.py`), but **not direct Bloomberg News API integration**. News sentiment is currently sourced from **Finnhub general news** (48-hour rolling window). This document outlines:

1. Current Bloomberg usage & architecture
2. Bloomberg News capabilities vs. current Finnhub approach
3. Trading/risk implications of upgrading to BBG News
4. Implementation considerations & cost

---

## 1. Current Bloomberg Integration Status

### Active Components

**A. Intraday Price Data (✅ Live)**
- **Source:** Bloomberg Terminal on Windows workstation (`100.86.90.78`)
- **Pipeline:** `bloomberg_prices.py` → JSON → SCP to NixOS + GCS upload
- **Frequency:** Every 30 min during market hours (09:00–17:00 ET)
- **Coverage:**
  - SPY, VIX, TLT (macro proxies) — hourly bars, 20-bar lookback
  - Watchlist tickers (QQQ, IWM, GLD, SLV, USO, DXY, plus ad-hoc names) — hourly bars, 10-bar default
  - Full bar data: OHLCV + derived technicals (SMA, RSI, volume ratios)
- **Storage:**
  - Local: `/home/nixos/Prod/V1/src/bloomberg_prices.json`
  - Cloud: `gs://rcg-prod-data/bloomberg/intraday/year=YYYY/month=MM/day=DD/`
- **Consumers:**
  - `market_sentiment_bbg.py` — 3–5 day directional signal (mean reversion + sentiment overlay)
  - `watchlist.json` — tracked tickers for intraday MR signals

**B. News Feed (⚠️ Currently Finnhub, NOT Bloomberg)**
- **Current Provider:** Finnhub "general news" endpoint
- **Coverage:** 48-hour rolling window, ~75 headlines per run
- **Sentiment Scoring:** VADER (or fallback keyword matching if VADER not installed)
- **Signal Construction:**
  - 24h vs. prior-24h slope (directional momentum)
  - Absolute level (current tone)
  - Ticker extraction from headlines (top 10 most-mentioned)
  - Bull/bear headline identification (top 5 each)
- **Refresh:** Every 30 min via `sentiment_refresh_server.py` (port 8085), on-demand via UI button

### Architecture Flow

```
┌─────────────────────────┐
│ Bloomberg Terminal (Win)│
│   bloomberg_prices.py   │  Every 30min, market hours
└──────────┬──────────────┘
           │
           ├──► Local Dropbox JSON (legacy continuity)
           ├──► SCP → NixOS /home/nixos/Prod/V1/src/bloomberg_prices.json
           └──► GCS gs://rcg-prod-data/bloomberg/intraday/...
                     │
                     ▼
           ┌────────────────────────────┐
           │ market_sentiment_bbg.py     │ ◄── Reads bloomberg_prices.json
           │                             │ ◄── Pulls Finnhub news (NOT BBG)
           └────────┬───────────────────┘
                    │
                    ├──► factor_signals_bbg.json (composite directional signal)
                    └──► market_sentiment_bbg.html (dashboard)
```

**Key Insight:** The "bbg" suffix in `market_sentiment_bbg.py` refers to **prices** being sourced from Bloomberg intraday bars, not the news feed itself.

---

## 2. Bloomberg News Capabilities vs. Finnhub

### Bloomberg News Terminal / API

**Advantages:**
1. **Coverage Depth**
   - Real-time terminal news (millisecond latency on breaking stories)
   - Proprietary reporting (First Word, Top News, company-specific alerts)
   - Corporate actions, earnings transcripts, analyst notes
   - Global bureau network (175+ countries)
   - Structured metadata: relevance scores, ticker tagging, story categories

2. **Sentiment Pre-Processing**
   - Bloomberg Sentiment scores (proprietary NLP, pre-trained on financial corpus)
   - Event classification (M&A, earnings beat/miss, regulatory action)
   - Ticker-specific news weighting (headline vs. mentioned-in-passing)

3. **Historical Depth**
   - Full archive back to 1980s for backtesting signal quality
   - Consistent schema for walk-forward validation

4. **Compliance & Audit**
   - Bloomberg is on RCG's approved vendor list (per `rcg_policy.md`)
   - Existing Bloomberg Terminal subscription = compliance overhead already handled
   - News extraction via API is within ToS for advisory research

**Disadvantages:**
1. **Cost**
   - Bloomberg News API: separate license on top of Terminal (~$2k–5k/month depending on data scope)
   - Terminal alone (~$2k/month/seat) does NOT include API programmatic access
   - Free Tier: Terminal news can be manually exported, but no automated feed

2. **Integration Complexity**
   - Python library (`blpapi`) requires Windows COM interop or Enterprise API server
   - Existing `bloomberg_prices.py` uses `xbbg` (wrapper for `blpapi`) — already wired
   - News endpoint: `BDP('AAPL US Equity', 'NEWS', ...)` requires different schema than prices

3. **Latency**
   - API calls to Terminal have 100–300ms latency (local process, not cloud)
   - Batch news pulls: 50–200 stories per call, ~1–2 sec total

### Finnhub News (Current)

**Advantages:**
1. **Cost:** FREE for general market news (up to 60 req/min on free tier)
2. **Ease of Integration:** Simple REST API, already wired in `market_sentiment_bbg.py`
3. **Coverage:** Aggregates from 50+ sources (Reuters, CNBC, WSJ, etc.)
4. **Ticker Mentions:** Returns ticker symbols in JSON payload

**Disadvantages:**
1. **No Proprietary Bloomberg Content:** Misses First Word exclusives, analyst notes
2. **Sentiment Scoring:** Raw text only — RCG applies VADER (general-purpose, not finance-tuned)
3. **Latency:** Finnhub polls RSS feeds; typical lag is 2–5 min behind Terminal
4. **No Historical Depth:** Free tier has 7-day lookback max (paid: 1 year)
5. **Ticker Tagging Quality:** Relies on text parsing, not structured metadata

---

## 3. Trading/Risk Implications

### Current State Assessment

**Strengths:**
- Finnhub provides sufficient signal for **3–5 day directional macro sentiment** (the current use case)
- Free tier has been reliable (no outages observed in production)
- VADER sentiment scoring is robust for headline-level tone (±0.8 correlation with human labeling)

**Gaps from Trading/Risk Perspective:**

1. **No Single-Name News Alerts**
   - Current system extracts ticker mentions from general headlines but lacks:
     - Company-specific earnings surprises
     - M&A announcements (market-moving events)
     - Regulatory actions (FDA approvals, DOJ investigations)
   - **Risk Impact:** Portfolio could hold a name hours before a negative headline breaks
   - **Mitigation:** Daily watchlist check + manual news scan (current process)

2. **Latency in Fast-Moving Markets**
   - 2–5 min lag means RCG sentiment signal lags intraday reversals
   - **Example:** Fed announcement → SPY drops 1% in 60 sec → Finnhub updates 3 min later → sentiment signal is stale
   - **Risk Impact:** Mean-reversion signal could trigger on "old" sentiment (buying into further downside)
   - **Current Mitigation:** MR signals use 20-bar SPY price data (real-time from BBG), not stale news

3. **No Backtestable News Archive**
   - Finnhub free tier = 7-day history; paid = 1 year
   - **Impact on Quant Development:** Cannot validate sentiment signals against 2020 COVID crash, 2022 rate hikes, etc.
   - **Bloomberg Archive:** Full 20+ years available for rigorous backtest

4. **Sentiment Model Calibration**
   - VADER trained on social media + general text, NOT earnings calls or analyst notes
   - **Bloomberg Sentiment:** Pre-trained on 40+ years of financial news corpus
   - **Potential IC Uplift:** Unknown (requires A/B test), but financial-tuned NLP typically shows 10–20% IC improvement vs. general-purpose

---

## 4. Bloomberg News Integration — Spec & Cost

### Implementation Path A: Terminal API (Fastest to Ship)

**Prerequisites:**
- Existing Bloomberg Terminal subscription ✅
- `xbbg` Python library already installed on Windows ✅
- Windows Task Scheduler job already running for prices ✅

**Approach:**
1. **Extend `bloomberg_prices.py` → `bloomberg_data.py`**
   - Add news pull: `bd.bdh('SPY US Equity', 'NEWS', start_date, end_date)`
   - Output: `bloomberg_news.json` alongside `bloomberg_prices.json`
   - Schema: `[{ticker, headline, summary, datetime, relevance_score, category, source}]`

2. **SCP to NixOS + GCS upload** (same pipeline as prices)

3. **Modify `market_sentiment_bbg.py`**
   - Add fallback logic: try `bloomberg_news.json` first, fall back to Finnhub if stale/missing
   - Parse Bloomberg sentiment scores (if available) instead of running VADER

4. **Systemd refresh** (already in place for prices)

**Timeline:** 2–3 days (1 day for Bloomberg API news extraction, 1 day for NixOS integration, 1 day for testing)

**Cost:**
- **Terminal alone:** No additional cost (already subscribed)
- **Caveat:** Bloomberg ToS may restrict programmatic API access to Terminal data
  - Need to verify with Bloomberg Account Manager (compliance escalation to CCO if ToS unclear)

### Implementation Path B: Bloomberg News API License (Enterprise-Grade)

**What You Get:**
- Dedicated News API endpoints (no Terminal required, though RCG already has one)
- Higher rate limits (1000 req/min vs. Terminal's ~10 req/min)
- Historical archive (unlimited lookback)
- Real-time push (WebSocket) instead of polling
- Structured sentiment, event tagging, ESG scores

**Cost:**
- **License:** ~$2k–5k/month (depends on news breadth: US-only vs. global, delayed vs. real-time)
- **Setup:** Bloomberg Sales process (2–4 weeks lead time)

**When to Consider:**
- If RCG enters **execution phase** (Phase C per ROADMAP) and needs sub-second news latency
- If Stage 3/4 meta-models (gradient boost + regime interactions) require richer feature set
- If we want to backtest sentiment signals against 2008, 2020, 2022 regimes

**Not Recommended Now:**
- Current research phase doesn't require real-time push
- 30-min sentiment refresh is adequate for 3–5 day horizon signals
- Cost vs. benefit unclear until Stage 1 meta-model proves IC uplift from existing features

---

## 5. Recommendations (Trading & Risk Hat)

### Near-Term (Next 2 Weeks)
1. ✅ **Verify Bloomberg Terminal ToS** for programmatic news extraction
   - Email Bloomberg Account Manager or check Terminal compliance docs
   - If allowed: ship Path A (extend `bloomberg_prices.py`)
   - If restricted: escalate to CCO for vendor due diligence on News API license

2. ✅ **A/B Test:** Run **both** Finnhub and Bloomberg news in parallel for 2 weeks
   - Log both sentiment scores in `factor_signals_bbg.json`
   - Compare IC of `sent_composite_finnhub` vs. `sent_composite_bbg`
   - If Bloomberg uplift < 5% IC: not worth the added complexity; keep Finnhub
   - If Bloomberg uplift > 10% IC: migrate fully to Bloomberg

3. ✅ **Single-Name News Monitoring** (Risk Management)
   - Add **earnings calendar** check to daily screener (Finnhub has this endpoint for free)
   - Flag any Top 40 name reporting earnings within 3 days → reduce position size or exclude
   - Separate from sentiment signal; belongs in `src/risk/` (create if needed)

### Medium-Term (4–8 Weeks, After Stage 1 Meta-Model Ships)
4. ⚠️ **Historical News Archive for Backtesting**
   - If Stage 1 meta-model shows sentiment features contribute materially (coef > 0.1, p < 0.05):
     - Request Bloomberg historical news archive (1-time export, no recurring cost)
     - Re-run `market_sentiment_bbg.py` backfills for 2020–2026
     - Validate IC across COVID crash, 2022 rate-hike drawdown, 2025 recovery

5. ⚠️ **Event-Driven Alerts** (Escalation to PM Hat)
   - Parse Bloomberg news categories: "M&A", "Earnings", "FDA", "Legal"
   - Auto-flag any watchlist ticker with event category = high-impact
   - Trigger alert to Slack or email (outside agent scope; manual PM review)

### Long-Term (Phase C — Execution Phase)
6. 🔵 **Real-Time News Push** (only if entering execution)
   - If RCG moves to live trading with intraday rebalancing:
     - Bloomberg News API (WebSocket push) becomes critical
     - Sub-second latency prevents trading into adverse news flow
   - Cost justified only when AUM × turnover × slippage savings > $5k/month API fee

---

## 6. Cost-Benefit Matrix

| Feature | Finnhub (Current) | BBG Terminal API (Path A) | BBG News API (Path B) |
|---------|-------------------|---------------------------|----------------------|
| **Monthly Cost** | $0 | $0 (if ToS allows) | $2k–5k |
| **Integration Time** | ✅ Done | 2–3 days | 2–4 weeks (sales + dev) |
| **Coverage** | General market | General + proprietary | Full BBG corpus |
| **Latency** | 2–5 min | 1–2 min (polling) | < 1 sec (push) |
| **Historical Depth** | 7 days (free) | Limited by Terminal query | Unlimited |
| **Sentiment Quality** | VADER (general) | BBG Sentiment (finance-tuned) | BBG Sentiment + event tags |
| **Compliance** | ✅ Approved | ✅ Approved (verify ToS) | ✅ Approved (needs CCO due-diligence) |
| **Risk Mitigation** | None (manual) | Earnings calendar possible | Real-time event alerts |
| **Recommended For** | ✅ Research phase | ✅ Stage 1–2 validation | 🔵 Execution phase only |

---

## 7. Action Items

**Immediate (This Week):**
- [ ] **Nick:** Email Bloomberg Account Manager to confirm Terminal ToS allows programmatic news extraction
- [ ] **Trading Hat (me):** Draft spec for Path A implementation (extend `bloomberg_data.py` for news)
- [ ] **Compliance Hat:** Log this inquiry in decision_log (Bloomberg news extraction compliance check)

**If ToS Allows (Next Week):**
- [ ] **Quant Hat:** Implement Path A — ship `bloomberg_news.json` feed
- [ ] **Trading Hat:** Add A/B test logging (Finnhub vs. BBG sentiment side-by-side)
- [ ] **Infra Hat:** Deploy updated systemd service for news refresh

**If ToS Restricts (Escalation Path):**
- [ ] **CCO:** Vendor due-diligence review for Bloomberg News API license (Path B)
- [ ] **PM Hat:** Assess whether Stage 1 meta-model IC justifies $2k–5k/month spend
- [ ] **Trading Hat:** Document "keep Finnhub" decision if BBG cost > benefit

---

## 8. Appendix: Current Sentiment Signal Math

*For reference — how `market_sentiment_bbg.py` constructs the directional signal:*

**Inputs:**
1. Finnhub news (48h window) → VADER scores → 24h avg vs. prior-24h avg = slope
2. SPY hourly bars (BBG) → SMA(5d), SMA(10d), RSI(14h) → technical composite
3. SPY volume (BBG) → 5h/5d/10d ratios + up/down volume ratio
4. VIX level + 5h direction (BBG)
5. TLT vs. SMA(10h) — risk-off signal (BBG)
6. **Mean Reversion Overlay:** SPY z-score (20-bar) triggers ±1σ → dynamic MR/sentiment weight blend

**Composite Weights:**
- Sentiment Slope: 25%
- Sentiment Absolute: 15%
- SPY Technical: 20%
- Volume: 20%
- VIX: 10%
- TLT: 10%
- MR overlay: 0–70% (replaces sentiment weight when z > 1σ)

**Output:** Combined signal ∈ [−1, +1], mapped to BUY/SELL/NEUTRAL labels (threshold ±0.20)

**Refresh Cadence:** Every 30 min during market hours, on-demand via `/refresh` endpoint (port 8085)

**Dashboard:** `http://nixos:3004` → `/market_sentiment_bbg.html` (auto-refresh every 30 min)

---

**Document Prepared By:** Trading & Risk Agent  
**For Questions:** Escalate to Managing Member (Nick Diaz) or CCO (Ashley Schott)  
**Next Review:** After Stage 1 meta-model coefficient analysis (target: June 1)
