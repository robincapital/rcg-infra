"""
market_sentiment_bbg.py  v1.0
RCG Market Directional Sentiment Signal — Bloomberg Intraday Edition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Identical to market_sentiment.py v2 EXCEPT:
  - SPY/VIX/TLT prices sourced from bloomberg_prices.json (hourly intraday)
    instead of Sharadar SFP closing prices
  - Runs on-demand or via cron after bloomberg_prices.py pushes fresh data
  - Outputs to market_sentiment_bbg.html (separate from closing-price version)

Signal inputs:
  1. Finnhub general news (48h) scored with VADER
  2. Sentiment slope: 24h vs prior 24h tone change
  3. SPY technicals: SMA 5d/10d hourly, RSI14 hourly
  4. Volume: SPY hourly vol vs 5h/5d/10d averages + up/down vol ratio
  5. VIX level + 5h direction (fear signal)
  6. TLT direction vs 10h SMA (flight-to-safety / risk-off signal)
  7. Top 10 most-mentioned tickers (bullish + bearish)

Output:
  - /home/nixos/Prod/V1/src/factor_signals_bbg.json
  - /home/nixos/Prod/V1/src/outputs/market_sentiment_bbg.html
"""

import os, json, re, math, requests
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
BBG_PRICES    = Path("/home/nixos/Prod/V1/src/bloomberg_prices.json")
JSON_OUT      = Path("/home/nixos/Prod/V1/src/factor_signals_bbg.json")
HTML_OUT      = Path("/home/nixos/Prod/V1/src/outputs/market_sentiment_bbg.html")

BUY_THRESHOLD  =  0.20
SELL_THRESHOLD = -0.20
MIN_HEADLINES  =  5

# Final composite weights
W_SENT_SLOPE  = 0.25
W_SENT_ABS    = 0.15
W_SPY_TECH    = 0.20
W_VOLUME      = 0.20
W_VIX         = 0.10
W_TLT         = 0.10

# Known large-cap tickers to scan for in headlines
WATCH_TICKERS = {
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","BRK","JPM",
    "V","UNH","XOM","JNJ","WMT","MA","PG","HD","CVX","MRK","ABBV","PEP",
    "KO","AVGO","COST","LLY","TMO","MCD","ACN","BAC","CRM","NEE","NKE",
    "DHR","TXN","PM","LIN","ORCL","AMD","QCOM","HON","RTX","UNP","GS",
    "CAT","AMAT","INTU","AMGN","SBUX","NOW","ISRG","GE","ADP","BKNG",
    "SPY","QQQ","IWM","TLT","GLD","BTC","ETH","COIN","MSTR","PLTR",
    "RIVN","LCID","GME","AMC","SOFI","HOOD","RBLX","SNAP","PINS",
    "ARM","SMCI","AI","SOUN","IONQ","RGTI","QUBT","MARA","RIOT","HUT",
}

# ── VADER ──────────────────────────────────────────────────────────────────────
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    def score_text(text: str) -> float:
        return _vader.polarity_scores(text)["compound"]
except ImportError:
    _BULL = {"surge","rally","gain","beat","strong","growth","record","bull",
             "buy","upgrade","positive","recovery","rebound","rise","breakout"}
    _BEAR = {"crash","drop","fall","recession","loss","miss","weak","bear",
             "sell","downgrade","negative","crisis","fear","decline","war",
             "conflict","closure","risk","concern","tariff","sanction"}
    def score_text(text: str) -> float:
        words = set(text.lower().split())
        bull = len(words & _BULL); bear = len(words & _BEAR)
        total = bull + bear
        return 0.0 if total == 0 else (bull - bear) / total

# ── Finnhub ────────────────────────────────────────────────────────────────────
def fetch_headlines(hours_back: int = 48) -> list[dict]:
    if not FINNHUB_KEY:
        raise ValueError("FINNHUB_API_KEY not set")
    url  = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_KEY}"
    resp = requests.get(url, timeout=10); resp.raise_for_status()
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp())
    return [
        {"datetime": i["datetime"], "headline": i.get("headline",""),
         "summary": i.get("summary",""), "source": i.get("source",""),
         "url": i.get("url","")}
        for i in resp.json() if i.get("datetime",0) >= cutoff_ts
    ]

def score_headlines(headlines: list[dict]) -> list[dict]:
    return [{**h, "score": score_text(f"{h['headline']}. {h['summary']}")}
            for h in headlines]

# ── Sentiment signals ──────────────────────────────────────────────────────────
def compute_sentiment_signals(scored: list[dict]) -> dict:
    now_ts   = int(datetime.now(timezone.utc).timestamp())
    cutoff24 = now_ts - 86400
    cutoff48 = now_ts - 86400 * 2

    recent = [h for h in scored if h["datetime"] >= cutoff24]
    prior  = [h for h in scored if cutoff48 <= h["datetime"] < cutoff24]

    sent_now  = sum(h["score"] for h in recent) / len(recent) if recent else 0.0
    sent_prev = sum(h["score"] for h in prior)  / len(prior)  if prior  else sent_now
    slope     = sent_now - sent_prev
    slope_norm = max(-1.0, min(1.0, slope / 0.5))

    by_score = sorted(scored, key=lambda x: x["score"])
    top_bear = [{"headline": h["headline"], "score": round(h["score"],3),
                 "source": h["source"], "url": h.get("url","")} for h in by_score[:5]]
    top_bull = [{"headline": h["headline"], "score": round(h["score"],3),
                 "source": h["source"], "url": h.get("url","")} for h in by_score[-5:][::-1]]

    return {
        "sent_now": round(sent_now,4), "sent_prev": round(sent_prev,4),
        "slope": round(slope,4), "slope_norm": round(slope_norm,4),
        "n_recent": len(recent), "n_prior": len(prior),
        "top_bull": top_bull, "top_bear": top_bear,
    }

# ── Ticker mentions ────────────────────────────────────────────────────────────
def extract_ticker_mentions(scored: list[dict]) -> list[dict]:
    """
    Scan headlines+summaries for known tickers.
    Returns top 10 by mention count with net sentiment.
    """
    counts  = defaultdict(int)
    sentsum = defaultdict(float)

    ticker_pattern = re.compile(r'\b([A-Z]{1,5})\b')

    for h in scored:
        text   = f"{h['headline']} {h['summary']}"
        found  = set(ticker_pattern.findall(text)) & WATCH_TICKERS
        for t in found:
            counts[t]  += 1
            sentsum[t] += h["score"]

    results = []
    for ticker, count in sorted(counts.items(), key=lambda x: -x[1])[:10]:
        avg_sent = sentsum[ticker] / count
        results.append({
            "ticker":   ticker,
            "mentions": count,
            "avg_sent": round(avg_sent, 3),
            "bias":     "BULL" if avg_sent > 0.05 else ("BEAR" if avg_sent < -0.05 else "NEUTRAL"),
        })
    return results



# ── Load Bloomberg intraday prices ─────────────────────────────────────────────
def load_bbg_prices() -> dict:
    if not BBG_PRICES.exists():
        print(f"[WARN] {BBG_PRICES} not found — ETF signals will be zero")
        return {}
    with open(BBG_PRICES) as f:
        data = json.load(f)
    age_mins = (datetime.now() - datetime.fromisoformat(
        data["generated_at"].replace("Z",""))).total_seconds() / 60
    print(f"  Bloomberg prices age: {age_mins:.0f} min (generated {data['generated_at'][:16]})")
    if age_mins > 120:
        print(f"  [WARN] Bloomberg prices are stale ({age_mins:.0f} min old)")
    result = dict(data.get("tickers", {}))
    result["watchlist"] = data.get("watchlist", {})
    return result

def get_spy_signals(bbg: dict) -> dict:
    spy = bbg.get("SPY", {})
    if not spy or "error" in spy:
        return {"sma_signal":0.0,"rsi_signal":0.0,"vol_signal":0.0,
                "spy_composite":0.0,"price":None}
    return {
        "price":         spy.get("price"),
        "sma20":         spy.get("sma_5d"),
        "sma50":         spy.get("sma_10d"),
        "rsi14":         spy.get("rsi14"),
        "vol_now":       spy.get("vol_now"),
        "vol5_avg":      spy.get("vol5h_avg"),
        "vol10_avg":     spy.get("vol5d_avg"),
        "vol50_avg":     spy.get("vol10d_avg"),
        "vol5_ratio":    spy.get("vol5h_ratio"),
        "vol10_ratio":   spy.get("vol10d_ratio"),
        "udv_ratio":     spy.get("udv_ratio"),
        "sma_signal":    spy.get("sma_signal", 0.0),
        "rsi_signal":    spy.get("rsi_signal", 0.0),
        "vol_signal":    spy.get("vol_signal", 0.0),
        "spy_composite": spy.get("spy_composite", 0.0),
        "source":        "bloomberg_intraday",
        "last_bar_time": spy.get("last_bar_time"),
    }

def get_vix_signal(bbg: dict) -> dict:
    vix = bbg.get("VIX", {})
    if not vix or "error" in vix:
        return {"vix":None,"vix_signal":0.0}
    return {
        "vix":        vix.get("vix"),
        "vix5_avg":   vix.get("vix5h_avg"),
        "vix_signal": vix.get("vix_signal", 0.0),
        "source":     "bloomberg_intraday",
    }

def get_tlt_signal(bbg: dict) -> dict:
    tlt = bbg.get("TLT", {})
    if not tlt or "error" in tlt:
        return {"tlt":None,"tlt_signal":0.0}
    return {
        "tlt":        tlt.get("tlt"),
        "tlt_sma10":  tlt.get("tlt_sma10h"),
        "tlt_signal": tlt.get("tlt_signal", 0.0),
        "source":     "bloomberg_intraday",
    }

# ── Mean Reversion Signal ───────────────────────────────────────────────────────
MR_LOOKBACK   = 20    # hourly bars (~3 trading days)
MR_1SIGMA     = 1.0   # signal activates
MR_2SIGMA     = 2.0   # full strength (70% weight)
MR_3SIGMA     = 3.0   # extreme flag

def compute_mean_reversion(bbg: dict) -> dict:
    """SPY MR signal for composite blending. Uses _compute_mr_for_ticker internally."""
    spy_data = bbg.get("SPY", {})
    bars = spy_data.get("bars", [])
    if len(bars) < 10:
        return {"active": False, "z_score": 0.0, "label": "INACTIVE",
                "signal": 0.0, "mr_weight": 0.0, "sent_weight": 1.0,
                "mean": None, "std": None,
                "current_price": spy_data.get("price"),
                "levels": {}, "note": "insufficient bars"}
    result = _compute_mr_for_ticker("SPY", bars, MR_LOOKBACK)
    trend = result.get("trend_strength", 0.0)
    raw   = result.get("mr_weight_raw", result["mr_weight"])
    note  = (f"z={result['z_score']:+.2f}σ  "
             f"MR weight={result['mr_weight']*100:.0f}%  "
             f"Sent weight={result['sent_weight']*100:.0f}%")
    if trend > 0.05 and raw > 0:
        note += f"  trend={trend:.2f} (MR raw was {raw*100:.0f}%, reduced)"
    result["note"] = note
    return result


# ── Composite (MR-aware) ────────────────────────────────────────────────────────
def build_composite(sent: dict, spy: dict, vix: dict, tlt: dict,
                    mr: dict, n_headlines: int) -> dict:
    """
    Combine sentiment + technicals + mean reversion.
    MR signal dynamically adjusts weighting when price is out of range.
    """
    sent_abs_norm = max(-1.0, min(1.0, sent["sent_now"] / 0.3))

    # Base sentiment composite (unchanged logic)
    sent_composite = (
        W_SENT_SLOPE * sent["slope_norm"] +
        W_SENT_ABS   * sent_abs_norm +
        W_SPY_TECH   * spy.get("spy_composite", 0.0) +
        W_VOLUME     * spy.get("vol_signal", 0.0) +
        W_VIX        * vix.get("vix_signal", 0.0) +
        W_TLT        * tlt.get("tlt_signal", 0.0)
    )
    sent_composite = round(sent_composite, 4)

    # MR blending
    mr_weight   = mr.get("mr_weight", 0.0)
    sent_weight = mr.get("sent_weight", 1.0)
    mr_signal   = mr.get("signal", 0.0)

    combined = round(sent_weight * sent_composite + mr_weight * mr_signal, 4)

    # Confidence
    count_conf = min(1.0, n_headlines / 20)
    signals    = [sent["slope_norm"], sent_abs_norm,
                  spy.get("spy_composite", 0.0), vix.get("vix_signal", 0.0),
                  tlt.get("tlt_signal", 0.0)]
    same_sign  = sum(1 for s in signals if s != 0 and (s > 0) == (combined > 0))
    agreement  = same_sign / max(1, len([s for s in signals if s != 0]))
    confidence = round((count_conf * 0.4 + agreement * 0.6) * 100, 1)

    sent_label = ("BUY"  if sent_composite >= BUY_THRESHOLD else
                  "SELL" if sent_composite <= SELL_THRESHOLD else "NEUTRAL")
    comb_label = ("BUY"  if combined >= BUY_THRESHOLD else
                  "SELL" if combined <= SELL_THRESHOLD else "NEUTRAL")

    return {
        "label":           comb_label,
        "sent_label":      sent_label,
        "composite":       combined,
        "sent_composite":  round(sent_composite, 4),
        "mr_signal":       mr_signal,
        "mr_weight":       mr_weight,
        "sent_weight":     sent_weight,
        "confidence":      confidence,
        "components": {
            "sentiment_slope": round(W_SENT_SLOPE * sent["slope_norm"], 4),
            "sentiment_abs":   round(W_SENT_ABS   * sent_abs_norm, 4),
            "spy_technical":   round(W_SPY_TECH   * spy.get("spy_composite", 0.0), 4),
            "volume":          round(W_VOLUME     * spy.get("vol_signal", 0.0), 4),
            "vix":             round(W_VIX        * vix.get("vix_signal", 0.0), 4),
            "tlt":             round(W_TLT        * tlt.get("tlt_signal", 0.0), 4),
        }
    }


# ── HTML dashboard ─────────────────────────────────────────────────────────────
def render_html(signal: dict) -> str:
    result     = signal.get("result", {})
    mr         = signal.get("mr", {})
    sent       = signal.get("sentiment", {})
    spy        = signal.get("spy", {})
    vix        = signal.get("vix", {})
    tlt        = signal.get("tlt", {})
    top_bull   = signal.get("top_bull", [])
    top_bear   = signal.get("top_bear", [])
    tickers    = signal.get("top_tickers", [])
    gen_at     = signal.get("generated_at", "")
    n_h        = signal.get("n_headlines", 0)

    comb_label = result.get("label", "NEUTRAL")
    sent_label = result.get("sent_label", "NEUTRAL")
    mr_label   = mr.get("label", "INACTIVE")
    composite  = result.get("composite", 0.0)
    sent_comp  = result.get("sent_composite", 0.0)
    mr_signal  = result.get("mr_signal", 0.0)
    confidence = result.get("confidence", 0.0)
    mr_weight  = result.get("mr_weight", 0.0)
    sent_weight= result.get("sent_weight", 1.0)
    comp       = result.get("components", {})

    def label_color(lbl):
        return {"BUY":"#00c97a","SELL":"#ff4d4d","NEUTRAL":"#f0a500",
                "WATCH":"#f0a500","EXTREME":"#ff00ff","INACTIVE":"#555"}.get(lbl,"#aaa")

    cc = label_color(comb_label)
    sc = label_color(sent_label)
    mc = label_color(mr_label)

    bar_pct = int((composite + 1) / 2 * 100)

    # MR levels section
    levels   = mr.get("levels", {})
    mr_price = mr.get("current_price", "—")
    mr_mean  = mr.get("mean", "—")
    mr_std   = mr.get("std", "—")
    z_score  = mr.get("z_score", 0.0)
    entry    = mr.get("entry_zone", ("—","—"))
    full_sig = mr.get("full_signal", "—")
    exit_px  = mr.get("exit_price", "—")
    extreme  = mr.get("extreme", "—")
    trigger  = mr.get("trigger", "—")
    n_bars   = mr.get("n_bars", 0)

    mr_active = mr.get("active", False)

    def mr_panel(m, lookback_label="20-bar"):
        """Render a bidirectional MR guidance panel for any ticker."""
        lbl    = m.get("label", "INACTIVE")
        lc     = label_color(lbl)
        active = m.get("active", False)
        z      = m.get("z_score", 0.0)
        price  = m.get("current_price", "—")
        mean_  = m.get("mean", "—")
        std_   = m.get("std", "—")
        nb     = m.get("n_bars", 0)
        lvls   = m.get("levels", {})
        mw     = m.get("mr_weight", 0.0)
        sw     = m.get("sent_weight", 1.0)
        ent    = m.get("entry_zone", ("—","—"))
        fsig   = m.get("full_signal", "—")
        ex     = m.get("exit_price", "—")
        xtr    = m.get("extreme", "—")
        trig   = m.get("trigger", "—")
        is_long = z < 0  # BUY reversion when below mean

        if active:
            side_label = ("LONG / BUY REVERSION" if is_long
                          else "SHORT / SELL REVERSION")
            entry_label = ("Scale long between 1σ–2σ below mean" if is_long
                           else "Scale short between 1σ–2σ above mean")
            full_label  = ("2σ below — max long, 70% MR weight" if is_long
                           else "2σ above — max short, 70% MR weight")
            exit_label  = "Cover / take profit at mean" if not is_long else "Exit long at mean"
            extreme_sub = ("3σ below — add to long" if is_long
                           else "3σ above — add to short")
            exit_color  = "#ff4d4d" if not is_long else "#00c97a"
            return f"""
            <div style="background:#0f0f0f;border:1px solid {lc};border-radius:4px;padding:14px;margin-top:8px">
              <div style="color:{lc};font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">
                {side_label} — z={z:+.2f}σ &nbsp;|&nbsp;
                Sentiment {sw*100:.0f}% / MR {mw*100:.0f}%
              </div>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">1σ Trigger</div>
                  <div style="color:#fff;font-size:16px;font-weight:700">${trig}</div>
                  <div style="color:#555;font-size:10px">Signal activated here</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Entry Zone</div>
                  <div style="color:{lc};font-size:16px;font-weight:700">${ent[0]} – ${ent[1]}</div>
                  <div style="color:#555;font-size:10px">{entry_label}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Full Signal (2σ)</div>
                  <div style="color:{lc};font-size:16px;font-weight:700">${fsig}</div>
                  <div style="color:#555;font-size:10px">{full_label}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Exit Target</div>
                  <div style="color:{exit_color};font-size:16px;font-weight:700">${ex}</div>
                  <div style="color:#555;font-size:10px">{exit_label}</div></div>
              </div>
              <div style="margin-top:10px;display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Extreme (3σ)</div>
                  <div style="color:#ff00ff;font-size:14px;font-weight:700">${xtr}</div>
                  <div style="color:#555;font-size:10px">{extreme_sub}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">{lookback_label} Mean</div>
                  <div style="color:#aaa;font-size:14px;font-weight:600">${mean_}</div>
                  <div style="color:#555;font-size:10px">σ = {std_}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Current Price</div>
                  <div style="color:#fff;font-size:14px;font-weight:600">${price}</div>
                  <div style="color:#555;font-size:10px">{nb} bars in window</div></div>
              </div>
            </div>"""
        else:
            dist_up = round(lvls.get("1sigma_up",0) - (price or 0), 2) if price and price != "—" else "—"
            dist_dn = round((price or 0) - lvls.get("1sigma_dn",0), 2) if price and price != "—" else "—"
            return f"""
            <div style="background:#0f0f0f;border:1px solid #2a2a2a;border-radius:4px;padding:14px;margin-top:8px">
              <div style="color:#555;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">
                MR INACTIVE — within 1σ (z={z:+.2f}σ)
              </div>
              <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px">
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Current Price</div>
                  <div style="color:#fff;font-size:15px;font-weight:700">${price}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">{lookback_label} Mean</div>
                  <div style="color:#aaa;font-size:15px;font-weight:600">${mean_}</div>
                  <div style="color:#555;font-size:10px">σ = {std_}</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Short Trigger ↑</div>
                  <div style="color:#ff4d4d;font-size:15px;font-weight:700">${lvls.get("1sigma_up","—")}</div>
                  <div style="color:#555;font-size:10px">${dist_up} away</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Long Trigger ↓</div>
                  <div style="color:#00c97a;font-size:15px;font-weight:700">${lvls.get("1sigma_dn","—")}</div>
                  <div style="color:#555;font-size:10px">${dist_dn} away</div></div>
                <div><div style="color:#555;font-size:10px;text-transform:uppercase">Bars</div>
                  <div style="color:#555;font-size:15px;font-weight:600">{nb}</div></div>
              </div>
              <div style="margin-top:8px;color:#3a3a3a;font-size:10px">
                2σ: ${lvls.get("2sigma_dn","—")} / ${lvls.get("2sigma_up","—")} &nbsp;·&nbsp;
                3σ: ${lvls.get("3sigma_dn","—")} / ${lvls.get("3sigma_up","—")}
              </div>
            </div>"""

    mr_guidance = mr_panel(mr, lookback_label="20-bar")

    def comp_bar(val, weight, label_text, footnote):
        pct   = int((val + 1) / 2 * 100)
        color = "#00c97a" if val > 0 else ("#ff4d4d" if val < 0 else "#666")
        contrib = comp.get(label_text.lower().replace(" ","_").replace("/",""), 0.0)
        return f"""
        <div class="comp-row">
          <div class="comp-label">{label_text} <span class="weight">({int(weight*100)}% weight)</span></div>
          <div class="comp-bar-wrap"><div class="comp-bar" style="width:{pct}%;background:{color}"></div></div>
          <div class="comp-val" style="color:{color}">{val:+.3f}</div>
          <div class="comp-contrib">contribution: {contrib:+.4f}</div>
          <div class="footnote">ⓘ {footnote}</div>
        </div>"""

    def ticker_rows(tickers):
        rows = ""
        for t in tickers:
            color = "#00c97a" if t["bias"]=="BULL" else ("#ff4d4d" if t["bias"]=="BEAR" else "#aaa")
            rows += f"""<tr>
              <td style="color:#fff;font-weight:600">{t["ticker"]}</td>
              <td>{t["mentions"]}</td>
              <td style="color:{color}">{t["bias"]}</td>
              <td style="color:{color}">{t["avg_sent"]:+.3f}</td></tr>"""
        return rows

    def headline_rows(items, color):
        rows = ""
        for h in items:
            url = h.get("url","#")
            rows += f"""<tr>
              <td><a href="{url}" target="_blank" style="color:{color};text-decoration:none">
                {h["headline"][:90]}{"…" if len(h["headline"])>90 else ""}
              </a></td>
              <td style="color:#aaa;font-size:11px">{h["source"]}</td>
              <td style="color:{color};font-weight:600">{h["score"]:+.3f}</td></tr>"""
        return rows

    vix_val = vix.get("vix","—"); tlt_val = tlt.get("tlt","—")
    rsi_val = spy.get("rsi14","—"); sma20 = spy.get("sma20","—")
    sma50   = spy.get("sma50","—"); spy_px = spy.get("price","—")
    v5r = spy.get("vol5_ratio","—"); udv = spy.get("udv_ratio","—")
    sent_now = sent.get("sent_now",0); sent_prev = sent.get("sent_prev",0)
    slope    = sent.get("slope",0)

    # ── Watchlist grid ──────────────────────────────────────────────────────
    wl_data = signal.get("watchlist", {})

    def wl_summary_row(ticker, m):
        """One-line summary row for the grid table."""
        lbl    = m.get("label", "INACTIVE")
        lc     = label_color(lbl)
        z      = m.get("z_score", 0.0)
        price  = m.get("current_price", "—")
        mean_  = m.get("mean", "—")
        trig   = m.get("trigger", "—")
        ent    = m.get("entry_zone", ("—","—"))
        fsig   = m.get("full_signal", "—")
        ex     = m.get("exit_price", "—")
        xtr    = m.get("extreme", "—")
        nb     = m.get("n_bars", 0)
        lb     = m.get("lookback", 10)
        note   = m.get("ticker_note", "")
        bps    = m.get("bps_target")
        is_long = z < 0
        side   = ("LONG" if is_long else "SHORT") if m.get("active") else "—"
        side_c = "#00c97a" if is_long else ("#ff4d4d" if m.get("active") else "#555")
        exit_c = "#00c97a" if is_long else "#ff4d4d"
        bps_str = (f"+{bps:.0f}" if bps else "—") if m.get("active") else "—"
        bps_c   = "#00c97a" if is_long else "#ff4d4d"
        err    = m.get("error", "")
        if err:
            return f"""<tr>
              <td style="color:#fff;font-weight:700">{ticker}</td>
              <td colspan="9" style="color:#555;font-size:11px">{err}</td></tr>"""
        return f"""<tr style="border-bottom:1px solid #1a1a1a">
          <td style="color:#fff;font-weight:700">{ticker}
            {"<br><span style='color:#555;font-size:10px'>"+note+"</span>" if note else ""}</td>
          <td style="color:#fff">${price}</td>
          <td style="color:{lc};font-weight:700">{lbl}</td>
          <td style="color:{side_c};font-weight:600">{side}</td>
          <td style="color:{lc}">{z:+.2f}σ</td>
          <td style="color:#aaa">${mean_}</td>
          <td style="color:{lc}">${ent[0]} – ${ent[1]}</td>
          <td style="color:{exit_c}">${ex}</td>
          <td style="color:{bps_c};font-weight:600">{bps_str} bps</td>
          <td style="color:#ff00ff;font-size:11px">${xtr}</td>
        </tr>"""

    def wl_detail_panel(ticker, m):
        """Expandable detail panel per ticker using the shared mr_panel()."""
        err = m.get("error","")
        if err:
            return ""
        lb_label = f"{m.get('lookback',10)}-bar"
        return f"""
        <tr id="detail-{ticker}" style="display:none">
          <td colspan="10" style="padding:0 0 12px 0">
            {mr_panel(m, lookback_label=lb_label)}
          </td>
        </tr>"""

    if wl_data:
        wl_rows = ""
        wl_details = ""
        for tk, m in wl_data.items():
            wl_rows    += wl_summary_row(tk, m)
            wl_details += wl_detail_panel(tk, m)

        watchlist_grid = f"""
        <table>
          <thead><tr>
            <th>Ticker</th><th>Price</th><th>Signal</th><th>Side</th>
            <th>z-score</th><th>Mean</th><th>Entry Zone</th>
            <th>Exit Target</th><th>Est. bps</th><th>3σ Extreme</th>
          </tr></thead>
          <tbody>
            {wl_rows}
            {wl_details}
          </tbody>
        </table>
        <div style="color:#444;font-size:10px;margin-top:6px">
          Click any row to expand full level detail
        </div>
        <script>
          document.querySelectorAll('tbody tr[style*="border-bottom"]').forEach(row => {{
            row.style.cursor = 'pointer';
            row.addEventListener('click', function() {{
              const ticker = this.querySelector('td:first-child').textContent.trim().split('\\n')[0].trim();
              const detail = document.getElementById('detail-' + ticker);
              if (detail) {{
                detail.style.display = detail.style.display === 'none' ? 'table-row' : 'none';
              }}
            }});
          }});
        </script>"""
    else:
        watchlist_grid = """<div style="color:#555;padding:12px">
          No watchlist data — ensure bloomberg_prices.json contains watchlist key
          and watchlist.json exists at /home/nixos/Prod/V1/src/watchlist.json</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="1800">
<title>RCG Market Sentiment — Bloomberg Intraday</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ background:#0d0d0d; color:#ddd; font-family:"Segoe UI",sans-serif; font-size:13px; padding:20px }}
  h1 {{ color:#c9a84c; font-size:20px; letter-spacing:2px; margin-bottom:4px }}
  .subtitle {{ color:#666; font-size:11px; margin-bottom:20px }}
  .stamps {{ display:flex; gap:20px; margin-bottom:20px; flex-wrap:wrap }}
  .stamp {{ padding:10px 20px; border-radius:4px; text-align:center; min-width:180px }}
  .stamp-label {{ font-size:10px; text-transform:uppercase; letter-spacing:2px; color:#888; margin-bottom:4px }}
  .stamp-val {{ font-size:28px; font-weight:800; letter-spacing:3px }}
  .stamp-sub {{ font-size:11px; color:#666; margin-top:3px }}
  .progress-wrap {{ background:#222; border-radius:3px; height:8px; width:320px; margin-bottom:24px }}
  .section {{ margin-bottom:28px }}
  .section-title {{ color:#c9a84c; font-size:12px; letter-spacing:2px; text-transform:uppercase;
                    border-bottom:1px solid #2a2a2a; padding-bottom:6px; margin-bottom:14px }}
  .stats-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:16px }}
  .stat-box {{ background:#141414; border:1px solid #222; border-radius:4px; padding:12px }}
  .stat-label {{ color:#666; font-size:10px; text-transform:uppercase; letter-spacing:1px }}
  .stat-val {{ color:#fff; font-size:18px; font-weight:700; margin-top:4px }}
  .stat-sub {{ color:#555; font-size:10px; margin-top:2px }}
  .comp-row {{ margin-bottom:12px }}
  .comp-label {{ color:#aaa; font-size:11px; margin-bottom:3px }}
  .weight {{ color:#555 }}
  .comp-bar-wrap {{ background:#1a1a1a; border-radius:2px; height:6px; width:100%; margin-bottom:3px }}
  .comp-bar {{ height:6px; border-radius:2px; min-width:2px }}
  .comp-val {{ font-size:12px; font-weight:600; display:inline }}
  .comp-contrib {{ font-size:11px; color:#555; display:inline; margin-left:12px }}
  .footnote {{ color:#444; font-size:10px; font-style:italic; margin-top:2px }}
  table {{ width:100%; border-collapse:collapse }}
  th {{ color:#666; font-size:10px; text-transform:uppercase; letter-spacing:1px;
        text-align:left; padding:6px 8px; border-bottom:1px solid #222 }}
  td {{ padding:6px 8px; border-bottom:1px solid #1a1a1a; vertical-align:top }}
  .timestamp {{ color:#444; font-size:10px; margin-top:24px }}
  .refresh-bar {{ position:fixed; top:0; left:0; right:0; background:#0a0a0a;
    border-bottom:1px solid #222; padding:8px 20px; display:flex;
    align-items:center; justify-content:space-between; z-index:100 }}
  .refresh-btn {{ background:#c9a84c; color:#0d0d0d; border:none; padding:8px 20px;
    border-radius:4px; font-weight:700; font-size:12px; cursor:pointer;
    letter-spacing:1px; text-transform:uppercase; transition:all 0.2s }}
  .refresh-btn:hover {{ background:#e0bc5c }}
  .refresh-btn:disabled {{ background:#333; color:#666; cursor:not-allowed }}
  .refresh-status {{ color:#666; font-size:11px }}
  .refresh-status.active {{ color:#c9a84c }}
  .refresh-status.done {{ color:#00c97a }}
  .refresh-status.error {{ color:#ff4d4d }}
  .refresh-timer {{ color:#555; font-size:11px; font-family:monospace }}
  body {{ padding-top:50px }}
</style>
</head>
<body>

<div class="refresh-bar">
  <div style="display:flex;align-items:center;gap:16px">
    <button class="refresh-btn" id="refreshBtn" onclick="triggerRefresh()">
      Refresh Data
    </button>
    <span class="refresh-status" id="refreshStatus">Ready</span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span class="refresh-timer" id="dataAge"></span>
  </div>
</div>

<h1>RCG MARKET SENTIMENT — BLOOMBERG INTRADAY <span style="font-size:13px;color:#555;font-weight:400;letter-spacing:1px">· Last updated: {gen_at[:16].replace("T"," ")} UTC</span></h1>
<div class="subtitle">3–5 Day Directional Signal · {n_h} headlines · Updates every 30 min · Bloomberg hourly bars</div>

<!-- Three stamps -->
<div class="stamps">
  <div class="stamp" style="border:2px solid {sc}">
    <div class="stamp-label">Sentiment</div>
    <div class="stamp-val" style="color:{sc}">{sent_label}</div>
    <div class="stamp-sub">score: {sent_comp:+.3f} · slow decay</div>
  </div>
  <div class="stamp" style="border:2px solid {mc}">
    <div class="stamp-label">Mean Reversion</div>
    <div class="stamp-val" style="color:{mc}">{mr_label}</div>
    <div class="stamp-sub">z={z_score:+.2f}σ · fast decay · weight {mr_weight*100:.0f}%</div>
  </div>
  <div class="stamp" style="border:2px solid {cc}">
    <div class="stamp-label">Combined Signal</div>
    <div class="stamp-val" style="color:{cc}">{comb_label}</div>
    <div class="stamp-sub">score: {composite:+.3f} · conf: {confidence}%</div>
  </div>
</div>

<div class="progress-wrap">
  <div style="height:8px;border-radius:3px;background:{cc};width:{bar_pct}%"></div>
</div>

<!-- MR Grid — all watchlist tickers including SPY -->
<div class="section">
  <div class="section-title">Mean Reversion Grid — Watchlist</div>
  {watchlist_grid}
  <div class="footnote" style="margin-top:8px">
    ⓘ Rolling mean/std of hourly closes (SPY: 20-bar, others: 10-bar by default, configurable per ticker).
    LONG = price below mean (BUY reversion). SHORT = price above mean (SELL reversion).
    Entry zone = 1σ–2σ from mean. Exit target = mean (full reversion). bps = estimated return entry mid → exit.
    Signal activates at ±1σ, full strength at ±2σ, extreme flag at ±3σ.
    Edit /home/nixos/Prod/V1/src/watchlist.json to change tickers or lookback.
  </div>
</div>

<!-- Market snapshot -->
<div class="section">
  <div class="section-title">Market Snapshot</div>
  <div class="stats-grid">
    <div class="stat-box"><div class="stat-label">SPY Price</div>
      <div class="stat-val">${spy_px}</div>
      <div class="stat-sub">SMA5d: {sma20} · SMA10d: {sma50}</div></div>
    <div class="stat-box"><div class="stat-label">SPY RSI (14h)</div>
      <div class="stat-val">{rsi_val}</div>
      <div class="stat-sub">&lt;35 oversold · &gt;70 overbought</div></div>
    <div class="stat-box"><div class="stat-label">VIX</div>
      <div class="stat-val">{vix_val}</div>
      <div class="stat-sub">5h avg: {vix.get("vix5_avg","—")}</div></div>
    <div class="stat-box"><div class="stat-label">TLT Price</div>
      <div class="stat-val">${tlt_val}</div>
      <div class="stat-sub">SMA10h: {tlt.get("tlt_sma10","—")}</div></div>
    <div class="stat-box"><div class="stat-label">Vol / 5h Avg</div>
      <div class="stat-val">{v5r}x</div>
      <div class="stat-sub">vs 5d baseline</div></div>
    <div class="stat-box"><div class="stat-label">Up/Down Vol</div>
      <div class="stat-val">{udv}</div>
      <div class="stat-sub">Last 13 bars · 0.5=neutral</div></div>
    <div class="stat-box"><div class="stat-label">Sentiment Slope</div>
      <div class="stat-val" style="color:{"#00c97a" if slope>0 else "#ff4d4d"}">{slope:+.3f}</div>
      <div class="stat-sub">Now: {sent_now:+.3f} · Prev: {sent_prev:+.3f}</div></div>
    <div class="stat-box"><div class="stat-label">MR Weight</div>
      <div class="stat-val" style="color:{"#f0a500" if mr_weight>0 else "#555"}">{mr_weight*100:.0f}%</div>
      <div class="stat-sub">Sent weight: {sent_weight*100:.0f}%</div></div>
  </div>
</div>

<!-- Signal components -->
<div class="section">
  <div class="section-title">Sentiment Components (before MR overlay)</div>
  {comp_bar(sent.get("slope_norm",0), W_SENT_SLOPE, "Sentiment Slope",
    "VADER compound score: 24h vs prior 24h. Positive = improving tone. Normalized to [-1,1]. Weight: 25%.")}
  {comp_bar(max(-1.0,min(1.0,sent_now/0.3)), W_SENT_ABS, "Sentiment Absolute",
    "Current 24h average VADER score normalized by 0.3. Captures current news tone level. Weight: 15%.")}
  {comp_bar(spy.get("spy_composite",0), W_SPY_TECH, "SPY Technical",
    "SMA 5d/10d hourly positioning (40%) + RSI14 hourly (35%) + volume (25%). Bloomberg intraday. Weight: 20%.")}
  {comp_bar(spy.get("vol_signal",0), W_VOLUME, "Volume",
    "5h avg vs 5d/10d baseline. Up/down volume ratio last 13 hourly bars. Expansion on up bars = bullish. Weight: 20%.")}
  {comp_bar(vix.get("vix_signal",0), W_VIX, "VIX",
    "Level: <15 calm (+1.0), >35 extreme fear (-1.0). Direction vs 5h avg. Level (60%) + direction (40%). Weight: 10%.")}
  {comp_bar(tlt.get("tlt_signal",0), W_TLT, "TLT Risk-Off",
    "TLT above SMA10h = bonds rallying = flight to safety = bearish equities. Weight: 10%.")}
</div>

<!-- Top tickers -->
<div class="section">
  <div class="section-title">Top 10 Mentioned Tickers</div>
  <table>
    <thead><tr><th>Ticker</th><th>Mentions</th><th>Bias</th><th>Avg Sentiment</th></tr></thead>
    <tbody>{ticker_rows(tickers)}</tbody>
  </table>
  <div class="footnote" style="margin-top:8px">
    ⓘ Tickers extracted from headline+summary text. BULL &gt; +0.05, BEAR &lt; −0.05.
  </div>
</div>

<!-- Headlines -->
<div class="section">
  <div class="section-title">Top 5 Bullish Headlines</div>
  <table>
    <thead><tr><th>Headline</th><th>Source</th><th>Score</th></tr></thead>
    <tbody>{headline_rows(top_bull, "#00c97a")}</tbody>
  </table>
</div>
<div class="section">
  <div class="section-title">Top 5 Bearish Headlines</div>
  <table>
    <thead><tr><th>Headline</th><th>Source</th><th>Score</th></tr></thead>
    <tbody>{headline_rows(top_bear, "#ff4d4d")}</tbody>
  </table>
  <div class="footnote" style="margin-top:8px">
    ⓘ VADER compound score: +1.0 (most positive) to −1.0 (most negative). Scores below −0.05 = negative.
  </div>
</div>

<div class="timestamp">
  Last generated: {gen_at} UTC &nbsp;·&nbsp;
  Bloomberg intraday — updates hourly during market hours &nbsp;·&nbsp;
  Source: Finnhub general news + Bloomberg Intraday (SPY/VIX/TLT hourly)
</div>
<script>
const REFRESH_URL = 'http://' + window.location.hostname + ':8085';
let pollInterval = null;

function triggerRefresh() {{
  const btn = document.getElementById('refreshBtn');
  const status = document.getElementById('refreshStatus');
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  status.textContent = 'Pulling Bloomberg prices...';
  status.className = 'refresh-status active';

  fetch(REFRESH_URL + '/refresh')
    .then(r => r.json())
    .then(data => {{
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(checkStatus, 2000);
    }})
    .catch(err => {{
      status.textContent = 'Refresh server not running';
      status.className = 'refresh-status error';
      btn.disabled = false;
      btn.textContent = 'Refresh Data';
    }});
}}

function checkStatus() {{
  fetch(REFRESH_URL + '/status')
    .then(r => r.json())
    .then(data => {{
      const s = data.state;
      const status = document.getElementById('refreshStatus');
      const btn = document.getElementById('refreshBtn');

      if (s.status === 'complete') {{
        status.textContent = 'Updated! Reloading...';
        status.className = 'refresh-status done';
        clearInterval(pollInterval);
        setTimeout(() => window.location.reload(), 1500);
      }} else if (s.status === 'error') {{
        status.textContent = 'Error: ' + (s.last_error || 'unknown').substring(0, 80);
        status.className = 'refresh-status error';
        btn.disabled = false;
        btn.textContent = 'Refresh Data';
        clearInterval(pollInterval);
      }} else if (s.running) {{
        status.textContent = s.status || 'Refreshing...';
        status.className = 'refresh-status active';
      }}
    }})
    .catch(() => {{}});
}}

function updateAge() {{
  const genEl = document.querySelector('.timestamp');
  if (!genEl) return;
  const match = genEl.textContent.match(/Last generated: ([\\d-]+T[\\d:]+)/);
  if (match) {{
    const genTime = new Date(match[1] + 'Z');
    const ageMin = Math.round((Date.now() - genTime.getTime()) / 60000);
    const ageEl = document.getElementById('dataAge');
    if (ageMin < 5) {{
      ageEl.textContent = 'Data: ' + ageMin + 'm ago';
      ageEl.style.color = '#00c97a';
    }} else if (ageMin < 30) {{
      ageEl.textContent = 'Data: ' + ageMin + 'm ago';
      ageEl.style.color = '#f0a500';
    }} else {{
      ageEl.textContent = 'Data: ' + ageMin + 'm ago (stale)';
      ageEl.style.color = '#ff4d4d';
    }}
  }}
}}
updateAge();
setInterval(updateAge, 30000);
</script>

</body>
</html>"""


# ── Watchlist MR signals ────────────────────────────────────────────────────────
WATCHLIST_PATH = Path("/home/nixos/Prod/V1/src/watchlist.json")
WL_LOOKBACK    = 10   # default, overridden per ticker via watchlist.json

# Trend-aware MR weighting: when the price series is strongly trending,
# mean reversion is the wrong bet. We reduce mr_weight by up to TREND_MR_REDUCTION
# of itself when trend_strength == 1.0 (full directional move).
TREND_MR_REDUCTION = 0.6


def _trend_strength(closes) -> float:
    """
    Return 0..1 — how directional the recent price action is.
    1.0 = strong, persistent move (MR likely wrong); 0.0 = chop (MR appropriate).

    Composition:
      persistence: |fraction-of-up-bars - 0.5| × 2  → 0 (balanced) to 1 (one-sided)
      magnitude:   |5-bar return| / 1.5%             clipped to 1
    Combined as persistence × magnitude.
    """
    if not closes or len(closes) < 7:
        return 0.0
    n = min(10, len(closes) - 1)
    up = sum(1 for i in range(-n, 0) if closes[i] > closes[i-1])
    persistence = abs((up / n) - 0.5) * 2.0  # 0..1
    ret5 = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] else 0.0
    magnitude = min(1.0, abs(ret5) / 0.015)
    return round(persistence * magnitude, 3)


def _compute_mr_for_ticker(ticker: str, bars: list, lookback: int,
                            note: str = "") -> dict:
    """
    Core MR computation for any ticker given its bars.
    Returns full signal dict including bps target return.
    """
    if len(bars) < 5:
        return {"error": f"only {len(bars)} bars", "label": "INACTIVE",
                "active": False, "z_score": 0.0, "signal": 0.0,
                "mr_weight": 0.0, "sent_weight": 1.0, "levels": {},
                "lookback": lookback, "ticker_note": note}

    closes  = [b["close"] for b in bars[-lookback:]]
    n       = len(closes)
    mean    = sum(closes) / n
    std     = (sum((c - mean)**2 for c in closes) / n) ** 0.5

    if std < 0.001:
        return {"label": "INACTIVE", "active": False, "z_score": 0.0,
                "signal": 0.0, "mr_weight": 0.0, "sent_weight": 1.0,
                "mean": round(mean, 3), "std": round(std, 4),
                "current_price": closes[-1], "levels": {},
                "lookback": lookback, "ticker_note": note}

    price     = closes[-1]
    z_score   = (price - mean) / std
    abs_z     = abs(z_score)
    direction = -1.0 if z_score > 0 else 1.0

    if abs_z <= MR_1SIGMA:
        signal_strength = 0.0
    elif abs_z <= MR_2SIGMA:
        signal_strength = (abs_z - MR_1SIGMA) / (MR_2SIGMA - MR_1SIGMA)
    else:
        signal_strength = 1.0

    mr_signal   = round(direction * signal_strength, 4)
    mr_weight_raw = round(min(0.70, 0.70 * max(0, abs_z - MR_1SIGMA)), 4) if abs_z > MR_1SIGMA else 0.0

    # Trend-aware MR weight: reduce when the move is strongly directional.
    # On clearly-trending days, mean reversion is the wrong side of the trade.
    trend       = _trend_strength(closes)
    mr_weight   = round(mr_weight_raw * (1.0 - TREND_MR_REDUCTION * trend), 4)
    sent_weight = round(1.0 - mr_weight, 4)

    dp        = 3 if price < 10 else 2
    level_1up = round(mean + 1*std, dp); level_1dn = round(mean - 1*std, dp)
    level_2up = round(mean + 2*std, dp); level_2dn = round(mean - 2*std, dp)
    level_3up = round(mean + 3*std, dp); level_3dn = round(mean - 3*std, dp)

    if z_score < 0:   # BUY reversion — price below mean
        entry_zone  = (level_2dn, level_1dn)
        full_signal = level_2dn
        trigger     = level_1dn
        exit_price  = round(mean, dp)
        extreme     = level_3dn
    else:             # SELL reversion — price above mean
        entry_zone  = (level_1up, level_2up)
        full_signal = level_2up
        trigger     = level_1up
        exit_price  = round(mean, dp)
        extreme     = level_3up

    # ── Basis point target return ────────────────────────────────────────────
    # Entry = midpoint of entry zone, exit = mean (reversion target)
    # For LONG: bps = (mean - entry_mid) / entry_mid * 10000
    # For SHORT: bps = (entry_mid - mean) / entry_mid * 10000
    if abs_z >= MR_1SIGMA:
        entry_mid = (entry_zone[0] + entry_zone[1]) / 2
        if entry_mid > 0:
            raw_bps = abs(exit_price - entry_mid) / entry_mid * 10000
            bps_target = round(raw_bps, 1)
        else:
            bps_target = None
    else:
        bps_target = None

    active = abs_z >= MR_1SIGMA
    if   not active:          label = "INACTIVE"
    elif abs_z >= MR_3SIGMA:  label = "EXTREME"
    elif abs_z >= MR_2SIGMA:  label = "BUY" if z_score < 0 else "SELL"
    else:                     label = "WATCH"

    return {
        "active": active, "z_score": round(z_score, 3), "abs_z": round(abs_z, 3),
        "label": label, "signal": mr_signal,
        "mr_weight": mr_weight, "sent_weight": sent_weight,
        "mr_weight_raw": mr_weight_raw, "trend_strength": trend,
        "mean": round(mean, dp), "std": round(std, dp+1),
        "current_price": round(price, dp),
        "levels": {"1sigma_up": level_1up, "1sigma_dn": level_1dn,
                   "2sigma_up": level_2up, "2sigma_dn": level_2dn,
                   "3sigma_up": level_3up, "3sigma_dn": level_3dn},
        "entry_zone": entry_zone, "full_signal": full_signal,
        "trigger": trigger, "exit_price": exit_price, "extreme": extreme,
        "bps_target": bps_target,
        "n_bars": n, "lookback": lookback, "ticker_note": note,
    }


def compute_watchlist_mr(bbg_watchlist: dict) -> dict:
    """
    Compute MR signals for each ticker in the watchlist.
    Reads watchlist.json for ordering, notes, and per-ticker lookback overrides.
    bbg_watchlist: {ticker: {bars: [...], ...}} from bloomberg_prices.json
    """
    notes     = {}
    overrides = {}
    default_lb = WL_LOOKBACK
    order      = []

    if WATCHLIST_PATH.exists():
        try:
            with open(WATCHLIST_PATH) as f:
                wl = json.load(f)
            notes      = wl.get("notes", {})
            overrides  = wl.get("lookback_override", {})
            default_lb = wl.get("lookback_bars", WL_LOOKBACK)
            order      = wl.get("tickers", [])
        except Exception:
            pass

    # Process in watchlist order, fall back to bbg dict order
    ticker_order = order if order else list(bbg_watchlist.keys())

    results = {}
    for ticker in ticker_order:
        data = bbg_watchlist.get(ticker)
        if data is None:
            continue
        if "error" in data:
            results[ticker] = {"error": data["error"], "label": "ERROR",
                               "active": False, "z_score": 0.0, "signal": 0.0,
                               "mr_weight": 0.0, "sent_weight": 1.0, "levels": {},
                               "ticker_note": notes.get(ticker, "")}
            continue

        lookback = overrides.get(ticker, default_lb)
        bars = data.get("bars", [])
        results[ticker] = _compute_mr_for_ticker(
            ticker, bars, lookback, note=notes.get(ticker, ""))

    return results


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running market_sentiment_bbg v2.0 (MR edition)...")

    try:
        headlines = fetch_headlines(hours_back=48)
        print(f"  Fetched {len(headlines)} headlines (last 48h)")
    except Exception as e:
        print(f"[ERROR] Finnhub fetch failed: {e}")
        headlines = []

    if len(headlines) < MIN_HEADLINES:
        signal = {
            "result": {"label":"NEUTRAL","sent_label":"NEUTRAL","composite":0.0,
                       "sent_composite":0.0,"mr_signal":0.0,"mr_weight":0.0,
                       "sent_weight":1.0,"confidence":0.0,"components":{}},
            "mr": {"active":False,"label":"INACTIVE","z_score":0.0,"signal":0.0,
                   "mr_weight":0.0,"sent_weight":1.0,"levels":{}},
            "sentiment":{},"spy":{},"vix":{},"tlt":{},
            "top_bull":[],"top_bear":[],"top_tickers":[],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_headlines": len(headlines), "status":"insufficient_data",
            "price_source": "bloomberg_intraday"
        }
    else:
        scored  = score_headlines(headlines)
        sent    = compute_sentiment_signals(scored)
        tickers = extract_ticker_mentions(scored)

        bbg  = load_bbg_prices()
        spy  = get_spy_signals(bbg)
        vix  = get_vix_signal(bbg)
        tlt  = get_tlt_signal(bbg)
        mr   = compute_mean_reversion(bbg)

        # Watchlist single-name MR
        bbg_watchlist = bbg.get("watchlist", {})
        # Inject SPY into watchlist dict so it appears in the consolidated grid
        if "SPY" not in bbg_watchlist and "SPY" in bbg:
            bbg_watchlist = {"SPY": bbg["SPY"], **bbg_watchlist}
        watchlist_mr  = compute_watchlist_mr(bbg_watchlist) if bbg_watchlist else {}
        if watchlist_mr:
            for tk, m in watchlist_mr.items():
                print(f"  WL {tk}: px={m.get('current_price')}  z={m.get('z_score'):+.2f}σ  "
                      f"label={m.get('label')}  bars={m.get('n_bars')}")

        result = build_composite(sent, spy, vix, tlt, mr, len(headlines))

        print(f"  Sentiment now={sent['sent_now']:+.3f}  slope={sent['slope']:+.3f}")
        print(f"  SPY px={spy.get('price')}  RSI={spy.get('rsi14')}  udv={spy.get('udv_ratio')}")
        print(f"  VIX={vix.get('vix')}  TLT={tlt.get('tlt')}")
        print(f"  MR: z={mr.get('z_score'):+.2f}σ  label={mr.get('label')}  "
              f"MR_wt={mr.get('mr_weight')*100:.0f}%  signal={mr.get('signal'):+.3f}")
        print(f"  → Sent={result['sent_label']} {result['sent_composite']:+.3f}  "
              f"MR={mr.get('label')}  Combined={result['label']} {result['composite']:+.3f}  "
              f"conf={result['confidence']}%")

        signal = {
            "result":       result,
            "mr":           mr,
            "watchlist":    watchlist_mr,
            "sentiment":    {k: sent[k] for k in
                             ["sent_now","sent_prev","slope","n_recent","n_prior"]},
            "spy":          spy,
            "vix":          vix,
            "tlt":          tlt,
            "top_bull":     sent["top_bull"],
            "top_bear":     sent["top_bear"],
            "top_tickers":  tickers,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_headlines":  len(headlines),
            "status":       "ok",
            "price_source": "bloomberg_intraday"
        }

    # Write JSON
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_OUT, "w") as f:
        json.dump(signal, f, indent=2)
    print(f"  JSON → {JSON_OUT}")

    # Write HTML
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(HTML_OUT, "w") as f:
        f.write(render_html(signal))
    print(f"  HTML → {HTML_OUT}")

    return signal


if __name__ == "__main__":
    main()
