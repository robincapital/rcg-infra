"""
market_sentiment.py  v2.0
RCG Market Directional Sentiment Signal — 3-5 Day Horizon
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Signal inputs:
  1. Finnhub general news (48h) scored with VADER
  2. Sentiment slope: 24h vs prior 24h tone change
  3. SPY technicals: SMA20/50, RSI14
  4. Volume: SPY vol vs 5d/10d/50d averages + up/down vol ratio
  5. VIX level + direction (fear signal)
  6. TLT direction (flight-to-safety / risk-off signal)
  7. Top 10 most-mentioned tickers (bullish + bearish)

Output:
  - /home/nixos/Prod/V1/src/factor_signals.json  (for screener)
  - /home/nixos/Prod/V1/src/outputs/market_sentiment.html  (dashboard)
"""

import os, json, re, math, requests
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
SFP_PATH      = Path("/var/sharadar/data/SFP.parquet")
JSON_OUT      = Path("/home/nixos/Prod/V1/src/factor_signals.json")
HTML_OUT      = Path("/home/nixos/Prod/V1/src/outputs/market_sentiment.html")

BUY_THRESHOLD  =  0.20
SELL_THRESHOLD = -0.20
MIN_HEADLINES  =  5

# Final composite weights
W_SENT_SLOPE  = 0.25   # sentiment tone change 24h vs 48h
W_SENT_ABS    = 0.15   # absolute current sentiment level
W_SPY_TECH    = 0.20   # SPY SMA + RSI
W_VOLUME      = 0.20   # volume vs 5d/10d/50d + up/down ratio
W_VIX         = 0.10   # VIX level + direction
W_TLT         = 0.10   # TLT direction (risk-off proxy)

# Known large-cap tickers to scan for in headlines
WATCH_TICKERS = {
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","BRK","JPM",
    "V","UNH","XOM","JNJ","WMT","MA","PG","HD","CVX","MRK","ABBV","PEP",
    "KO","AVGO","COST","LLY","TMO","MCD","ACN","BAC","CRM","NEE","NKE",
    "DHR","TXN","PM","LIN","ORCL","AMD","QCOM","HON","RTX","UNP","GS",
    "CAT","AMAT","INTU","AMGN","SBUX","NOW","ISRG","GE","ADP","BKNG",
    "SPY","QQQ","IWM","TLT","GLD","BTC","ETH","COIN","MSTR","PLTR",
    "RIVN","LCID","GME","AMC","BBBY","SOFI","HOOD","RBLX","SNAP","PINS",
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

# ── ETF technicals + volume from SFP ──────────────────────────────────────────
def load_etf(ticker: str, n: int = 60) -> list[dict]:
    """Load last N rows of close+volume for a ticker from SFP parquet."""
    try:
        import polars as pl
        df = (
            pl.scan_parquet(str(SFP_PATH))
            .filter(pl.col("ticker") == ticker)
            .select(["date", "close", "volume"])
            .sort("date")
            .tail(n)
            .collect()
        )
        return df.to_dicts()
    except Exception as e:
        print(f"[WARN] SFP load failed for {ticker}: {e}")
        return []

def compute_spy_signals(rows: list[dict]) -> dict:
    if len(rows) < 21:
        return {"sma_signal": 0.0, "rsi_signal": 0.0, "vol_signal": 0.0,
                "spy_composite": 0.0}

    closes  = [r["close"]  for r in rows]
    volumes = [r["volume"] for r in rows]
    price   = closes[-1]

    # ── SMA
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    sma_score = (0.5 if price > sma20 else 0.0) + (0.5 if price > sma50 else 0.0)
    sma_signal = (sma_score - 0.5) * 2   # → [-1, 1]

    # ── RSI 14
    gains, losses = [], []
    for i in range(-14, 0):
        d = closes[i] - closes[i-1]
        (gains if d > 0 else losses).append(abs(d))
    avg_g = sum(gains)/14 if gains else 0.001
    avg_l = sum(losses)/14 if losses else 0.001
    rsi   = 100 - (100 / (1 + avg_g/avg_l))
    if   rsi < 35: rsi_signal =  1.0
    elif rsi < 45: rsi_signal =  0.5
    elif rsi > 70: rsi_signal = -1.0
    elif rsi > 60: rsi_signal = -0.5
    else:          rsi_signal =  0.0

    # ── Volume vs 5d / 10d / 50d
    vol_now = volumes[-1]
    vol5    = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else vol_now
    vol10   = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else vol_now
    vol50   = sum(volumes[-50:]) / 50 if len(volumes) >= 50 else vol_now

    # ratio vs 50d baseline
    vol5_ratio  = vol5  / vol50 if vol50 > 0 else 1.0
    vol10_ratio = vol10 / vol50 if vol50 > 0 else 1.0

    # Up/down volume: last 5 sessions
    up_vol = down_vol = 0.0
    for i in range(-5, 0):
        if closes[i] >= closes[i-1]:
            up_vol   += volumes[i]
        else:
            down_vol += volumes[i]
    udv_ratio = (up_vol / (up_vol + down_vol)) if (up_vol + down_vol) > 0 else 0.5
    udv_signal = (udv_ratio - 0.5) * 2   # → [-1, 1]

    # Volume expansion on trend direction — confirms price signal
    vol_trend = sma_signal  # price direction
    vol_expansion = min(1.0, (vol5_ratio - 1.0) * 2)  # how much above 50d avg
    vol_signal = round(vol_trend * max(0.0, vol_expansion) + udv_signal * 0.5, 4)
    vol_signal = max(-1.0, min(1.0, vol_signal))

    spy_composite = round(sma_signal*0.40 + rsi_signal*0.35 + vol_signal*0.25, 4)

    return {
        "price":        round(price, 2),
        "sma20":        round(sma20, 2),
        "sma50":        round(sma50, 2),
        "rsi14":        round(rsi, 1),
        "vol_now":      int(vol_now),
        "vol5_avg":     int(vol5),
        "vol10_avg":    int(vol10),
        "vol50_avg":    int(vol50),
        "vol5_ratio":   round(vol5_ratio, 2),
        "vol10_ratio":  round(vol10_ratio, 2),
        "udv_ratio":    round(udv_ratio, 3),
        "sma_signal":   round(sma_signal, 4),
        "rsi_signal":   round(rsi_signal, 4),
        "vol_signal":   round(vol_signal, 4),
        "spy_composite": spy_composite,
    }

def compute_vix_signal(rows: list[dict]) -> dict:
    if len(rows) < 6:
        return {"vix": None, "vix_signal": 0.0}
    closes = [r["close"] for r in rows]
    vix    = closes[-1]
    vix5   = sum(closes[-5:]) / 5

    # VIX level: <15 calm (bullish), >25 fearful (bearish), >35 extreme fear
    if   vix < 15:  level_sig =  1.0
    elif vix < 20:  level_sig =  0.5
    elif vix < 25:  level_sig =  0.0
    elif vix < 35:  level_sig = -0.5
    else:           level_sig = -1.0

    # VIX direction: falling = bullish, rising = bearish
    dir_sig = -1.0 if vix > vix5 * 1.05 else (1.0 if vix < vix5 * 0.95 else 0.0)

    vix_signal = round(level_sig * 0.6 + dir_sig * 0.4, 4)
    return {
        "vix":        round(vix, 2),
        "vix5_avg":   round(vix5, 2),
        "vix_signal": vix_signal,
    }

def compute_tlt_signal(rows: list[dict]) -> dict:
    """TLT rising = risk-off = bearish for equities. TLT falling = risk-on = bullish."""
    if len(rows) < 11:
        return {"tlt": None, "tlt_signal": 0.0}
    closes = [r["close"] for r in rows]
    tlt    = closes[-1]
    sma10  = sum(closes[-10:]) / 10

    # TLT below SMA10 = risk-on = bullish equities
    tlt_signal = -1.0 if tlt > sma10 * 1.01 else (1.0 if tlt < sma10 * 0.99 else 0.0)
    return {
        "tlt":        round(tlt, 2),
        "tlt_sma10":  round(sma10, 2),
        "tlt_signal": round(tlt_signal, 4),
    }

# ── Composite ──────────────────────────────────────────────────────────────────
def build_composite(sent: dict, spy: dict, vix: dict, tlt: dict, n_headlines: int) -> dict:
    sent_abs_norm = max(-1.0, min(1.0, sent["sent_now"] / 0.3))

    composite = (
        W_SENT_SLOPE * sent["slope_norm"] +
        W_SENT_ABS   * sent_abs_norm +
        W_SPY_TECH   * spy.get("spy_composite", 0.0) +
        W_VOLUME     * spy.get("vol_signal", 0.0) +
        W_VIX        * vix.get("vix_signal", 0.0) +
        W_TLT        * tlt.get("tlt_signal", 0.0)
    )
    composite = round(composite, 4)

    count_conf = min(1.0, n_headlines / 20)
    signals    = [sent["slope_norm"], sent_abs_norm,
                  spy.get("spy_composite",0.0), vix.get("vix_signal",0.0),
                  tlt.get("tlt_signal",0.0)]
    same_sign  = sum(1 for s in signals if s != 0 and (s > 0) == (composite > 0))
    agreement  = same_sign / max(1, len([s for s in signals if s != 0]))
    confidence = round((count_conf * 0.4 + agreement * 0.6) * 100, 1)

    label = "BUY" if composite >= BUY_THRESHOLD else ("SELL" if composite <= SELL_THRESHOLD else "NEUTRAL")

    return {
        "label":      label,
        "composite":  composite,
        "confidence": confidence,
        "components": {
            "sentiment_slope": round(W_SENT_SLOPE * sent["slope_norm"], 4),
            "sentiment_abs":   round(W_SENT_ABS   * sent_abs_norm, 4),
            "spy_technical":   round(W_SPY_TECH   * spy.get("spy_composite",0.0), 4),
            "volume":          round(W_VOLUME     * spy.get("vol_signal",0.0), 4),
            "vix":             round(W_VIX        * vix.get("vix_signal",0.0), 4),
            "tlt":             round(W_TLT        * tlt.get("tlt_signal",0.0), 4),
        }
    }

# ── HTML dashboard ─────────────────────────────────────────────────────────────
def render_html(signal: dict) -> str:
    label      = signal["label"]
    composite  = signal["composite"]
    confidence = signal["confidence"]
    comp       = signal.get("components", {})
    sent       = signal.get("sentiment", {})
    spy        = signal.get("spy", {})
    vix        = signal.get("vix", {})
    tlt        = signal.get("tlt", {})
    top_bull   = signal.get("top_bull", [])
    top_bear   = signal.get("top_bear", [])
    tickers    = signal.get("top_tickers", [])
    gen_at     = signal.get("generated_at", "")
    n_h        = signal.get("n_headlines", 0)

    label_color = {"BUY": "#00c97a", "SELL": "#ff4d4d", "NEUTRAL": "#f0a500"}[label]
    bar_pct     = int((composite + 1) / 2 * 100)  # map [-1,1] to [0,100]

    def comp_bar(val, weight, label_text, footnote):
        pct   = int((val + 1) / 2 * 100)
        color = "#00c97a" if val > 0 else ("#ff4d4d" if val < 0 else "#666")
        contrib = comp.get(label_text.lower().replace(" ","_").replace("/",""), 0.0)
        return f"""
        <div class="comp-row">
          <div class="comp-label">{label_text} <span class="weight">({int(weight*100)}% weight)</span></div>
          <div class="comp-bar-wrap">
            <div class="comp-bar" style="width:{pct}%;background:{color}"></div>
          </div>
          <div class="comp-val" style="color:{color}">{val:+.3f}</div>
          <div class="comp-contrib">contribution: {contrib:+.4f}</div>
          <div class="footnote">ⓘ {footnote}</div>
        </div>"""

    def ticker_rows(tickers):
        rows = ""
        for t in tickers:
            color = "#00c97a" if t["bias"]=="BULL" else ("#ff4d4d" if t["bias"]=="BEAR" else "#aaa")
            rows += f"""<tr>
              <td style="color:#fff;font-weight:600">{t['ticker']}</td>
              <td>{t['mentions']}</td>
              <td style="color:{color}">{t['bias']}</td>
              <td style="color:{color}">{t['avg_sent']:+.3f}</td>
            </tr>"""
        return rows

    def headline_rows(items, color):
        rows = ""
        for h in items:
            url = h.get("url","#")
            rows += f"""<tr>
              <td><a href="{url}" target="_blank" style="color:{color};text-decoration:none">
                {h['headline'][:90]}{'…' if len(h['headline'])>90 else ''}
              </a></td>
              <td style="color:#aaa;font-size:11px">{h['source']}</td>
              <td style="color:{color};font-weight:600">{h['score']:+.3f}</td>
            </tr>"""
        return rows

    vix_val  = vix.get("vix","—")
    tlt_val  = tlt.get("tlt","—")
    rsi_val  = spy.get("rsi14","—")
    sma20    = spy.get("sma20","—")
    sma50    = spy.get("sma50","—")
    spy_px   = spy.get("price","—")
    v5r      = spy.get("vol5_ratio","—")
    v10r     = spy.get("vol10_ratio","—")
    udv      = spy.get("udv_ratio","—")

    sent_now  = sent.get("sent_now",0)
    sent_prev = sent.get("sent_prev",0)
    slope     = sent.get("slope",0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="1800">
<title>RCG Market Sentiment</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ background:#0d0d0d; color:#ddd; font-family:'Segoe UI',sans-serif; font-size:13px; padding:20px }}
  h1 {{ color:#c9a84c; font-size:20px; letter-spacing:2px; margin-bottom:4px }}
  .subtitle {{ color:#666; font-size:11px; margin-bottom:24px }}
  .bias-stamp {{
    display:inline-block; padding:10px 28px; border-radius:4px;
    font-size:32px; font-weight:800; letter-spacing:4px;
    color:{label_color}; border:2px solid {label_color};
    margin-bottom:8px
  }}
  .composite {{ color:#aaa; font-size:13px; margin-bottom:4px }}
  .confidence {{ color:#888; font-size:12px; margin-bottom:20px }}
  .progress-wrap {{ background:#222; border-radius:3px; height:8px; width:320px; margin-bottom:24px }}
  .progress-bar {{ height:8px; border-radius:3px; background:{label_color}; width:{bar_pct}% }}
  .section {{ margin-bottom:28px }}
  .section-title {{ color:#c9a84c; font-size:12px; letter-spacing:2px; text-transform:uppercase;
                    border-bottom:1px solid #2a2a2a; padding-bottom:6px; margin-bottom:14px }}
  .stats-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px }}
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
</style>
</head>
<body>

<h1>RCG MARKET SENTIMENT</h1>
<div class="subtitle">3–5 Day Directional Signal · {n_h} headlines analyzed · Cached, updated each run</div>

<div class="bias-stamp">{label}</div><br>
<div class="composite">Composite score: <strong style="color:#fff">{composite:+.4f}</strong>
  &nbsp;|&nbsp; Threshold: BUY ≥ +0.20 / SELL ≤ −0.20</div>
<div class="confidence">Signal confidence: <strong style="color:#fff">{confidence}%</strong></div>
<div class="progress-wrap"><div class="progress-bar"></div></div>

<!-- Market snapshot -->
<div class="section">
  <div class="section-title">Market Snapshot</div>
  <div class="stats-grid">
    <div class="stat-box">
      <div class="stat-label">SPY Price</div>
      <div class="stat-val">${spy_px}</div>
      <div class="stat-sub">SMA20: {sma20} · SMA50: {sma50}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">SPY RSI (14)</div>
      <div class="stat-val">{rsi_val}</div>
      <div class="stat-sub">&lt;35 oversold · &gt;70 overbought</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">VIX</div>
      <div class="stat-val">{vix_val}</div>
      <div class="stat-sub">5d avg: {vix.get('vix5_avg','—')}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">TLT Price</div>
      <div class="stat-val">${tlt_val}</div>
      <div class="stat-sub">SMA10: {tlt.get('tlt_sma10','—')}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Vol / 5d Avg</div>
      <div class="stat-val">{v5r}x</div>
      <div class="stat-sub">vs 50d baseline</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Vol / 10d Avg</div>
      <div class="stat-val">{v10r}x</div>
      <div class="stat-sub">vs 50d baseline</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Up/Down Vol Ratio</div>
      <div class="stat-val">{udv}</div>
      <div class="stat-sub">Last 5 sessions · 0.5 = neutral</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Sentiment Slope</div>
      <div class="stat-val" style="color:{'#00c97a' if slope>0 else '#ff4d4d'}">{slope:+.3f}</div>
      <div class="stat-sub">Now: {sent_now:+.3f} · Prev: {sent_prev:+.3f}</div>
    </div>
  </div>
</div>

<!-- Signal components -->
<div class="section">
  <div class="section-title">Signal Components</div>
  {comp_bar(sent.get('slope_norm',0), W_SENT_SLOPE, "Sentiment Slope",
    "VADER compound score: 24h average vs prior 24h. Positive = improving tone. "
    "Normalized to [-1,1] dividing by 0.5 (typical max slope). Weight: 25%.")}
  {comp_bar(max(-1.0,min(1.0,sent_now/0.3)), W_SENT_ABS, "Sentiment Absolute",
    "Current 24h average VADER score, normalized by 0.3 (typical max compound). "
    "Captures how positive/negative the news tone is right now. Weight: 15%.")}
  {comp_bar(spy.get('spy_composite',0), W_SPY_TECH, "SPY Technical",
    "SMA20/50 positioning (40%) + RSI14 (35%) + volume signal (25%). "
    "Price above both SMAs = bullish. RSI <35 = oversold bounce signal. Weight: 20%.")}
  {comp_bar(spy.get('vol_signal',0), W_VOLUME, "Volume",
    "5d and 10d avg volume vs 50d baseline ratio. High volume confirming price direction = strong signal. "
    "Up/down volume ratio over last 5 sessions added. Expansion on up days = bullish. Weight: 20%.")}
  {comp_bar(vix.get('vix_signal',0), W_VIX, "VIX",
    "VIX level: <15 calm (+1.0), >35 extreme fear (-1.0). Direction: VIX falling vs 5d avg = bullish. "
    "Level (60%) + direction (40%) combined. Weight: 10%.")}
  {comp_bar(tlt.get('tlt_signal',0), W_TLT, "TLT (Risk-Off Proxy)",
    "TLT above SMA10 = bonds rallying = flight to safety = bearish for equities. "
    "TLT below SMA10 = risk-on = bullish for equities. Weight: 10%.")}
</div>

<!-- Top tickers -->
<div class="section">
  <div class="section-title">Top 10 Mentioned Tickers</div>
  <table>
    <thead><tr><th>Ticker</th><th>Mentions</th><th>Bias</th><th>Avg Sentiment</th></tr></thead>
    <tbody>{ticker_rows(tickers)}</tbody>
  </table>
  <div class="footnote" style="margin-top:8px">
    ⓘ Tickers extracted from headline+summary text. Avg sentiment = mean VADER compound score
    of all articles mentioning that ticker. BULL &gt; +0.05, BEAR &lt; −0.05.
  </div>
</div>

<!-- Top bullish headlines -->
<div class="section">
  <div class="section-title">Top 5 Bullish Headlines</div>
  <table>
    <thead><tr><th>Headline</th><th>Source</th><th>Score</th></tr></thead>
    <tbody>{headline_rows(top_bull, '#00c97a')}</tbody>
  </table>
  <div class="footnote" style="margin-top:8px">
    ⓘ VADER compound score range: +1.0 (most positive) to −1.0 (most negative).
    Scores above +0.05 considered positive sentiment.
  </div>
</div>

<!-- Top bearish headlines -->
<div class="section">
  <div class="section-title">Top 5 Bearish Headlines</div>
  <table>
    <thead><tr><th>Headline</th><th>Source</th><th>Score</th></tr></thead>
    <tbody>{headline_rows(top_bear, '#ff4d4d')}</tbody>
  </table>
  <div class="footnote" style="margin-top:8px">
    ⓘ Scores below −0.05 considered negative sentiment. Extreme bearish &lt; −0.5.
    These are the most negatively-scored articles in the last 48h.
  </div>
</div>

<div class="timestamp">Last generated: {gen_at} UTC &nbsp;·&nbsp;
  Cached output — re-runs each morning before screener &nbsp;·&nbsp;
  Source: Finnhub general news + Sharadar SFP (SPY/VIX/TLT)
</div>

</body>
</html>"""

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running market_sentiment v2.0...")

    try:
        headlines = fetch_headlines(hours_back=48)
        print(f"  Fetched {len(headlines)} headlines (last 48h)")
    except Exception as e:
        print(f"[ERROR] Finnhub fetch failed: {e}")
        headlines = []

    if len(headlines) < MIN_HEADLINES:
        print(f"[WARN] Only {len(headlines)} headlines — defaulting to NEUTRAL")
        signal = {
            "label":"NEUTRAL","composite":0.0,"confidence":0.0,
            "components":{},"sentiment":{},"spy":{},"vix":{},"tlt":{},
            "top_bull":[],"top_bear":[],"top_tickers":[],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_headlines": len(headlines), "status":"insufficient_data"
        }
    else:
        scored   = score_headlines(headlines)
        sent     = compute_sentiment_signals(scored)
        tickers  = extract_ticker_mentions(scored)

        spy_rows = load_etf("SPY", 60)
        vix_rows = load_etf("VIX", 10)
        tlt_rows = load_etf("TLT", 15)

        spy  = compute_spy_signals(spy_rows)
        vix  = compute_vix_signal(vix_rows)
        tlt  = compute_tlt_signal(tlt_rows)
        result = build_composite(sent, spy, vix, tlt, len(headlines))

        signal = {
            "label":       result["label"],
            "composite":   result["composite"],
            "confidence":  result["confidence"],
            "components":  result["components"],
            "sentiment":   {k: sent[k] for k in
                            ["sent_now","sent_prev","slope","n_recent","n_prior"]},
            "spy":         spy,
            "vix":         vix,
            "tlt":         tlt,
            "top_bull":    sent["top_bull"],
            "top_bear":    sent["top_bear"],
            "top_tickers": tickers,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_headlines": len(headlines),
            "status":      "ok"
        }

        print(f"  Sentiment now={sent['sent_now']:+.3f}  slope={sent['slope']:+.3f}")
        print(f"  SPY px={spy.get('price')}  RSI={spy.get('rsi14')}  "
              f"vol5x={spy.get('vol5_ratio')}  udv={spy.get('udv_ratio')}")
        print(f"  VIX={vix.get('vix')}  TLT={tlt.get('tlt')}")
        print(f"  → {result['label']}  composite={result['composite']:+.3f}  "
              f"confidence={result['confidence']}%")
        print(f"  Top tickers: {[t['ticker'] for t in tickers[:5]]}")

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
