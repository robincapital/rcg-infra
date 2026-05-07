"""
finnhub_signals.py — per-ticker Finnhub feeds (news + recs + insider)
=====================================================================
Pulls three Finnhub endpoints per watchlist ticker, caches results so reruns
within each endpoint's TTL don't re-hit the API, writes a compact JSON output
for the dashboard, and records numeric signals into rcg_signals.signals for
later forward-return analysis.

Cadences (per-endpoint TTL):
  news:      30 min  — catalysts can shift fast intraday
  recs:       6h     — analyst recs change slowly (weekly at best)
  insider:   12h     — insider txns settle daily

Free-tier Finnhub limit is 60 calls/min. With ~40 tickers × 3 endpoints
= 120 calls per fire if cache is empty; this is staggered with 200ms
inter-call delays (~5 calls/sec), well under the limit.

Run cadence:
  Same as predictions_capture: every 30 min M-F market hours
  (HH:05, HH:35 ET via systemd timer).

Outputs:
  /home/nixos/Prod/V1/src/finnhub_signals.json  (compact, served via 8080)
  /home/nixos/Prod/V1/src/.finnhub_cache.json   (raw API responses + ttls)
  rcg_signals.signals  rows under run_type='finnhub_signals'
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

sys.path.insert(0, "/home/nixos/Prod/V1/src")
import signals_db as sdb  # noqa: E402

# ─── Config ────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("FINNHUB_API_KEY", "d6ivnd1r01qleu95pan0d6ivnd1r01qleu95pang")
BASE = "https://finnhub.io/api/v1"

# Watchlist source: the screener writes the live top-40 + macros to outputs/.
# (src/watchlist.json is the legacy 6-ticker file; market_sentiment_bbg.py
# similarly reads the full BBG watchlist out of bloomberg_prices.json instead.)
WATCHLIST_OUTPUTS = Path("/home/nixos/Prod/V1/outputs/watchlist.json")
WATCHLIST_LEGACY  = Path("/home/nixos/Prod/V1/src/watchlist.json")
OUTPUT_PATH       = Path("/home/nixos/Prod/V1/src/finnhub_signals.json")
CACHE_PATH        = Path("/home/nixos/Prod/V1/src/.finnhub_cache.json")

# Per-endpoint TTL (seconds)
TTL_NEWS    = 30 * 60          # 30 min
TTL_RECS    =  6 * 3600        # 6h
TTL_INSIDER = 12 * 3600        # 12h

INTER_CALL_DELAY_S = 1.10      # ≈55 calls/min — strictly under free-tier 60/min
HTTP_TIMEOUT_S     = 8
RETRY_ON_429_S     = 8         # backoff seconds before one retry on rate-limit hit

# ─── News polarity lexicon (lightweight; can swap for VADER later) ─────────
POSITIVE = {
    "beat", "beats", "raise", "raised", "raises", "upgrade", "upgraded", "upgrades",
    "buy", "buys", "strong", "rally", "rallies", "soar", "soars", "jump",
    "jumps", "gain", "gains", "rise", "rises", "surge", "surges", "tops",
    "topped", "positive", "optimistic", "bullish", "record", "breakout",
    "profit", "profits", "growth", "expand", "expands", "approved", "approves",
    "wins", "win", "won", "outperform", "outperforms", "exceed", "exceeds",
}
NEGATIVE = {
    "miss", "misses", "missed", "cut", "cuts", "downgrade", "downgraded",
    "downgrades", "sell", "sells", "weak", "weakness", "fall", "falls", "fell",
    "plunge", "plunges", "drop", "drops", "tank", "tanks", "crash", "crashes",
    "slip", "slips", "lose", "loses", "lost", "negative", "pessimistic",
    "bearish", "breakdown", "loss", "losses", "decline", "declines", "risk",
    "fraud", "lawsuit", "lawsuits", "investigation", "recall", "recalls",
    "concern", "concerns", "warning", "warns", "underperform", "halt",
    "delisted", "bankrupt", "bankruptcy",
}


# ─── HTTP helper ───────────────────────────────────────────────────────────
def http_get(path: str, params: dict) -> any:
    """Stdlib urllib GET; one auto-retry on HTTP 429 after a backoff. Returns parsed JSON or None."""
    params = {**params, "token": API_KEY}
    url = f"{BASE}{path}?{urlencode(params)}"
    sym = params.get("symbol", "-")
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                print(f"  [finnhub] 429 on {path} {sym} — sleeping {RETRY_ON_429_S}s before retry")
                time.sleep(RETRY_ON_429_S)
                continue
            print(f"  [finnhub] GET {path} {sym} -> HTTP {e.code}")
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            print(f"  [finnhub] GET {path} {sym} -> {type(e).__name__}: {e}")
            return None
    return None


# ─── Cache management ──────────────────────────────────────────────────────
def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, default=str))
    tmp.replace(CACHE_PATH)


def needs_refresh(cache_entry, ttl_s: int) -> bool:
    if not cache_entry:
        return True
    # Always re-fetch entries where the previous pull failed (data=None).
    # Otherwise a one-off 429 would poison the cache for the full TTL.
    if cache_entry.get("data") is None:
        return True
    last = cache_entry.get("last_pulled")
    if not last:
        return True
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return True
    return (datetime.now(timezone.utc) - ts).total_seconds() > ttl_s


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Endpoint fetchers ─────────────────────────────────────────────────────
def fetch_news(ticker: str, lookback_days: int = 2):
    today = datetime.now(timezone.utc).date()
    return http_get("/company-news", {
        "symbol": ticker,
        "from":   (today - timedelta(days=lookback_days)).isoformat(),
        "to":     today.isoformat(),
    })


def fetch_recs(ticker: str):
    return http_get("/stock/recommendation", {"symbol": ticker})


def fetch_insider(ticker: str, lookback_days: int = 30):
    today = datetime.now(timezone.utc).date()
    return http_get("/stock/insider-transactions", {
        "symbol": ticker,
        "from":   (today - timedelta(days=lookback_days)).isoformat(),
        "to":     today.isoformat(),
    })


# ─── Signal extraction ─────────────────────────────────────────────────────
def compute_news_polarity(headlines) -> float:
    """Bag-of-words polarity in [-1, +1]. 0 = neutral / no matched terms."""
    if not headlines:
        return 0.0
    pos = neg = 0
    for h in headlines:
        text = (str(h.get("headline", "")) + " " + str(h.get("summary", ""))).lower()
        # Cheap word membership check; fine for short headlines
        for w in text.replace(",", " ").replace(".", " ").split():
            if w in POSITIVE:
                pos += 1
            elif w in NEGATIVE:
                neg += 1
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


def process_news(news_list) -> dict:
    if not news_list or not isinstance(news_list, list):
        return {"count_24h": 0, "count_total": 0, "polarity": 0.0, "headlines": []}
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    recent = [h for h in news_list if isinstance(h, dict) and h.get("datetime", 0) >= one_day_ago]
    polarity = compute_news_polarity(recent)
    return {
        "count_24h":   len(recent),
        "count_total": len(news_list),
        "polarity":    polarity,
        "headlines":   [
            {"h": h.get("headline"), "src": h.get("source"),
             "ts": h.get("datetime"), "url": h.get("url")}
            for h in sorted(recent, key=lambda x: x.get("datetime", 0), reverse=True)[:5]
        ],
    }


def process_recs(rec_list) -> dict | None:
    """Latest period vs 3 months ago. Buy-score net = (2*sB + buy) - (sell + 2*sS)."""
    if not rec_list or not isinstance(rec_list, list):
        return None
    rec_list = sorted(rec_list, key=lambda r: r.get("period", ""), reverse=True)
    current = rec_list[0]
    older   = rec_list[3] if len(rec_list) > 3 else None  # ~3 months back
    delta = None
    if older:
        delta = {k: int((current.get(k) or 0) - (older.get(k) or 0))
                 for k in ("buy", "hold", "sell", "strongBuy", "strongSell")}
    buy_score_now = (
        (current.get("strongBuy") or 0) * 2 + (current.get("buy") or 0) -
        (current.get("sell") or 0) - (current.get("strongSell") or 0) * 2
    )
    return {
        "current":       current,
        "delta_3m":      delta,
        "buy_score":     buy_score_now,
        "period":        current.get("period"),
    }


def process_insider(txns) -> dict | None:
    """
    Insider activity, split by Form-4 transaction code so tax/exercise-driven
    transactions don't pollute the directional signal.

    Codes (SEC Form 4):
      P  open-market PURCHASE          → discretionary, asymmetric, predictive
      S  open-market SALE              → discretionary but often via 10b5-1; weaker
      F  share withholding for tax/exercise  → MECHANICAL, ignored for direction
      M  exercise of derivative              → MECHANICAL
      D  derivative disposition              → MECHANICAL
      A  grant or award                      → MECHANICAL
      G  bona fide gift                      → MECHANICAL

    The headline `signal` is driven by net PURCHASES only (the asymmetric signal
    that academic research finds predictive). Discretionary sales are surfaced
    as a separate channel; mechanical transactions are tracked but never
    flagged as directional.
    """
    if not txns or not isinstance(txns, dict):
        return None
    data = txns.get("data") or []
    if not data:
        return {
            "buys_30d_usd": 0, "sells_30d_usd": 0, "mechanical_30d_usd": 0,
            "n_buys": 0, "n_sells": 0, "n_mechanical": 0,
            "n_transactions_30d": 0, "signal": "neutral",
        }

    buys_usd = sells_usd = mech_usd = 0.0
    n_buys = n_sells = n_mech = 0

    for t in data:
        code   = (t.get("transactionCode") or "").upper()
        change = t.get("change") or 0
        price  = t.get("transactionPrice") or 0
        notional = change * price  # signed
        if code == "P":
            buys_usd += notional
            n_buys += 1
        elif code == "S":
            sells_usd += notional   # sells_usd will be negative since `change` < 0
            n_sells += 1
        else:
            mech_usd += notional
            n_mech += 1

    # Signal is purchase-driven: > $100k net buying = "buy"; > $250k discretionary
    # selling AND no buying activity = "sell" (still a noisy bar). Otherwise neutral.
    if buys_usd > 100_000:
        signal = "buy"
    elif buys_usd <= 0 and sells_usd < -250_000:
        signal = "sell"
    else:
        signal = "neutral"

    return {
        "buys_30d_usd":          round(buys_usd),
        "sells_30d_usd":         round(sells_usd),
        "mechanical_30d_usd":    round(mech_usd),
        "n_buys":                n_buys,
        "n_sells":                n_sells,
        "n_mechanical":          n_mech,
        "n_transactions_30d":    len(data),
        "signal":                signal,
    }


# ─── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    # Prefer the live screener-written watchlist; fall back to legacy if missing
    wl_path = WATCHLIST_OUTPUTS if WATCHLIST_OUTPUTS.exists() else WATCHLIST_LEGACY
    if not wl_path.exists():
        print("[finnhub] no watchlist.json found — exiting")
        return
    wl = json.loads(wl_path.read_text())
    print(f"[finnhub] watchlist source: {wl_path}")
    tickers = [t for t in (wl.get("tickers") or []) if t not in ("SPY", "VIX", "TLT")]
    if not tickers:
        print("[finnhub] empty watchlist — exiting")
        return

    cache = load_cache()
    started = now_iso()
    output = {"generated_at": started, "tickers": {}}
    n_pulls = {"news": 0, "recs": 0, "insider": 0}
    n_skips = {"news": 0, "recs": 0, "insider": 0}

    for ticker in tickers:
        cache_t = cache.get(ticker, {})
        out_t   = {}

        # ── news ──
        if needs_refresh(cache_t.get("news"), TTL_NEWS):
            data = fetch_news(ticker)
            time.sleep(INTER_CALL_DELAY_S)
            n_pulls["news"] += 1
            if data is not None:
                cache_t["news"] = {"last_pulled": started, "data": data}
        else:
            n_skips["news"] += 1
        out_t["news"] = process_news((cache_t.get("news") or {}).get("data"))

        # ── recommendations ──
        if needs_refresh(cache_t.get("recs"), TTL_RECS):
            data = fetch_recs(ticker)
            time.sleep(INTER_CALL_DELAY_S)
            n_pulls["recs"] += 1
            if data is not None:
                cache_t["recs"] = {"last_pulled": started, "data": data}
        else:
            n_skips["recs"] += 1
        out_t["recs"] = process_recs((cache_t.get("recs") or {}).get("data"))

        # ── insider transactions ──
        if needs_refresh(cache_t.get("insider"), TTL_INSIDER):
            data = fetch_insider(ticker)
            time.sleep(INTER_CALL_DELAY_S)
            n_pulls["insider"] += 1
            if data is not None:
                cache_t["insider"] = {"last_pulled": started, "data": data}
        else:
            n_skips["insider"] += 1
        out_t["insider"] = process_insider((cache_t.get("insider") or {}).get("data"))

        cache[ticker] = cache_t
        output["tickers"][ticker] = out_t

    save_cache(cache)
    OUTPUT_PATH.write_text(json.dumps(output, default=str, indent=2))
    print(f"[finnhub] {len(tickers)} tickers · pulls {n_pulls} · cache hits {n_skips}")
    print(f"[finnhub] wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")

    # ── DB capture ──
    run_id = sdb.record_run(
        run_type="finnhub_signals",
        config={"n_tickers": len(tickers), "pulls": n_pulls, "cache_hits": n_skips},
    )
    if not run_id:
        print("[finnhub] signals_db unavailable — skipping DB capture")
        return

    n_sig = 0
    for ticker, t in output["tickers"].items():
        news = t.get("news") or {}
        if news:
            sdb.record_signal(run_id, ticker, "news_count_24h",
                              value=float(news.get("count_24h", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "news_polarity",
                              value=float(news.get("polarity", 0)));  n_sig += 1
        recs = t.get("recs")
        if recs and recs.get("buy_score") is not None:
            sdb.record_signal(run_id, ticker, "rec_buy_score",
                              value=float(recs["buy_score"])); n_sig += 1
        insider = t.get("insider")
        if insider:
            # Split-channel insider signals (purchases vs discretionary sells vs mechanical)
            sdb.record_signal(run_id, ticker, "insider_buys_30d_usd",
                              value=float(insider.get("buys_30d_usd", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "insider_sells_30d_usd",
                              value=float(insider.get("sells_30d_usd", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "insider_mechanical_30d_usd",
                              value=float(insider.get("mechanical_30d_usd", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "insider_n_buys",
                              value=float(insider.get("n_buys", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "insider_n_sells",
                              value=float(insider.get("n_sells", 0))); n_sig += 1
            sdb.record_signal(run_id, ticker, "insider_signal",
                              string=insider.get("signal", "neutral")); n_sig += 1

    sdb.finalize_run(run_id, n_out=len(output["tickers"]))
    print(f"[finnhub] run_id={run_id} · {n_sig} signals captured")


if __name__ == "__main__":
    main()
