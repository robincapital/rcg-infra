"""
RCG Investment Analysis Report Generator — v3
=============================================
Robin Capital Group LLC

TWO-PATH DESIGN:
  1. Ticker in screener CSV  → pull pre-computed PT, scores, weights directly
  2. Ticker NOT in CSV       → compute independently using v3 PT engine
                               (works for ANY ticker: micro-caps, ADRs, biotech, etc.)

Price Target (v3):
  - Internal model = conviction-weighted blend of EV/EBITDA, EV/Revenue, FCF Yield
  - Analyst target = reference/sanity check only (not blended in)
  - Divergence flagged when |internal - analyst| / price > 40%
"""

# ============================================================
# USER INPUT
# ============================================================
TICKER = "WULF"   # <-- Change this

# ============================================================
# IMPORTS + PATHS
# ============================================================
import sys, os
sys.path.insert(0, '/home/nixos/Prod/V1/src')

import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from scipy import stats

SHARADAR_SF1     = Path("/var/sharadar/data/SF1.parquet")
SHARADAR_SEP     = Path("/var/sharadar/data/SEP.parquet")
SHARADAR_TICKERS = Path("/var/sharadar/data/TICKERS.parquet")
SCREENER_CSV     = Path("/home/nixos/Prod/V1/outputs/long_screener_results.csv")
OUTPUT_DIR       = Path("/home/nixos/Prod/V1/outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FINNHUB_API_KEY              = os.environ.get("FINNHUB_API_KEY", "d6ivnd1r01qleu95pan0d6ivnd1r01qleu95pang")
ANALYST_DIVERGENCE_THRESHOLD = 0.40
SECTOR_MULTIPLES = {
    "Technology":             {"ev_ebitda": 18.0, "ev_rev": 4.5,  "fcf_yield": 0.035},
    "Communication Services": {"ev_ebitda": 14.0, "ev_rev": 3.5,  "fcf_yield": 0.040},
    "Consumer Discretionary": {"ev_ebitda": 13.0, "ev_rev": 1.5,  "fcf_yield": 0.040},
    "Consumer Staples":       {"ev_ebitda": 12.0, "ev_rev": 1.2,  "fcf_yield": 0.045},
    "Healthcare":             {"ev_ebitda": 14.0, "ev_rev": 3.0,  "fcf_yield": 0.040},
    "Industrials":            {"ev_ebitda": 11.0, "ev_rev": 1.8,  "fcf_yield": 0.045},
    "Materials":              {"ev_ebitda":  9.0, "ev_rev": 1.4,  "fcf_yield": 0.050},
    "Real Estate":            {"ev_ebitda": 16.0, "ev_rev": 5.0,  "fcf_yield": 0.055},
    "Energy":                 {"ev_ebitda":  7.0, "ev_rev": 1.2,  "fcf_yield": 0.060},
    "Utilities":              {"ev_ebitda": 10.0, "ev_rev": 2.5,  "fcf_yield": 0.055},
    "Financials":             {"ev_ebitda": 12.0, "ev_rev": 2.5,  "fcf_yield": 0.050},
    "Financial Services":     {"ev_ebitda": 12.0, "ev_rev": 2.5,  "fcf_yield": 0.050},
    "_default":               {"ev_ebitda": 12.0, "ev_rev": 2.0,  "fcf_yield": 0.045},
}

try:
    import polars as pl
    HAS_POLARS = True
except ImportError:
    HAS_POLARS = False
    print("[WARN] polars not available")

# ============================================================
# REPORTLAB COLORS
# ============================================================
from reportlab.lib import colors as rl_colors

def hex_to_rl(h):
    h = h.lstrip('#')
    return rl_colors.Color(int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255)

RCG_NAVY       = hex_to_rl("#0a1628")
RCG_NAVY_LIGHT = hex_to_rl("#111d33")
RCG_NAVY_MID   = hex_to_rl("#0e1a2e")
RCG_GOLD       = hex_to_rl("#c8a84e")
RCG_GREEN      = hex_to_rl("#22c55e")
RCG_RED        = hex_to_rl("#ef4444")
RCG_TEXT       = hex_to_rl("#e2e8f0")
RCG_TEXT_DIM   = hex_to_rl("#64748b")
RCG_BORDER     = hex_to_rl("#1e3050")
RCG_AMBER      = hex_to_rl("#f59e0b")

# ============================================================
# SCREENER CSV LOADER  (fast path — v3 consistent)
# ============================================================
def load_screener_row(ticker):
    """Pull pre-computed screener row. Returns dict or None if not in last run.
    NOTE: Not being in the CSV is normal — ticker may not meet screener filters.
          The report computes independently for ANY ticker in either case."""
    if not SCREENER_CSV.exists() or not HAS_POLARS:
        return None
    try:
        df = pl.read_csv(str(SCREENER_CSV))
        df = df.rename({c: c.lower() for c in df.columns})
        row = df.filter(pl.col("ticker") == ticker)
        if row.height == 0:
            print(f"  [INFO] {ticker} not in screener CSV → computing PT independently (full Sharadar+Finnhub analysis)")
            return None
        r = row.row(0, named=True)
        print(f"  [FAST] Loaded pre-computed screener data for {ticker} from CSV")
        return r
    except Exception as e:
        print(f"  [WARN] Could not load screener CSV: {e} → falling back to independent compute")
        return None

# ============================================================
# SHARADAR LOADERS
# ============================================================
def load_ticker_info(ticker):
    info = {"name": ticker, "sector": "", "industry": "", "description": ""}
    if not SHARADAR_TICKERS.exists() or not HAS_POLARS:
        return info
    tickers = pl.read_parquet(SHARADAR_TICKERS)
    tickers = tickers.rename({c: c.lower() for c in tickers.columns})
    row = tickers.filter(pl.col("ticker") == ticker)
    if row.height > 0:
        r = row.row(0, named=True)
        info["name"]        = r.get("name", ticker) or ticker
        info["sector"]      = r.get("sector", "") or ""
        info["industry"]    = r.get("industry", "") or ""
        info["description"] = r.get("famaindustry", "") or r.get("sicindustry", "") or ""
    return info


def load_fundamentals(ticker):
    if not SHARADAR_SF1.exists() or not HAS_POLARS:
        return pl.DataFrame()
    sf1 = pl.read_parquet(SHARADAR_SF1)
    sf1 = sf1.rename({c: c.lower() for c in sf1.columns})
    cutoff = datetime.now() - timedelta(days=365 * 3)
    return sf1.filter(
        (pl.col("ticker") == ticker) &
        (pl.col("dimension") == "ARQ") &
        (pl.col("datekey") >= cutoff)
    ).sort("datekey")


def load_prices(ticker, days=365):
    """Load prices — closeunadj for display (actual market price, not split-adjusted)."""
    if not SHARADAR_SEP.exists() or not HAS_POLARS:
        return pl.DataFrame()
    sep = pl.read_parquet(SHARADAR_SEP)
    sep = sep.rename({c: c.lower() for c in sep.columns})
    px_col   = "closeunadj" if "closeunadj" in sep.columns else "close"
    date_col = next((c for c in ["date","datekey","calendardate"] if c in sep.columns), None)
    if not date_col:
        return pl.DataFrame()
    if date_col != "date":
        sep = sep.rename({date_col: "date"})
    cutoff = datetime.now() - timedelta(days=days)
    result = sep.filter(
        (pl.col("ticker") == ticker) & (pl.col("date") >= cutoff)
    ).sort("date")
    if px_col != "close":
        result = result.with_columns(pl.col(px_col).alias("close"))
    return result.select(["ticker","date","close"])

# ============================================================
# FINNHUB
# ============================================================
def fetch_realtime_price(ticker):
    import requests
    result = {"current": None, "previous_close": None,
              "open": None, "high": None, "low": None, "source": "none"}
    try:
        resp = requests.get("https://finnhub.io/api/v1/quote",
                            params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=5)
        if resp.status_code == 200:
            d = resp.json()
            if d and d.get("c") and d["c"] > 0:
                result.update({
                    "current":        round(d["c"], 2),
                    "previous_close": round(d.get("pc", 0), 2) or None,
                    "open":           round(d.get("o",  0), 2) or None,
                    "high":           round(d.get("h",  0), 2) or None,
                    "low":            round(d.get("l",  0), 2) or None,
                    "source":         "finnhub_realtime",
                })
    except Exception as e:
        print(f"  [WARN] Finnhub quote: {e}")
    return result


def fetch_analyst_data(ticker):
    import requests, time
    result = {
        "sentiment_score": 0.0, "strongBuy": 0, "buy": 0, "hold": 0,
        "sell": 0, "strongSell": 0, "total_analysts": 0,
        "target_mean": None, "target_high": None, "target_low": None,
    }
    try:
        resp = requests.get("https://finnhub.io/api/v1/stock/recommendation",
                            params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                l = data[0]
                sb,b,h,s,ss = (l.get(k,0) for k in
                               ["strongBuy","buy","hold","sell","strongSell"])
                total = sb+b+h+s+ss
                if total > 0:
                    w = (sb*1.0 + b*0.5 + h*0.0 + s*(-0.5) + ss*(-1.0)) / total
                    result.update({"sentiment_score": round(w,4),
                                   "strongBuy":sb,"buy":b,"hold":h,
                                   "sell":s,"strongSell":ss,"total_analysts":total})
    except Exception:
        pass
    time.sleep(0.3)
    try:
        resp = requests.get("https://finnhub.io/api/v1/stock/price-target",
                            params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("targetMean"):
                result.update({
                    "target_mean": data.get("targetMean"),
                    "target_high": data.get("targetHigh"),
                    "target_low":  data.get("targetLow"),
                })
    except Exception:
        pass
    return result

# ============================================================
# COMPANY NEWS  (Finnhub /company-news)
# ============================================================
def fetch_company_news(ticker, days_back=14, max_items=6):
    """Fetch recent news headlines from Finnhub. Returns list of dicts."""
    import requests
    from datetime import date
    results = []
    try:
        to_date   = date.today().strftime("%Y-%m-%d")
        from_date = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": from_date, "to": to_date,
                    "token": FINNHUB_API_KEY},
            timeout=6
        )
        if resp.status_code == 200:
            items = resp.json()
            seen  = set()
            for item in items:
                headline = (item.get("headline") or "").strip()
                if not headline or headline in seen:
                    continue
                seen.add(headline)
                ts = item.get("datetime", 0)
                try:
                    dt_str = datetime.utcfromtimestamp(int(ts)).strftime("%b %d")
                except Exception:
                    dt_str = "—"
                source = (item.get("source") or "").strip()
                results.append({"date": dt_str, "headline": headline, "source": source})
                if len(results) >= max_items:
                    break
    except Exception as e:
        print(f"  [WARN] Finnhub news: {e}")
    return results


# ============================================================
# SECTOR COMPS  (Sharadar SF1 live peer medians)
# ============================================================
def compute_sector_comps(ticker, sector, fund_df):
    """
    Compute live sector peer medians from Sharadar SF1.
    Returns dict with ticker values vs sector medians for key metrics.
    """
    comps = {}
    if not SHARADAR_SF1.exists() or not HAS_POLARS or not sector:
        return comps
    try:
        sf1 = pl.read_parquet(SHARADAR_SF1)
        sf1 = sf1.rename({c: c.lower() for c in sf1.columns})
        tickers_df = pl.read_parquet(SHARADAR_TICKERS)
        tickers_df = tickers_df.rename({c: c.lower() for c in tickers_df.columns})
        peers = tickers_df.filter(pl.col("sector") == sector)["ticker"].to_list()
        peers = [p for p in peers if p != ticker]

        cutoff = datetime.now() - timedelta(days=548)
        peer_fund = sf1.filter(
            (pl.col("ticker").is_in(peers)) &
            (pl.col("dimension") == "ARQ") &
            (pl.col("datekey") >= cutoff)
        )
        if peer_fund.height == 0:
            return comps
        peer_latest = peer_fund.sort("datekey").group_by("ticker").last()

        if hasattr(fund_df, "height") and fund_df.height > 0:
            tkr_row = fund_df.sort("datekey").row(-1, named=True)
        else:
            return comps

        def safe_tkr(col):
            v = tkr_row.get(col)
            if v is None: return None
            try:
                f = float(v)
                return None if np.isnan(f) else round(f, 4)
            except Exception:
                return None

        # Revenue growth QoQ
        rev_list = fund_df["revenue"].to_list() if "revenue" in fund_df.columns else []
        rev_clean = [float(v) for v in rev_list
                     if v is not None and not (isinstance(v,float) and np.isnan(v)) and float(v)>0]
        tkr_rev_growth = None
        if len(rev_clean) >= 2:
            tkr_rev_growth = round((rev_clean[-1]/rev_clean[-2]-1)*100, 2)

        peer_rev_growths = []
        for _, grp in peer_fund.sort("datekey").group_by("ticker"):
            try:
                revs = [float(v) for v in grp["revenue"].to_list()
                        if v is not None and not (isinstance(v,float) and np.isnan(v)) and float(v)>0]
                if len(revs) >= 2:
                    peer_rev_growths.append((revs[-1]/revs[-2]-1)*100)
            except Exception:
                pass
        peer_rev_growth_med = round(float(np.median(peer_rev_growths)), 2) if peer_rev_growths else None

        def ebitda_margin(rev, ebit):
            if rev and float(rev) > 0 and ebit is not None:
                return round(float(ebit)/float(rev)*100, 2)
            return None

        tkr_ebitda = safe_tkr("ebitda")
        tkr_rev    = safe_tkr("revenue")
        tkr_margin = ebitda_margin(tkr_rev, tkr_ebitda)
        peer_margins = [ebitda_margin(r.get("revenue"), r.get("ebitda"))
                        for r in peer_latest.iter_rows(named=True)]
        peer_margins = [m for m in peer_margins if m is not None]
        peer_margin_med = round(float(np.median(peer_margins)), 2) if peer_margins else None

        tkr_mktcap = safe_tkr("marketcap")
        tkr_debt   = safe_tkr("debt") or 0
        tkr_cash   = safe_tkr("cashnequsd") or 0
        tkr_ev_rev = None
        if tkr_mktcap and tkr_rev and tkr_rev > 0:
            tkr_ev_rev = round((tkr_mktcap + tkr_debt - tkr_cash) / (tkr_rev * 4), 2)

        peer_ev_revs = []
        for r in peer_latest.iter_rows(named=True):
            mc,rv,dbt,csh = r.get("marketcap"),r.get("revenue"),r.get("debt") or 0,r.get("cashnequsd") or 0
            if mc and rv and float(rv) > 0:
                peer_ev_revs.append((float(mc)+float(dbt)-float(csh))/(float(rv)*4))
        peer_ev_rev_med = round(float(np.median(peer_ev_revs)), 2) if peer_ev_revs else None

        tkr_net_debt_ebitda = None
        if tkr_ebitda and tkr_ebitda > 0:
            tkr_net_debt_ebitda = round((tkr_debt - tkr_cash) / (tkr_ebitda*4), 2)
        peer_nde = []
        for r in peer_latest.iter_rows(named=True):
            eb,dbt,csh = r.get("ebitda"),r.get("debt") or 0,r.get("cashnequsd") or 0
            if eb and float(eb) > 0:
                peer_nde.append((float(dbt)-float(csh))/(float(eb)*4))
        peer_nde_med = round(float(np.median(peer_nde)), 2) if peer_nde else None

        comps = {
            "n_peers":               peer_latest.height,
            "sector":                sector,
            "rev_growth_tkr":        tkr_rev_growth,
            "rev_growth_peer":       peer_rev_growth_med,
            "ebitda_margin_tkr":     tkr_margin,
            "ebitda_margin_peer":    peer_margin_med,
            "ev_rev_tkr":            tkr_ev_rev,
            "ev_rev_peer":           peer_ev_rev_med,
            "net_debt_ebitda_tkr":   tkr_net_debt_ebitda,
            "net_debt_ebitda_peer":  peer_nde_med,
        }
    except Exception as e:
        print(f"  [WARN] sector comps: {e}")
    return comps


# ============================================================
# V3 PRICE TARGET ENGINE
# (used when ticker not in screener CSV — works for ANY ticker)
# ============================================================
def _theil_sen(series):
    clean = [float(v) for v in series
             if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if len(clean) < 3:
        return 0.0, 0.0, 0.0
    x = np.arange(len(clean))
    y = np.array(clean)
    slopes = []
    for i in range(len(x)):
        for j in range(i+1, len(x)):
            if x[j] != x[i]:
                slopes.append((y[j]-y[i])/(x[j]-x[i]))
    if not slopes:
        return 0.0, float(np.mean(y)), 0.0
    slope     = float(np.median(slopes))
    intercept = float(np.median(y) - slope * np.median(x))
    y_pred    = slope * x + intercept
    ss_res    = np.sum((y - y_pred)**2)
    ss_tot    = np.sum((y - np.mean(y))**2)
    r2        = float(1 - ss_res/ss_tot) if ss_tot > 0 else 0.0
    return slope, intercept, max(0.0, r2)


def _rolling_median(series, window=3):
    result = []
    for i in range(len(series)):
        start = max(0, i - window + 1)
        window_vals = [v for v in series[start:i+1]
                       if v is not None and not (isinstance(v, float) and np.isnan(v))]
        result.append(float(np.median(window_vals)) if window_vals else 0.0)
    return result


def _model_conviction(r2, n, cv):
    if n < 3: return 0.0
    return float(np.clip(
        0.50 * max(0.0, float(r2)) +
        0.30 * max(0.0, 1.0 - cv/2.0) +
        0.20 * min(1.0, n/10.0), 0.0, 1.0))


def _revenue_growth_stats(rev_clean):
    """
    Compute revenue growth statistics.
    Returns (median_qoq, ann_growth, is_emerging_growth, growth_mult)
      is_emerging_growth = True when median QoQ > 25% sustained over 3+ quarters
      growth_mult        = EV/Rev multiple scalar for standard model
    """
    if len(rev_clean) < 4:
        return 0.0, 0.0, False, 1.0
    recent = rev_clean[-4:]
    qoq_growths = []
    for i in range(1, len(recent)):
        if recent[i-1] > 0:
            qoq_growths.append((recent[i] / recent[i-1]) - 1.0)
    if not qoq_growths:
        return 0.0, 0.0, False, 1.0
    median_qoq = float(np.median(qoq_growths))
    ann_growth  = (1 + median_qoq)**4 - 1

    # Emerging growth: >25% QoQ sustained (at least 3 of last 4 quarters above threshold)
    high_growth_quarters = sum(1 for g in qoq_growths if g >= 0.25)
    is_emerging = high_growth_quarters >= 2 and median_qoq >= 0.25

    # Standard EV/Rev multiple scalar
    if ann_growth >= 0.60:    mult = 2.20
    elif ann_growth >= 0.35:  mult = 1.80
    elif ann_growth >= 0.20:  mult = 1.40
    elif ann_growth >= 0.10:  mult = 1.15
    elif ann_growth >= 0.00:  mult = 1.00
    elif ann_growth >= -0.10: mult = 0.80
    else:                     mult = 0.60

    return median_qoq, ann_growth, is_emerging, mult


def _revenue_growth_mult(rev_clean):
    """Backward-compatible wrapper."""
    _, _, _, mult = _revenue_growth_stats(rev_clean)
    return mult


def _fundamental_quality_score(ebitda_series, revenue_series, fcf_series):
    """
    Quality gate: returns a discount multiplier (0.4 – 1.0) applied to final PT.
    Penalizes companies with deteriorating fundamentals across all three dimensions.
    A company firing on all cylinders gets 1.0 (no haircut).
    """
    score = 0.0
    checks = 0

    def _clean(s):
        return [float(v) for v in s
                if v is not None and not (isinstance(v,float) and np.isnan(v))]

    # Revenue trend (0–2 pts)
    rev = _clean(revenue_series)
    if len(rev) >= 3:
        checks += 2
        slope, _, r2 = _theil_sen(rev)
        mean_abs = np.mean(np.abs(rev))
        norm_slope = (slope / mean_abs) * r2 if mean_abs > 0 else 0
        if norm_slope > 0.02:   score += 2.0
        elif norm_slope > 0:    score += 1.2
        elif norm_slope > -0.02: score += 0.5
        # else 0 — declining revenue

    # EBITDA trend + margin (0–2 pts)
    ebitda = _clean(ebitda_series)
    if len(ebitda) >= 3 and len(rev) >= 3:
        checks += 2
        positive_ebitda = [v for v in ebitda if v > 0]
        ebitda_positive_ratio = len(positive_ebitda) / len(ebitda)
        slope, _, r2 = _theil_sen(ebitda)
        mean_abs = np.mean(np.abs(ebitda))
        norm_slope = (slope / mean_abs) * r2 if mean_abs > 0 else 0
        margin = ebitda[-1] / rev[-1] if rev[-1] > 0 else 0
        if ebitda_positive_ratio >= 0.75 and norm_slope > 0:
            score += 2.0
        elif ebitda_positive_ratio >= 0.5:
            score += 1.0 + (0.5 if norm_slope > 0 else 0)
        elif norm_slope > 0.02:
            score += 0.5   # improving but still mostly negative
        # else 0

    # FCF quality (0–1 pt)
    fcf = _clean(fcf_series)
    if len(fcf) >= 3:
        checks += 1
        pos_ratio = len([v for v in fcf if v > 0]) / len(fcf)
        slope, _, _ = _theil_sen(fcf)
        if pos_ratio >= 0.75:          score += 1.0
        elif pos_ratio >= 0.5:         score += 0.6
        elif slope > 0 and pos_ratio > 0: score += 0.3
        # else 0

    if checks == 0:
        return 0.70   # no data → conservative

    quality_ratio = score / checks   # 0.0 – 1.0
    # Map to discount multiplier: worst = 0.40, best = 1.0
    mult = 0.40 + 0.60 * quality_ratio
    return round(float(np.clip(mult, 0.40, 1.0)), 3)


def compute_v3_target_price(ebitda_series, revenue_series, fcf_series,
                             marketcap, debt, cash, shares, sector):
    """
    V3 multi-model conviction-weighted price target.
    No exclusions — works for any ticker (biotech, micro-cap, ADR, etc.)

    Enhancements vs original:
      - Growth-adjusted EV/Revenue multiple (fast growers get premium, decliners get haircut)
      - Fundamental quality discount applied to final blended PT
      - Mean-reversion cap: underperformers cannot exceed 1.5x sector anchor on EV/Rev

    Returns (target_price, model_breakdown_dict)
    """
    sm = SECTOR_MULTIPLES.get(sector, SECTOR_MULTIPLES["_default"])
    models = {}

    def _clean(s):
        return [float(v) for v in s
                if v is not None and not (isinstance(v,float) and np.isnan(v))]

    if not marketcap or marketcap <= 0 or not shares or shares <= 0:
        return None, {}

    # Pre-compute growth stats + quality score
    rev_clean_raw                              = _clean(revenue_series)
    median_qoq, ann_growth, is_emerging, growth_mult = _revenue_growth_stats(rev_clean_raw)
    quality_score = _fundamental_quality_score(ebitda_series, revenue_series, fcf_series)

    # Emerging growth flag: >25% QoQ median + low/no debt
    net_debt      = (debt or 0) - (cash or 0)
    net_debt_rev  = net_debt / (rev_clean_raw[-1]*4) if (rev_clean_raw and rev_clean_raw[-1]>0) else 999
    is_clean_bs   = net_debt_rev < 0.5   # net debt < 0.5x annualized revenue = clean balance sheet
    emerging      = is_emerging and is_clean_bs

    # Model 1: EV/EBITDA
    ebitda_clean = _clean(_rolling_median(ebitda_series))
    if len(ebitda_clean) >= 3:
        slope, intercept, r2 = _theil_sen(ebitda_clean)
        proj  = [slope*(len(ebitda_clean)+i)+intercept for i in range(1,5)]
        fwd   = sum(proj)
        cv    = float(np.std(ebitda_clean)/np.mean(np.abs(ebitda_clean))) if np.mean(np.abs(ebitda_clean)) > 0 else 1.0
        if fwd > 0:
            curr_ev       = marketcap + (debt or 0) - (cash or 0)
            trailing      = ebitda_clean[-1] * 4
            trail_mult    = curr_ev/trailing if trailing > 0 else sm["ev_ebitda"]
            trail_clipped = float(np.clip(trail_mult, 4, 40))
            if quality_score < 0.60:
                blended = min(0.60*sm["ev_ebitda"] + 0.40*trail_clipped, sm["ev_ebitda"])
            else:
                blended = 0.60*sm["ev_ebitda"] + 0.40*trail_clipped
            target_eq = fwd*blended - (debt or 0) + (cash or 0)
            if target_eq > 0:
                models["ev_ebitda"] = {
                    "pt": round(target_eq/shares,2), "r2": round(r2,3),
                    "conviction": _model_conviction(r2, len(ebitda_clean), cv),
                    "mult": round(blended,1), "sector_anchor": sm["ev_ebitda"],
                    "quality_score": quality_score,
                }

    # Model 2: EV/Revenue  (growth-adjusted)
    rev_clean = _clean(_rolling_median(revenue_series))
    if len(rev_clean) >= 3:
        slope, intercept, r2 = _theil_sen(rev_clean)
        proj = [slope*(len(rev_clean)+i)+intercept for i in range(1,5)]
        fwd  = sum(proj)
        cv   = float(np.std(rev_clean)/np.mean(np.abs(rev_clean))) if np.mean(np.abs(rev_clean)) > 0 else 1.0
        if fwd > 0:
            curr_ev       = marketcap + (debt or 0) - (cash or 0)
            trail_mult    = curr_ev/(rev_clean[-1]*4) if rev_clean[-1] > 0 else sm["ev_rev"]
            trail_clipped = float(np.clip(trail_mult, 0.2, 20))
            growth_adj_anchor = sm["ev_rev"] * growth_mult
            if quality_score < 0.60:
                blended = min(0.60*growth_adj_anchor + 0.40*trail_clipped, sm["ev_rev"])
            else:
                blended = 0.60*growth_adj_anchor + 0.40*trail_clipped
                blended = min(blended, sm["ev_rev"] * 2.5)
            target_eq = fwd*blended - (debt or 0) + (cash or 0)
            if target_eq > 0:
                models["ev_rev"] = {
                    "pt": round(target_eq/shares,2), "r2": round(r2,3),
                    "conviction": _model_conviction(r2, len(rev_clean), cv),
                    "mult": round(blended,2), "sector_anchor": sm["ev_rev"],
                    "growth_mult": round(growth_mult,2), "quality_score": quality_score,
                }

    # Model 3: FCF Yield
    fcf_clean    = _clean(fcf_series)
    positive_fcf = [v for v in fcf_clean if v > 0]
    if len(fcf_clean) >= 3 and len(positive_fcf) >= 3:
        slope, intercept, r2 = _theil_sen(fcf_clean)
        proj = [slope*(len(fcf_clean)+i)+intercept for i in range(1,5)]
        fwd  = sum(proj)
        cv   = float(np.std(fcf_clean)/np.mean(np.abs(fcf_clean))) if np.mean(np.abs(fcf_clean)) > 0 else 1.0
        if fwd > 0:
            quality_yield_adj = 1.0 + (1.0 - quality_score) * 0.5
            req_yield = sm["fcf_yield"] * quality_yield_adj
            pt3 = fwd/req_yield/shares
            c3  = _model_conviction(r2, len(fcf_clean), cv) * (len(positive_fcf)/len(fcf_clean))
            models["fcf_yield"] = {
                "pt": round(pt3,2), "r2": round(r2,3), "conviction": round(c3,4),
                "req_yield": round(req_yield*100,2), "sector_anchor": round(sm["fcf_yield"]*100,2),
                "quality_score": quality_score,
            }

    # Model 4: Emerging Growth  (TAM-based revenue run-rate projection)
    # Activates ONLY when: median QoQ rev growth >= 25% AND net debt < 0.5x annualized rev
    # Logic: project revenue forward at a decelerating growth curve (growth fades to sector
    # median over 3 years), then apply a high-growth EV/Rev multiple reflecting TAM capture.
    # This is how buyside values early-stage compounders — not on trailing multiples.
    if emerging and len(rev_clean_raw) >= 4:
        try:
            current_ann_rev = rev_clean_raw[-1] * 4   # annualize latest quarter

            # Deceleration curve: growth fades from median_qoq toward 5% annual over 3 years
            # Year 1: maintain ~80% of current QoQ pace (market already pricing some)
            # Year 2: fade to 50%
            # Year 3: fade to 25% of current pace (approaching maturity)
            target_mature_ann = 0.05   # 5% long-run growth
            decay = [0.80, 0.50, 0.25]
            rev_proj = current_ann_rev
            projected_revs = []
            for d in decay:
                growth_this_yr = max(target_mature_ann, median_qoq * 4 * d)  # annualize qoq
                rev_proj = rev_proj * (1 + growth_this_yr)
                projected_revs.append(rev_proj)

            # Year-3 projected revenue × high-growth EV/Rev multiple
            # Multiple = sector anchor × growth premium (scales with current growth rate)
            # At 25% QoQ (100%+ ann): use 4–6x sector anchor
            # At 50%+ QoQ: up to 8x sector anchor
            if median_qoq >= 0.50:
                tam_mult = sm["ev_rev"] * 7.0
            elif median_qoq >= 0.35:
                tam_mult = sm["ev_rev"] * 5.5
            elif median_qoq >= 0.25:
                tam_mult = sm["ev_rev"] * 4.0
            else:
                tam_mult = sm["ev_rev"] * 3.0

            # Discount year-3 value back to present at 25% discount rate (VC-style hurdle)
            discount_rate = 0.25
            pv_factor     = 1 / (1 + discount_rate)**3
            target_ev_eg  = projected_revs[-1] * tam_mult * pv_factor
            target_eq_eg  = target_ev_eg - (debt or 0) + (cash or 0)

            if target_eq_eg > 0:
                pt4 = target_eq_eg / shares
                # Conviction = f(growth consistency, R2 of revenue trend, balance sheet quality)
                slope_eg, _, r2_eg = _theil_sen(rev_clean_raw)
                cv_eg = float(np.std(rev_clean_raw)/np.mean(np.abs(rev_clean_raw))) if np.mean(np.abs(rev_clean_raw))>0 else 1.0
                c4    = _model_conviction(r2_eg, len(rev_clean_raw), cv_eg)
                # Extra conviction boost for very high sustained growth
                if median_qoq >= 0.35: c4 = min(c4 * 1.3, 1.0)

                models["emerging_growth"] = {
                    "pt":             round(pt4, 2),
                    "r2":             round(r2_eg, 3),
                    "conviction":     round(c4, 4),
                    "median_qoq":     round(median_qoq * 100, 1),
                    "ann_growth":     round(ann_growth * 100, 1),
                    "tam_mult":       round(tam_mult, 1),
                    "yr3_rev_proj":   round(projected_revs[-1]/1e6, 1),
                    "discount_rate":  25,
                    "sector_anchor":  sm["ev_rev"],
                }
        except Exception as e:
            print(f"  [WARN] emerging growth model: {e}")

    if not models:
        return None, {}

    # Weight models — give emerging_growth dominant weight when it fires
    model_keys = [k for k in models if k != "_meta"]
    convictions = {k: models[k]["conviction"] for k in model_keys}
    total_conv  = sum(convictions.values())
    if total_conv == 0:
        w = {k: 1.0/len(model_keys) for k in model_keys}
    else:
        w = {k: convictions[k]/total_conv for k in model_keys}

    # If emerging growth model fired, boost its weight to 50% and redistribute rest
    if "emerging_growth" in w and emerging:
        eg_weight = 0.50
        other_keys = [k for k in model_keys if k != "emerging_growth"]
        other_total = sum(w[k] for k in other_keys)
        if other_total > 0:
            for k in other_keys:
                w[k] = (w[k] / other_total) * (1.0 - eg_weight)
        w["emerging_growth"] = eg_weight

    raw_target = sum(w[k]*models[k]["pt"] for k in model_keys)

    # Quality discount — but exempt emerging growth companies from full haircut
    # (they are pre-profitability by design; quality score would unfairly penalize them)
    if emerging:
        # Lighter haircut: only penalize if quality_score is very low (<0.5)
        quality_adj = max(quality_score, 0.75) if quality_score >= 0.45 else quality_score
    else:
        quality_adj = quality_score

    final_target = raw_target * quality_adj

    for k in model_keys:
        models[k]["weight"] = round(w[k], 4)
    models["_meta"] = {
        "growth_mult":    round(growth_mult, 2),
        "median_qoq_pct": round(median_qoq*100, 1),
        "ann_growth_pct": round(ann_growth*100, 1),
        "emerging":       emerging,
        "quality_score":  round(quality_score, 3),
        "raw_pt":         round(raw_target, 2),
        "quality_haircut":round(1.0 - quality_adj, 3),
    }

    return round(final_target, 2), models

# ============================================================
# ANALYSIS ENGINE
# ============================================================
def compute_trend(values):
    clean = [float(v) for v in values
             if v is not None and not (isinstance(v,float) and np.isnan(v))]
    if len(clean) < 3:
        return 0.0, 0.0, 0.0
    x = np.arange(len(clean))
    y = np.array(clean)
    slope, intercept, r_val, _, _ = stats.linregress(x, y)
    mean_abs = np.mean(np.abs(y))
    if mean_abs == 0: return 0.0, 0.0, 0.0
    return (slope/mean_abs)*r_val**2, slope, r_val**2


def compute_rsi_wilder(closes, period=14):
    if len(closes) < period+1: return 50.0
    deltas   = np.diff(closes)
    gains    = np.where(deltas>0,deltas,0.0)
    losses   = np.where(deltas<0,-deltas,0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    alpha    = 1.0/period
    for i in range(period, len(deltas)):
        avg_gain = avg_gain*(1-alpha)+gains[i]*alpha
        avg_loss = avg_loss*(1-alpha)+losses[i]*alpha
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0/(1.0+avg_gain/avg_loss))


def run_analysis(ticker):
    print(f"\n{'='*55}")
    print(f"  Analyzing: {ticker}")
    print(f"{'='*55}")

    info    = load_ticker_info(ticker)
    csv_row = load_screener_row(ticker)   # None if ticker filtered out of screener — normal
    fund    = load_fundamentals(ticker)
    prices  = load_prices(ticker, days=365)

    print(f"  {info['name']} | {info['sector']} | {info['industry']}")
    n_quarters = fund.height if HAS_POLARS and hasattr(fund,'height') else 0
    print(f"  {n_quarters} quarters loaded")

    print("  Fetching Finnhub real-time price...")
    rt = fetch_realtime_price(ticker)
    print("  Fetching Finnhub analyst data...")
    analyst = fetch_analyst_data(ticker)
    print("  Fetching company news...")
    news = fetch_company_news(ticker)
    print("  Computing sector comps...")
    sector_comps = compute_sector_comps(ticker, info["sector"], fund)

    # If in screener CSV, override analyst fields with pre-computed values for consistency
    if csv_row:
        for key, csv_key in [("target_mean","analyst_target_mean"),
                              ("sentiment_score","sentiment_score"),
                              ("total_analysts","analyst_count"),
                              ("buy","analyst_buy"),
                              ("hold","analyst_hold"),
                              ("sell","analyst_sell")]:
            val = csv_row.get(csv_key)
            if val is not None:
                try:
                    analyst[key] = float(val) if key in ("sentiment_score","target_mean") else int(val)
                except Exception:
                    pass

    # Current price
    closes = np.array([])
    if HAS_POLARS and hasattr(prices,'height') and prices.height > 0:
        closes = prices["close"].to_numpy().astype(float)

    sharadar_price = float(closes[-1]) if len(closes) > 0 else None
    if rt["current"] and rt["current"] > 0:
        last_price, price_source = rt["current"], "finnhub_realtime"
    elif sharadar_price:
        last_price, price_source = sharadar_price, "sharadar_closeunadj"
    else:
        last_price, price_source = None, "unavailable"

    # Fundamental series
    def safe_col(col):
        if HAS_POLARS and hasattr(fund,"columns") and col in fund.columns:
            return fund[col].to_list()
        return []

    revenue     = safe_col("revenue")
    ebitda      = safe_col("ebitda")
    debt_series = safe_col("debt")
    ncfo        = safe_col("ncfo")
    capex       = safe_col("capex")
    marketcap_s = safe_col("marketcap")
    cash_s      = safe_col("cashnequsd")
    shares_s    = safe_col("shareswadil") or safe_col("shareswa")

    fcf_series = []
    if ncfo and capex:
        for n, cx in zip(ncfo, capex):
            try: fcf_series.append(float(n) - float(cx))
            except Exception: fcf_series.append(None)
    elif "fcf" in (fund.columns if HAS_POLARS and hasattr(fund,"columns") else []):
        fcf_series = safe_col("fcf")

    def cv(v):
        if v is None: return None
        try:
            f = float(v)
            return None if np.isnan(f) else f
        except Exception: return None

    latest_rev    = cv(revenue[-1])    if revenue    else None
    latest_ebitda = cv(ebitda[-1])     if ebitda     else None
    latest_fcf    = cv(fcf_series[-1]) if fcf_series else None
    latest_debt   = cv(debt_series[-1])if debt_series else None
    latest_mktcap = cv(marketcap_s[-1])if marketcap_s else None
    latest_cash   = cv(cash_s[-1])     if cash_s     else 0.0
    shares_clean  = [float(v) for v in shares_s
                     if v is not None and not (isinstance(v,float) and np.isnan(v)) and float(v)>0]
    latest_shares = (shares_clean[-1] if shares_clean else
                     (latest_mktcap/last_price if latest_mktcap and last_price and last_price>0 else None))

    # Trends
    rev_trend,   _, rev_r2   = compute_trend(revenue)
    ebitda_trend, _, _       = compute_trend(ebitda)
    fcf_trend,   _, _        = compute_trend(fcf_series)
    debt_trend_raw, _, _     = compute_trend(debt_series)
    debt_trend               = -debt_trend_raw

    # EBITDA margin
    margins = []
    for r2, e2 in zip(revenue, ebitda):
        r_v, e_v = cv(r2), cv(e2)
        if r_v and r_v>0 and e_v is not None: margins.append(e_v/r_v)
    margin_latest    = margins[-1] if margins else None
    margin_avg       = float(np.mean(margins)) if len(margins)>=3 else None
    margin_expanding = np.mean(margins[-2:]) > np.mean(margins[:2]) if len(margins)>=4 else False

    # Share signal
    share_change_pct, share_signal = None, "neutral"
    if len(shares_clean) >= 4:
        old = np.mean(shares_clean[:2])
        new = np.mean(shares_clean[-2:])
        if old > 0:
            share_change_pct = ((new/old)-1)*100
            share_signal = ("buyback" if share_change_pct<-1 else
                            "dilution" if share_change_pct>2 else "stable")

    # Debt signal
    debt_clean = [cv(v) for v in debt_series if cv(v) is not None]
    debt_paydown_signal, debt_paydown_rate = "neutral", None
    if len(debt_clean) >= 4 and debt_clean[0] > 0:
        reduction = debt_clean[0] - debt_clean[-1]
        n_periods = len(debt_clean)-1
        debt_paydown_rate = (reduction/debt_clean[0])/n_periods
        debt_paydown_signal = ("active_deleveraging"  if reduction>0  and debt_paydown_rate>0.01 else
                               "increasing_leverage"  if reduction<0  and abs(debt_paydown_rate)>0.01 else
                               "neutral")

    fcf_yield = None
    if latest_fcf and latest_mktcap and latest_mktcap>0:
        fcf_yield = (latest_fcf*4)/latest_mktcap

    net_debt_ebitda = None
    if latest_debt is not None and latest_ebitda and latest_ebitda>0:
        net_debt_ebitda = round((latest_debt-(latest_cash or 0))/(latest_ebitda*4),2)

    debt_coverage = None
    if latest_debt and latest_debt>0:
        net_d = latest_debt-(latest_cash or 0)
        if net_d>0 and latest_fcf:
            debt_coverage = round(((latest_cash or 0)+max(latest_fcf*4,0))/net_d,2)

    # Technicals
    rsi      = compute_rsi_wilder(closes) if len(closes)>20 else 50.0
    mom_60d  = ((closes[-1]/closes[-60])-1)*100 if len(closes)>=60 else 0.0
    sma_20   = float(np.mean(closes[-20:]))  if len(closes)>=20  else None
    sma_50   = float(np.mean(closes[-50:]))  if len(closes)>=50  else None
    sma_200  = float(np.mean(closes[-200:])) if len(closes)>=200 else None
    hi_52    = float(np.max(closes[-252:]))  if len(closes)>=20  else None
    lo_52    = float(np.min(closes[-252:]))  if len(closes)>=20  else None
    vol_60   = float(np.std(np.diff(np.log(closes[-60:])))*np.sqrt(252)*100) if len(closes)>30 else None

    # ── PRICE TARGET ──────────────────────────────────────────
    # Day 5: Always run shared price_targets engine. Replaces the old
    # CSV-read fork (Path 1) that inherited the screener's broken PT engine
    # and bypassed the report's R²-floor / envelope / FCF-cap gates.
    import price_targets as _pt_engine

    internal_target    = None
    pt_model_breakdown = {}
    pt_source          = "none"
    divergence_flagged = False

    print("  Computing PT via shared price_targets engine...")

    if latest_mktcap and latest_shares and last_price:
        _res = _pt_engine.compute_target_price(
            ebitda_series    = ebitda,
            revenue_series   = revenue,
            fcf_series       = fcf_series,
            debt_series      = debt_series,
            marketcap        = float(latest_mktcap),
            last_price       = float(last_price),
            cash_on_hand     = float(latest_cash or 0),
            shares_diluted   = float(latest_shares) if latest_shares else None,
            sector           = info["sector"],
            fed_target_rate  = 0.03625,
            fed_neutral_rate = 0.0300,
            analyst_target   = cv(analyst.get("target_mean")),
            n_analysts       = int(analyst.get("total_analysts") or 0),
            apply_envelope   = True,
        )
        internal_target    = _res.target_price
        pt_model_breakdown = _res.breakdown.get("models", {})
        pt_source          = _res.pt_source
        divergence_flagged = _res.divergence_flag

        if internal_target:
            print(f"  Internal PT: ${internal_target:.2f}  source={pt_source}  "
                  f"models={list(pt_model_breakdown.keys())}  "
                  f"gates={_res.gates_fired}")
        else:
            print("  PT engine returned None — insufficient model fit")
    else:
        print("  Missing mktcap/shares/price — PT unavailable")

    analyst_target = cv(analyst.get("target_mean"))

    upside_pct = ((internal_target/last_price)-1)*100 if (
        internal_target and last_price and last_price>0) else None

    # ── CONVICTION SCORING ────────────────────────────────────
    conviction_score = 0.0
    breakdown        = {}

    val_contrib = 0.0
    if upside_pct is not None:
        val_contrib = (min(np.sqrt(upside_pct/10.0)*1.0, 5.0) if upside_pct>0
                       else max(upside_pct/10.0,-3.0))
    conviction_score += val_contrib; breakdown["valuation"] = round(val_contrib,2)

    fund_avg     = np.mean([rev_trend,ebitda_trend,fcf_trend,debt_trend])
    fund_contrib = (1.5 if fund_avg>0.05 else 1.0 if fund_avg>0.02 else
                    0.3 if fund_avg>0 else -0.3 if fund_avg>-0.02 else -1.0)
    conviction_score += fund_contrib; breakdown["fundamentals"] = round(fund_contrib,2)

    mom_contrib = (1.0 if mom_60d>15 else 0.5 if mom_60d>5 else
                   0.0 if mom_60d>-5 else -0.5 if mom_60d>-15 else -1.0)
    conviction_score += mom_contrib; breakdown["momentum"] = round(mom_contrib,2)

    rsi_contrib = (0.5 if 50<=rsi<=65 else 0.2 if 40<=rsi<=70 else
                   -1.0 if rsi>80 else -0.5 if rsi<30 else 0.0)
    conviction_score += rsi_contrib; breakdown["rsi"] = round(rsi_contrib,2)

    sent        = analyst.get("sentiment_score",0)
    sent_contrib = (1.0 if sent>0.4 else 0.5 if sent>0.2 else
                    -0.5 if sent<-0.2 else -1.0 if sent<-0.4 else 0.0)
    conviction_score += sent_contrib; breakdown["sentiment"] = round(sent_contrib,2)

    cap_contrib = 0.0
    if share_signal=="buyback":                   cap_contrib += 0.75
    elif share_signal=="dilution":                cap_contrib -= 0.75
    if debt_paydown_signal=="active_deleveraging": cap_contrib += 0.5
    elif debt_paydown_signal=="increasing_leverage": cap_contrib -= 0.5
    conviction_score += cap_contrib; breakdown["capital_alloc"] = round(cap_contrib,2)

    margin_contrib = 0.5 if margin_expanding else 0.0
    conviction_score += margin_contrib; breakdown["margin"] = round(margin_contrib,2)

    fcf_contrib = (0.5 if fcf_yield and fcf_yield>0.08 else
                   -0.5 if fcf_yield and fcf_yield<-0.02 else 0.0)
    conviction_score += fcf_contrib; breakdown["fcf_yield"] = round(fcf_contrib,2)

    conviction_score = round(conviction_score,2)
    if   conviction_score >= 2.5:  recommendation, rec_color = "BUY",  RCG_GREEN
    elif conviction_score <= -2.0: recommendation, rec_color = "SELL", RCG_RED
    else:                          recommendation, rec_color = "HOLD", RCG_AMBER

    # ── THESIS + RISKS ────────────────────────────────────────
    thesis, risks = [], []
    if rev_trend > 0.01:    thesis.append(f"Revenue trending positively (score: {rev_trend:.3f})")
    if ebitda_trend > 0.01: thesis.append(f"EBITDA growth trajectory (score: {ebitda_trend:.3f})")
    if fcf_trend > 0.01:    thesis.append(f"Free cash flow improving (score: {fcf_trend:.3f})")
    if debt_trend > 0.01:   thesis.append("Debt actively being reduced")
    if upside_pct and upside_pct>10:
        thesis.append(f"Internal model implies {upside_pct:.1f}% upside to ${internal_target:.2f}")
    if mom_60d > 5:         thesis.append(f"Positive price momentum: {mom_60d:+.1f}% over 60d")
    if sent > 0.2:
        thesis.append(f"Analyst consensus positive ({analyst['buy']}B/{analyst['hold']}H/{analyst['sell']}S)")
    if net_debt_ebitda is not None and net_debt_ebitda<3:
        thesis.append(f"Healthy leverage: Net Debt/EBITDA = {net_debt_ebitda:.1f}x")
    if share_signal=="buyback" and share_change_pct is not None:
        thesis.append(f"Active buyback: shares down {abs(share_change_pct):.1f}%")
    if margin_expanding and margin_latest:
        thesis.append(f"EBITDA margin expanding: {margin_latest*100:.1f}% vs avg {margin_avg*100:.1f}%")
    if fcf_yield and fcf_yield>0.05:
        thesis.append(f"Attractive FCF yield: {fcf_yield*100:.1f}% annualized")

    if vol_60 and vol_60>35:       risks.append(f"Elevated realized vol: {vol_60:.1f}% ann. (60d)")
    if rsi > 70:                   risks.append(f"RSI {rsi:.0f} — overbought")
    elif rsi < 30:                 risks.append(f"RSI {rsi:.0f} — oversold")
    if net_debt_ebitda is not None and net_debt_ebitda>4:
        risks.append(f"High leverage: Net Debt/EBITDA = {net_debt_ebitda:.1f}x")
    if upside_pct is not None and upside_pct<0:
        risks.append(f"Trading above internal model target (downside: {upside_pct:.1f}%)")
    if debt_coverage is not None and debt_coverage<0.5:
        risks.append(f"Weak debt coverage: {debt_coverage:.2f}x")
    if share_signal=="dilution" and share_change_pct:
        risks.append(f"Share dilution: shares up {share_change_pct:.1f}% over trailing quarters")
    if debt_paydown_signal=="increasing_leverage":
        risks.append("Debt increasing over trailing quarters")
    if divergence_flagged and analyst_target:
        pct = ((internal_target-analyst_target)/last_price*100
               if internal_target and last_price else 0)
        risks.append(f"⚠ Model vs analyst divergence >40% (analyst: ${analyst_target:.2f}, diff: {pct:+.1f}%)")
    if not risks:
        risks.append("No significant risk flags in current data")

    return {
        "ticker": ticker, "info": info,
        "recommendation": recommendation, "rec_color": rec_color,
        "conviction_score": conviction_score, "conviction_breakdown": breakdown,
        "last_price": last_price, "price_source": price_source, "rt_price": rt,
        "internal_target": internal_target, "analyst_target": analyst_target,
        "upside_pct": upside_pct, "pt_source": pt_source,
        "divergence_flagged": divergence_flagged,
        "pt_model_breakdown": pt_model_breakdown,
        "from_screener_csv": csv_row is not None,
        "thesis_points": thesis, "risk_points": risks,
        "fundamentals": {
            "latest_revenue":      latest_rev,    "latest_ebitda":    latest_ebitda,
            "latest_fcf":          latest_fcf,    "latest_debt":      latest_debt,
            "latest_cash":         latest_cash,   "marketcap":        latest_mktcap,
            "net_debt_ebitda":     net_debt_ebitda,"debt_coverage":   debt_coverage,
            "rev_trend":           rev_trend,      "ebitda_trend":    ebitda_trend,
            "fcf_trend":           fcf_trend,      "debt_trend":      debt_trend,
            "ebitda_margin":       margin_latest,  "ebitda_margin_avg":margin_avg,
            "margin_expanding":    margin_expanding,"fcf_yield":       fcf_yield,
            "share_change_pct":    share_change_pct,"share_signal":   share_signal,
            "debt_paydown_signal": debt_paydown_signal,"debt_paydown_rate":debt_paydown_rate,
        },
        "technicals": {
            "rsi":rsi,"mom_60d":mom_60d,"sma_20":sma_20,"sma_50":sma_50,
            "sma_200":sma_200,"hi_52":hi_52,"lo_52":lo_52,"volatility":vol_60,
        },
        "analyst": analyst, "news": news, "sector_comps": sector_comps, "n_quarters": n_quarters,
        "run_date": datetime.now().strftime("%B %d, %Y"),
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

# ============================================================
# FORMATTING HELPERS
# ============================================================
def fmt_money(v):
    if v is None: return "N/A"
    v = float(v)
    if abs(v) >= 1e9: return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.0f}"

def fmt_pct(v, d=1):
    if v is None: return "N/A"
    return f"{v:+.{d}f}%"

def fmt_price(v):
    if v is None: return "N/A"
    return f"${float(v):.2f}"

def _wrap(text, max_chars):
    words = text.split()
    lines, line = [], ""
    for w in words:
        test = f"{line} {w}" if line else w
        if len(test) <= max_chars: line = test
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    return lines

# ============================================================
# PDF GENERATION
# ============================================================
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _section_header(c, x, y, w, title):
    c.setFillColor(RCG_GOLD)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x, y, title)
    y -= 3
    c.setStrokeColor(RCG_BORDER)
    c.setLineWidth(0.5)
    c.line(x, y, x+w, y)
    return y - 10


def _data_table(c, x, y, w, rows, bottom=65):
    for i, (label, value) in enumerate(rows):
        if y < bottom: break
        if i % 2 == 0:
            c.setFillColor(RCG_NAVY_MID)
            c.rect(x, y-3, w, 12, fill=True, stroke=False)
        c.setFont("Helvetica", 7)
        c.setFillColor(RCG_TEXT_DIM)
        c.drawString(x+4, y, str(label))
        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(RCG_TEXT)
        c.drawRightString(x+w-4, y, str(value))
        y -= 12
    return y


def _trend_table(c, x, y, w, rows, bottom=65):
    for i, (label, value, positive) in enumerate(rows):
        if y < bottom: break
        if i % 2 == 0:
            c.setFillColor(RCG_NAVY_MID)
            c.rect(x, y-3, w, 12, fill=True, stroke=False)
        c.setFont("Helvetica", 7)
        c.setFillColor(RCG_TEXT_DIM)
        c.drawString(x+4, y, label)
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(RCG_GREEN if positive else RCG_RED)
        c.drawRightString(x+w-4, y, value)
        y -= 12
    return y


def draw_report(c, data):
    width, height = letter
    margin  = 32
    usable  = width - 2 * margin
    gutter  = 10
    col_w   = (usable - gutter) / 2
    left_x  = margin
    rgt_x   = margin + col_w + gutter

    # ── Layout constants ────────────────────────────────────
    HEADER_H     = 64    # top header bar
    BANNER_H     = 34    # rec banner
    META_H       = 22    # conviction + data badge rows
    FOOTER_H     = 52    # footer disclaimer area
    NEWS_H       = 82    # reserved for news strip (6 rows × ~11pt + header)
    COMPS_H      = 72    # reserved for comps strip (4 rows × 12pt + header)
    BOTTOM_STRIP = FOOTER_H + NEWS_H + COMPS_H  # 206pt from bottom
    COL_TOP      = height - HEADER_H - BANNER_H - META_H - 8
    COL_BOTTOM   = FOOTER_H + NEWS_H + COMPS_H + 8   # columns must stop here

    # ── Background ───────────────────────────────────────────
    c.setFillColor(RCG_NAVY)
    c.rect(0, 0, width, height, fill=True, stroke=False)

    # ── Header bar ───────────────────────────────────────────
    c.setFillColor(RCG_NAVY_LIGHT)
    c.rect(0, height - HEADER_H, width, HEADER_H, fill=True, stroke=False)
    c.setStrokeColor(RCG_GOLD)
    c.setLineWidth(2)
    c.line(0, height - HEADER_H, width, height - HEADER_H)

    c.setFillColor(RCG_GOLD)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, height - 30, "Investment Analysis Report")
    c.setFont("Helvetica", 8)
    c.setFillColor(RCG_TEXT_DIM)
    c.drawString(margin, height - 43, f"Robin Capital Group LLC  |  {data['run_date']}")

    c.setFillColor(RCG_GOLD)
    c.setFont("Helvetica-Bold", 24)
    tw = c.stringWidth(data["ticker"], "Helvetica-Bold", 24)
    c.drawString(width - margin - tw, height - 32, data["ticker"])
    c.setFont("Helvetica", 7)
    c.setFillColor(RCG_TEXT_DIM)
    c.drawRightString(width - margin, height - 44, data["info"]["name"][:45])
    c.drawRightString(width - margin, height - 54,
                      f"{data['info']['sector']}  |  {data['info']['industry']}"[:55])

    # ── Recommendation banner ────────────────────────────────
    banner_y = height - HEADER_H - 4
    rec      = data["recommendation"]
    rec_bg   = hex_to_rl("#0f3d1e" if rec == "BUY" else
                         "#3d0f0f" if rec == "SELL" else "#3d3310")
    c.setFillColor(rec_bg)
    c.roundRect(margin, banner_y - BANNER_H + 4, usable, BANNER_H - 2, 4,
                fill=True, stroke=False)
    c.setFillColor(data["rec_color"])
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin + 8, banner_y - 18, f"Recommendation:  {rec}")
    c.setFont("Helvetica", 8)
    c.setFillColor(RCG_TEXT_DIM)
    conv_x = margin + 8 + c.stringWidth(f"Recommendation:  {rec}  ", "Helvetica-Bold", 16)
    c.drawString(conv_x, banner_y - 17, f"conviction: {data['conviction_score']:+.1f}")

    if data["last_price"]:
        pt_src   = data["pt_source"]
        div_mark = "  ■ diverges >40% from analyst" if data["divergence_flagged"] else ""
        pstr     = (f"Price: {fmt_price(data['last_price'])}    "
                    f"Internal PT [{pt_src}]: {fmt_price(data['internal_target'])}    "
                    f"Upside: {fmt_pct(data['upside_pct'])}{div_mark}")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(RCG_TEXT)
        pw = c.stringWidth(pstr, "Helvetica", 7.5)
        c.drawRightString(width - margin - 6, banner_y - 18, pstr)
        src_label = {"finnhub_realtime": "real-time (Finnhub)",
                     "sharadar_closeunadj": "last close (Sharadar)"}.get(
                     data["price_source"], data["price_source"])
        c.setFont("Helvetica", 5)
        c.setFillColor(RCG_TEXT_DIM)
        c.drawRightString(width - margin - 6, banner_y - 28, f"price: {src_label}")

    # ── Conviction + data badge row ──────────────────────────
    meta_y = banner_y - BANNER_H + 2
    bd     = data.get("conviction_breakdown", {})
    if bd:
        c.setFont("Helvetica", 5.5)
        c.setFillColor(RCG_TEXT_DIM)
        parts = [f"{k}: {v:+.1f}" for k, v in bd.items() if v != 0]
        c.drawString(margin, meta_y, "Conviction:  " + "  |  ".join(parts))
    meta_y -= 9

    src_badge = ("DATA: Screener CSV + Finnhub" if data["from_screener_csv"]
                 else "DATA: Computed independently — Sharadar + Finnhub")
    c.setFont("Helvetica", 5.5)
    c.setFillColor(RCG_GOLD if data["from_screener_csv"] else RCG_TEXT_DIM)
    c.drawString(margin, meta_y, src_badge)

    # ── Column divider line ──────────────────────────────────
    c.setStrokeColor(RCG_BORDER)
    c.setLineWidth(0.4)
    c.line(rgt_x - gutter/2, COL_TOP - 4, rgt_x - gutter/2, COL_BOTTOM)

    # ── LEFT COLUMN ──────────────────────────────────────────
    y = COL_TOP

    # Investment Thesis
    y = _section_header(c, left_x, y, col_w, "Investment Thesis")
    c.setFont("Helvetica", 6.5)
    for point in data["thesis_points"][:6]:
        if y < COL_BOTTOM: break
        c.setFillColor(RCG_GOLD)
        c.drawString(left_x + 4, y, "+")
        c.setFillColor(RCG_TEXT)
        for ln in _wrap(point, 46):
            if y < COL_BOTTOM: break
            c.drawString(left_x + 13, y, ln)
            y -= 8.5
        y -= 2
    y -= 5

    # Fundamental Snapshot
    if y > COL_BOTTOM + 20:
        y = _section_header(c, left_x, y, col_w, "Fundamental Snapshot")
        f   = data["fundamentals"]
        shr = {"buyback": "Buyback", "dilution": "Dilution",
               "stable": "Stable", "neutral": "Stable"}.get(f.get("share_signal", ""), "N/A")
        if f.get("share_change_pct") is not None:
            shr += f" ({f['share_change_pct']:+.1f}%)"
        dbt = {"active_deleveraging": "Deleveraging", "increasing_leverage": "Increasing",
               "neutral": "Stable"}.get(f.get("debt_paydown_signal", ""), "N/A")
        snap_rows = [
            ("Market Cap",       fmt_money(f["marketcap"])),
            ("Revenue (Q)",      fmt_money(f["latest_revenue"])),
            ("EBITDA (Q)",       fmt_money(f["latest_ebitda"])),
            ("EBITDA Margin",    f"{f['ebitda_margin']*100:.1f}%" if f.get("ebitda_margin") else "N/A"),
            ("FCF (Q)",          fmt_money(f["latest_fcf"])),
            ("FCF Yield (ann.)", f"{f['fcf_yield']*100:.1f}%" if f.get("fcf_yield") else "N/A"),
            ("Total Debt",       fmt_money(f["latest_debt"])),
            ("Cash",             fmt_money(f["latest_cash"])),
            ("Net Debt/EBITDA",  f"{f['net_debt_ebitda']:.1f}x" if f["net_debt_ebitda"] is not None else "N/A"),
            ("Debt Coverage",    f"{f['debt_coverage']:.2f}x" if f["debt_coverage"] is not None else "N/A"),
            ("Share Trend",      shr),
            ("Debt Trajectory",  dbt),
        ]
        y = _data_table(c, left_x, y, col_w, snap_rows, bottom=COL_BOTTOM)
        y -= 5

    # Fundamental Trend Scores
    if y > COL_BOTTOM + 20:
        y = _section_header(c, left_x, y, col_w, "Fundamental Trend Scores")
        y = _trend_table(c, left_x, y, col_w, [
            ("Revenue Trend",  f"{f['rev_trend']:.4f}",    f["rev_trend"] > 0),
            ("EBITDA Trend",   f"{f['ebitda_trend']:.4f}", f["ebitda_trend"] > 0),
            ("FCF Trend",      f"{f['fcf_trend']:.4f}",    f["fcf_trend"] > 0),
            ("Debt Reduction", f"{f['debt_trend']:.4f}",   f["debt_trend"] > 0),
        ], bottom=COL_BOTTOM)

    # ── RIGHT COLUMN ─────────────────────────────────────────
    ry = COL_TOP

    # Risk Factors
    ry = _section_header(c, rgt_x, ry, col_w, "Risk Factors")
    c.setFont("Helvetica", 6.5)
    for point in data["risk_points"][:6]:
        if ry < COL_BOTTOM: break
        c.setFillColor(RCG_RED)
        c.drawString(rgt_x + 4, ry, "!")
        c.setFillColor(RCG_TEXT)
        for ln in _wrap(point, 46):
            if ry < COL_BOTTOM: break
            c.drawString(rgt_x + 13, ry, ln)
            ry -= 8.5
        ry -= 2
    ry -= 5

    # Technical Snapshot
    if ry > COL_BOTTOM + 20:
        ry  = _section_header(c, rgt_x, ry, col_w, "Technical Snapshot")
        rt  = data.get("rt_price", {})
        tec = data["technicals"]
        tech_rows = [("Current Price", fmt_price(data["last_price"]))]
        if rt.get("previous_close"): tech_rows.append(("Prev Close", fmt_price(rt["previous_close"])))
        if rt.get("open"):           tech_rows.append(("Open",       fmt_price(rt["open"])))
        if rt.get("high"):           tech_rows.append(("High",       fmt_price(rt["high"])))
        if rt.get("low"):            tech_rows.append(("Low",        fmt_price(rt["low"])))
        tech_rows += [
            ("RSI (14d)",        f"{tec['rsi']:.1f}"),
            ("60d Momentum",     fmt_pct(tec["mom_60d"])),
            ("SMA 20",           fmt_price(tec["sma_20"])),
            ("SMA 50",           fmt_price(tec["sma_50"])),
            ("SMA 200",          fmt_price(tec["sma_200"])),
            ("52w High",         fmt_price(tec["hi_52"])),
            ("52w Low",          fmt_price(tec["lo_52"])),
            ("Vol 60d ann.",     f"{tec['volatility']:.1f}%" if tec["volatility"] else "N/A"),
        ]
        ry = _data_table(c, rgt_x, ry, col_w, tech_rows, bottom=COL_BOTTOM)
        ry -= 5

    # Analyst Consensus
    if ry > COL_BOTTOM + 20:
        ry = _section_header(c, rgt_x, ry, col_w, "Analyst Consensus  (reference only)")
        a  = data["analyst"]
        ry = _data_table(c, rgt_x, ry, col_w, [
            ("Analysts",        str(a["total_analysts"])),
            ("Strong Buy / Buy", f"{a['strongBuy']} / {a['buy']}"),
            ("Hold",            str(a["hold"])),
            ("Sell / Str Sell", f"{a['sell']} / {a['strongSell']}"),
            ("Consensus Score", f"{a['sentiment_score']:+.2f}"),
            ("Analyst Target",  fmt_price(a["target_mean"])),
            ("Target High/Low", f"{fmt_price(a['target_high'])} / {fmt_price(a['target_low'])}"),
        ], bottom=COL_BOTTOM)
        ry -= 5

    # Price Target Summary
    if ry > COL_BOTTOM + 20:
        ry = _section_header(c, rgt_x, ry, col_w, "Price Target Summary (v3)")
        src_labels = {
            "M":    "Internal model",
            "M\u2713": "Model — analyst aligned",
            "M*":   "Model — analyst divergence ⚠",
            "A":    "Analyst fallback",
            "none": "N/A",
        }
        div_row = (
            f"{abs(data['internal_target']-data['analyst_target'])/data['last_price']*100:.1f}% of price"
            if (data["divergence_flagged"] and data["internal_target"]
                and data["analyst_target"] and data["last_price"])
            else "Within threshold"
        )
        ry = _data_table(c, rgt_x, ry, col_w, [
            ("Internal PT",   fmt_price(data["internal_target"])),
            ("Analyst Target",fmt_price(data["analyst_target"])),
            ("PT Source",     src_labels.get(data["pt_source"], data["pt_source"])),
            ("Divergence",    div_row),
            ("Implied Upside",fmt_pct(data["upside_pct"])),
            ("Data Path",     "Screener CSV" if data["from_screener_csv"] else "Independent"),
        ], bottom=COL_BOTTOM)

    # ── INDUSTRY COMPS STRIP  (full width, above news) ───────
    sc     = data.get("sector_comps", {})
    comp_y = FOOTER_H + NEWS_H + COMPS_H - 4   # top of comps strip

    c.setStrokeColor(RCG_BORDER)
    c.setLineWidth(0.4)
    c.line(margin, comp_y + 2, margin + usable, comp_y + 2)

    if sc:
        comp_y = _section_header(c, margin, comp_y, usable,
                                 f"Industry Comparison — {sc.get('sector','')}  "
                                 f"({sc.get('n_peers',0)} Sharadar peers)")

        def _comp_val(tkr, peer, fmt_fn, higher_better):
            tkr_str  = fmt_fn(tkr)  if tkr  is not None else "N/A"
            peer_str = fmt_fn(peer) if peer is not None else "N/A"
            if tkr is not None and peer is not None:
                diff  = tkr - peer
                arrow = "▲" if diff > 0 else "▼" if diff < 0 else "—"
                good  = (diff > 0) == higher_better
                vs    = f"{arrow} {abs(diff):.1f}"
            else:
                vs, good = "—", None
            return tkr_str, peer_str, vs, good

        comp_rows = [
            ("Rev Growth QoQ",
             *_comp_val(sc.get("rev_growth_tkr"), sc.get("rev_growth_peer"),
                        lambda v: f"{v:+.1f}%", True)),
            ("EBITDA Margin",
             *_comp_val(sc.get("ebitda_margin_tkr"), sc.get("ebitda_margin_peer"),
                        lambda v: f"{v:.1f}%", True)),
            ("EV / Revenue",
             *_comp_val(sc.get("ev_rev_tkr"), sc.get("ev_rev_peer"),
                        lambda v: f"{v:.2f}x", False)),
            ("Net Debt / EBITDA",
             *_comp_val(sc.get("net_debt_ebitda_tkr"), sc.get("net_debt_ebitda_peer"),
                        lambda v: f"{v:.2f}x", False)),
        ]

        for idx, (label, tkr_str, peer_str, vs, good) in enumerate(comp_rows):
            if idx % 2 == 0:
                c.setFillColor(RCG_NAVY_MID)
                c.rect(margin, comp_y - 3, usable, 12, fill=True, stroke=False)
            c.setFont("Helvetica", 7)
            c.setFillColor(RCG_TEXT_DIM)
            c.drawString(margin + 4, comp_y, label)
            c.setFont("Helvetica-Bold", 7)
            c.setFillColor(RCG_TEXT)
            c.drawString(margin + 115, comp_y, tkr_str)
            c.setFont("Helvetica", 7)
            c.setFillColor(RCG_TEXT_DIM)
            c.drawString(margin + 175, comp_y, f"Sector median: {peer_str}")
            if good is not None:
                c.setFont("Helvetica-Bold", 7)
                c.setFillColor(RCG_GREEN if good else RCG_RED)
                c.drawRightString(margin + usable - 4, comp_y, vs)
            comp_y -= 12

    # ── NEWS STRIP  (full width, above footer) ────────────────
    news_y = FOOTER_H + NEWS_H - 2

    c.setStrokeColor(RCG_BORDER)
    c.setLineWidth(0.4)
    c.line(margin, news_y + 2, margin + usable, news_y + 2)

    news = data.get("news", [])
    if news:
        news_y = _section_header(c, margin, news_y, usable, "Recent News & Corporate Developments")
        for i, item in enumerate(news[:6]):
            if news_y < FOOTER_H: break
            if i % 2 == 0:
                c.setFillColor(RCG_NAVY_MID)
                c.rect(margin, news_y - 3, usable, 11, fill=True, stroke=False)
            c.setFont("Helvetica-Bold", 6)
            c.setFillColor(RCG_GOLD)
            c.drawString(margin + 4, news_y, item["date"])
            src = item.get("source", "")
            if src:
                c.setFont("Helvetica", 6)
                c.setFillColor(RCG_TEXT_DIM)
                c.drawString(margin + 38, news_y, f"[{src[:16]}]")
            headline = item["headline"]
            max_w    = usable - 115
            while c.stringWidth(headline, "Helvetica", 6) > max_w and len(headline) > 20:
                headline = headline[:-4] + "…"
            c.setFont("Helvetica", 6)
            c.setFillColor(RCG_TEXT)
            c.drawString(margin + 108, news_y, headline)
            news_y -= 11

    # ── FOOTER ───────────────────────────────────────────────
    c.setStrokeColor(RCG_BORDER)
    c.setLineWidth(0.5)
    c.line(margin, FOOTER_H - 4, width - margin, FOOTER_H - 4)
    c.setFont("Helvetica", 5)
    c.setFillColor(RCG_TEXT_DIM)
    c.drawString(margin, FOOTER_H - 14,
                 f"Robin Capital Group LLC  |  {data['run_time']}  |  "
                 f"Data: Sharadar + Finnhub  |  {data['n_quarters']}Q fundamentals  |  "
                 f"Author: Nick Diaz, CIO")
    disclaimer = ("Disclaimer: This report is for informational purposes only. "
                  "Models and assumptions are proprietary to RCG and do not constitute "
                  "financial advice. Investments may lose value. Perform your own due diligence.")
    c.setFont("Helvetica", 4)
    words = disclaimer.split()
    line, dy = "", FOOTER_H - 22
    for w in words:
        test = f"{line} {w}" if line else w
        if c.stringWidth(test, "Helvetica", 4) < usable:
            line = test
        else:
            c.drawString(margin, dy, line)
            dy -= 5
            line = w
    if line:
        c.drawString(margin, dy, line)


# ============================================================
# ENTRY POINT
# ============================================================
def generate_report(ticker):
    data     = run_analysis(ticker)
    filename = f"{ticker}_RCG_Analysis.pdf"
    filepath = OUTPUT_DIR / filename

    from reportlab.pdfgen import canvas as rl_canvas
    cv = rl_canvas.Canvas(str(filepath), pagesize=letter)
    draw_report(cv, data)
    cv.save()

    print(f"\n  PDF: {filepath.resolve()}")
    print(f"  Recommendation: {data['recommendation']}  (conviction: {data['conviction_score']:+.2f})")
    if data["internal_target"]:
        print(f"  Internal PT [{data['pt_source']}]: {fmt_price(data['internal_target'])}"
              f"  ({fmt_pct(data['upside_pct'])} upside)")
    if data["analyst_target"]:
        print(f"  Analyst target (ref): {fmt_price(data['analyst_target'])}")
    if data["divergence_flagged"]:
        print(f"  ⚠  Divergence flagged — model vs analyst gap >40%")
    print(f"  Data path: {'Screener CSV' if data['from_screener_csv'] else 'Independent compute (any ticker)'}")
    bd = data.get("conviction_breakdown", {})
    if bd:
        print("  Conviction breakdown:")
        for k, v in bd.items():
            print(f"    {k:20s}: {v:+.2f}")
        print(f"    {'TOTAL':20s}: {data['conviction_score']:+.2f}")

    return filepath, data


# Functions loaded. Call generate_report("TICKER") to run.
