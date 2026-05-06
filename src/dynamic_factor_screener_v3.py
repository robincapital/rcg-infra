"""
Dynamic Factor-Weighted Fundamental + Technical Screener  v2.0
===============================================================
Robin Capital Group LLC

CHANGES FROM v1:
  [FIX] EV/target price calc now uses actual cashnequsd instead of FCF proxy
  [FIX] RSI uses Wilder's exponential smoothing (EMA) instead of SMA
  [FIX] Sensitivity matrix enforced zero-sum per factor (no weight inflation)
  [NEW] Biotech/pharma exclusion filter (binary outcome names)
  [NEW] Sector concentration cap (max 5 names per sector in top results)
  [NEW] Blended price target: internal model + Finnhub analyst consensus
        Final target = 40% internal model + 60% analyst consensus (when available)

CHANGES v3 -> v3.1:
  [NEW] Market Bias Banner — daily SPY directional signal (BUY/NEUTRAL/SELL)
        Composite of momentum, stress, volatility, liquidity, quality, dividend signals.
        Confidence-gated momentum weight skew: BUY + conf>55% boosts tech weights,
        SELL + conf>55% suppresses them (zero-sum with fundamentals).
  [NEW] Relative Valuation Grid in expand panel — EV/EBITDA, EV/Revenue, P/FCF
        vs rate-adjusted sector anchors. Green = cheap vs peers, Red = premium.
  [NEW] Implied multiples computed per ticker in screen_stocks() for display.

Derives macro regime factors from ETF data (SPY, IWM, VTV, VUG, USMV, VYM)
to dynamically weight BOTH fundamental AND technical/momentum scoring criteria.
"""

import polars as pl
import numpy as np
from scipy import stats
from datetime import datetime, timedelta
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# ── USER INPUTS  (edit these before each run) ───────────────
# ============================================================
MARKET_CAP_PRESET = "all"
MARKET_CAP_MIN_CUSTOM = 500e6
MARKET_CAP_MAX_CUSTOM = 35e9

FED_TARGET_RATE   = 0.0425
FED_NEUTRAL_RATE  = 0.0250

# ── END USER INPUTS ─────────────────────────────────────────

SHARADAR_SF1 = Path("/var/sharadar/data/SF1.parquet")
SHARADAR_SEP = Path("/var/sharadar/data/SEP.parquet")
SHARADAR_SFP = Path("/var/sharadar/data/SFP.parquet")
SHARADAR_TICKERS = Path("/var/sharadar/data/TICKERS.parquet")

FACTOR_ETFS = ["SPY", "IWM", "VTV", "VUG", "USMV", "VYM", "TLT", "GLD"]

_CAP_PRESETS = {
    "small":     (500e6,   2e9),
    "mid":       (2e9,    10e9),
    "large":     (10e9,  200e9),
    "smid":      (500e6,  10e9),
    "midlarge":  (2e9,   200e9),
    "all":       (500e6, 200e9),
    "custom":    (MARKET_CAP_MIN_CUSTOM, MARKET_CAP_MAX_CUSTOM),
}
MARKET_CAP_MIN, MARKET_CAP_MAX = _CAP_PRESETS.get(
    MARKET_CAP_PRESET.lower(), _CAP_PRESETS["all"])

MIN_QUARTERS = 6
MAX_RESULTS = 40
MAX_PER_SECTOR = 5
MIN_DEBT_COVERAGE = 0.50

SHORT_WINDOW = 20
LONG_WINDOW = 60
FAST_WINDOW = 5
MED_WINDOW = 10
VOL_WINDOW_FAST = 5
VOL_WINDOW_SLOW = 30
RSI_PERIOD = 14
SMA_SHORT = 20
SMA_LONG = 50
PRICE_LOOKBACK_DAYS = 180

ANALYST_DIVERGENCE_FLAG = 0.40

SECTOR_MULTIPLES = {
    "Technology":             {"ev_ebitda": 18.0, "ev_rev": 4.5,  "fcf_yield": 0.035, "rate_sensitivity": 0.10},
    "Communication Services": {"ev_ebitda": 14.0, "ev_rev": 3.5,  "fcf_yield": 0.040, "rate_sensitivity": 0.09},
    "Consumer Discretionary": {"ev_ebitda": 13.0, "ev_rev": 1.5,  "fcf_yield": 0.040, "rate_sensitivity": 0.07},
    "Consumer Staples":       {"ev_ebitda": 12.0, "ev_rev": 1.2,  "fcf_yield": 0.045, "rate_sensitivity": 0.04},
    "Healthcare":             {"ev_ebitda": 14.0, "ev_rev": 3.0,  "fcf_yield": 0.040, "rate_sensitivity": 0.05},
    "Industrials":            {"ev_ebitda": 11.0, "ev_rev": 1.8,  "fcf_yield": 0.045, "rate_sensitivity": 0.05},
    "Materials":              {"ev_ebitda": 9.0,  "ev_rev": 1.4,  "fcf_yield": 0.050, "rate_sensitivity": 0.04},
    "Real Estate":            {"ev_ebitda": 16.0, "ev_rev": 5.0,  "fcf_yield": 0.055, "rate_sensitivity": 0.12},
    "Energy":                 {"ev_ebitda": 7.0,  "ev_rev": 1.2,  "fcf_yield": 0.060, "rate_sensitivity": 0.02},
    "Utilities":              {"ev_ebitda": 10.0, "ev_rev": 2.5,  "fcf_yield": 0.055, "rate_sensitivity": 0.08},
    "Financials":             {"ev_ebitda": 12.0, "ev_rev": 2.5,  "fcf_yield": 0.050, "rate_sensitivity": 0.02},
    "Basic Materials":        {"ev_ebitda": 9.0,  "ev_rev": 1.4,  "fcf_yield": 0.050, "rate_sensitivity": 0.04},
    "_default":               {"ev_ebitda": 12.0, "ev_rev": 2.0,  "fcf_yield": 0.045, "rate_sensitivity": 0.05},
}

FINNHUB_API_KEY = None

EXCLUDED_SECTORS = {"Healthcare"}
EXCLUDED_INDUSTRIES = {
    "Biotechnology", "Pharmaceuticals", "Drug Manufacturers",
    "Drug Manufacturers - General", "Drug Manufacturers - Specialty & Generic",
    "Biotechnology & Drugs", "Pharmaceutical Retailers",
    "Diagnostics & Research",
}
EXCLUDE_ALL_HEALTHCARE = False
EXCLUDE_BIOTECH_PHARMA = True

SCORING_CRITERIA = [
    "revenue_trend", "ebitda_trend", "fcf_trend", "debt_trend",
    "price_momentum", "rsi_score", "sma_cross_score",
    "upside_score", "sentiment_score",
]
BASELINE_WEIGHT = 1.0 / len(SCORING_CRITERIA)

_RAW_FACTOR_WEIGHT_MAP = {
    "momentum": {
        "revenue_trend":   0.04, "ebitda_trend":    0.06, "fcf_trend":       0.00,
        "debt_trend":     -0.04, "price_momentum":  0.14, "rsi_score":       0.08,
        "sma_cross_score": 0.10, "upside_score":   -0.04,
        "sentiment_score": 0.04,
    },
    "volatility": {
        "revenue_trend":  -0.02, "ebitda_trend":    0.04, "fcf_trend":       0.12,
        "debt_trend":      0.14, "price_momentum": -0.10, "rsi_score":      -0.08,
        "sma_cross_score":-0.04, "upside_score":    0.12,
        "sentiment_score": 0.06,
    },
    "stress": {
        "revenue_trend":   0.00, "ebitda_trend":    0.06, "fcf_trend":       0.16,
        "debt_trend":      0.16, "price_momentum": -0.14, "rsi_score":      -0.10,
        "sma_cross_score":-0.08, "upside_score":    0.14,
        "sentiment_score": 0.06,
    },
    "liquidity": {
        "revenue_trend":   0.04, "ebitda_trend":    0.06, "fcf_trend":       0.04,
        "debt_trend":     -0.02, "price_momentum":  0.06, "rsi_score":       0.04,
        "sma_cross_score": 0.04, "upside_score":    0.08,
        "sentiment_score": 0.08,
    },
    "quality": {
        "revenue_trend":   0.06, "ebitda_trend":    0.08, "fcf_trend":       0.10,
        "debt_trend":      0.08, "price_momentum": -0.08, "rsi_score":      -0.04,
        "sma_cross_score":-0.02, "upside_score":    0.10,
        "sentiment_score": 0.00,
    },
    "dividends": {
        "revenue_trend":   0.00, "ebitda_trend":    0.04, "fcf_trend":       0.10,
        "debt_trend":      0.06, "price_momentum": -0.04, "rsi_score":      -0.02,
        "sma_cross_score": 0.00, "upside_score":    0.08,
        "sentiment_score": 0.02,
    },
}

def _enforce_zero_sum(raw_map):
    result = {}
    for factor, sensitivities in raw_map.items():
        total = sum(sensitivities.values())
        n = len(sensitivities)
        adjustment = total / n
        result[factor] = {k: round(v - adjustment, 6) for k, v in sensitivities.items()}
        check = sum(result[factor].values())
        assert abs(check) < 1e-4, f"Zero-sum failed for {factor}: sum={check}"
    return result

FACTOR_WEIGHT_MAP = _enforce_zero_sum(_RAW_FACTOR_WEIGHT_MAP)


# ============================================================
# SYNTHETIC DATA
# ============================================================
def generate_synthetic_etf_data():
    np.random.seed(42)
    dates = [datetime.now() - timedelta(days=d) for d in range(180, 0, -1)]
    rows = []
    base = {"SPY": 540, "IWM": 210, "VTV": 170, "VUG": 370, "USMV": 82, "VYM": 120,
            "TLT": 96, "GLD": 235}
    drift = {"SPY": 0.0004, "IWM": 0.0001, "VTV": 0.0005, "VUG": 0.0002,
             "USMV": 0.0003, "VYM": 0.0004, "TLT": 0.0001, "GLD": 0.0003}
    vol = {"SPY": 0.012, "IWM": 0.015, "VTV": 0.010, "VUG": 0.014,
           "USMV": 0.008, "VYM": 0.009, "TLT": 0.010, "GLD": 0.009}
    for ticker in FACTOR_ETFS:
        price = base[ticker]
        for d in dates:
            price *= (1 + drift[ticker] + vol[ticker] * np.random.randn())
            rows.append({"ticker": ticker, "date": d, "close": round(price, 2)})
    return pl.DataFrame(rows)


def generate_synthetic_fundamentals():
    np.random.seed(123)
    tickers = ["ACME", "BOLT", "CRUX", "DYNM", "FLUX", "GEAR", "HIVE", "INTL",
               "JOLT", "KORE", "LYNX", "MESA", "NEXS", "ONYX", "PRAX", "QUIK",
               "RIFT", "STRM", "TRVL", "UNIT", "VECT", "WAVR", "XCEL", "YELD", "ZETA",
               "ALFA", "BRVO", "CODA", "DELT", "ECHO"]
    rows = []
    for ticker in tickers:
        base_rev = np.random.uniform(500, 5000) * 1e6
        base_ebitda = base_rev * np.random.uniform(0.1, 0.35)
        base_debt = base_rev * np.random.uniform(0.5, 2.0)
        base_ncfo = base_ebitda * np.random.uniform(0.6, 0.9)
        mktcap = np.random.uniform(1.5, 40) * 1e9
        rev_g = np.random.uniform(-0.02, 0.06)
        ebitda_g = np.random.uniform(-0.03, 0.08)
        debt_g = np.random.uniform(-0.05, 0.03)
        for q in range(8):
            datekey = datetime.now() - timedelta(days=(8 - q) * 90)
            revenue = base_rev * (1 + rev_g) ** q
            ebitda = base_ebitda * (1 + ebitda_g) ** q
            debt = base_debt * (1 + debt_g) ** q
            ncfo = base_ncfo * (1 + ebitda_g * 0.8) ** q
            capex = ncfo * np.random.uniform(0.2, 0.5)
            cashnequsd = base_rev * np.random.uniform(0.05, 0.3) * (1 + np.random.uniform(-0.02, 0.03)) ** q
            rows.append({
                "ticker": ticker, "dimension": "ARQ", "datekey": datekey,
                "revenue": round(revenue), "ebitda": round(ebitda),
                "debt": round(debt), "ncfo": round(ncfo), "capex": round(capex),
                "fcf": round(ncfo - capex), "marketcap": round(mktcap),
                "cashnequsd": round(cashnequsd),
            })
    return pl.DataFrame(rows)


def generate_synthetic_equity_prices(tickers):
    np.random.seed(456)
    rows = []
    dates = [datetime.now() - timedelta(days=d) for d in range(PRICE_LOOKBACK_DAYS, 0, -1)]
    for ticker in tickers:
        price = np.random.uniform(15, 300)
        drift = np.random.uniform(-0.0003, 0.001)
        ticker_vol = np.random.uniform(0.01, 0.025)
        for d in dates:
            price *= (1 + drift + ticker_vol * np.random.randn())
            price = max(price, 1.0)
            rows.append({"ticker": ticker, "date": d, "close": round(price, 2),
                          "volume": int(np.random.uniform(500000, 10000000))})
    return pl.DataFrame(rows)


# ============================================================
# DATA LOADING
# ============================================================
def load_etf_prices():
    if not SHARADAR_SFP.exists():
        print("[INFO] SFP not found - using synthetic ETF data for demo")
        return generate_synthetic_etf_data()
    sfp = pl.read_parquet(SHARADAR_SFP)
    sfp = sfp.rename({c: c.lower() for c in sfp.columns})
    cutoff = datetime.now() - timedelta(days=180)
    return sfp.filter(
        (pl.col("ticker").is_in(FACTOR_ETFS)) & (pl.col("date") >= cutoff)
    ).select(["ticker", "date", "close"]).sort(["ticker", "date"])


def load_ticker_metadata():
    if not SHARADAR_TICKERS.exists():
        print("[INFO] TICKERS not found - using synthetic exclusion lists for demo")
        adr_set = {"INTL", "TRVL", "ECHO"}
        biotech_set = {"PRAX"}
        sector_map = {}
        return adr_set, biotech_set, sector_map

    tickers = pl.read_parquet(SHARADAR_TICKERS)
    tickers = tickers.rename({c: c.lower() for c in tickers.columns})

    adr_filter_cols = []
    if "category" in tickers.columns:
        adr_filter_cols.append(pl.col("category").str.contains("(?i)ADR"))
    if "isforeign" in tickers.columns:
        adr_filter_cols.append(pl.col("isforeign").str.contains("(?i)Y"))
    if adr_filter_cols:
        combined = adr_filter_cols[0]
        for f in adr_filter_cols[1:]:
            combined = combined | f
        adrs = tickers.filter(combined)
    else:
        adrs = pl.DataFrame()
    adr_set = set(adrs["ticker"].to_list()) if adrs.height > 0 and "ticker" in adrs.columns else set()
    print(f"  {len(adr_set)} ADR tickers excluded")

    biotech_set = set()
    has_sector = "sector" in tickers.columns
    has_industry = "industry" in tickers.columns

    if EXCLUDE_BIOTECH_PHARMA and (has_sector or has_industry):
        for row in tickers.iter_rows(named=True):
            t = row.get("ticker")
            if not t:
                continue
            sector = (row.get("sector", "") or "").strip()
            industry = (row.get("industry", "") or "").strip()
            if EXCLUDE_ALL_HEALTHCARE and sector in EXCLUDED_SECTORS:
                biotech_set.add(t)
            elif industry in EXCLUDED_INDUSTRIES:
                biotech_set.add(t)
            elif any(kw in industry.lower() for kw in ["biotech", "pharma", "drug"]):
                biotech_set.add(t)
        print(f"  {len(biotech_set)} biotech/pharma tickers excluded")

    sector_map = {}
    if has_sector or has_industry:
        for row in tickers.iter_rows(named=True):
            t = row.get("ticker")
            if t:
                sector_map[t] = {
                    "sector": row.get("sector", "") or "",
                    "industry": row.get("industry", "") or "",
                }
        print(f"  {len(sector_map)} tickers with sector/industry data")

    return adr_set, biotech_set, sector_map


def load_fundamentals():
    if not SHARADAR_SF1.exists():
        print("[INFO] SF1 not found - using synthetic fundamental data for demo")
        return generate_synthetic_fundamentals()
    sf1 = pl.read_parquet(SHARADAR_SF1)
    sf1 = sf1.rename({c: c.lower() for c in sf1.columns})
    cutoff = datetime.now() - timedelta(days=365 * 3)
    return sf1.filter(
        (pl.col("dimension") == "ARQ") & (pl.col("datekey") >= cutoff)
    ).sort(["ticker", "datekey"])


def load_equity_prices(tickers):
    if not SHARADAR_SEP.exists():
        print("[INFO] SEP not found - using synthetic equity prices for demo")
        return generate_synthetic_equity_prices(tickers)
    sep = pl.read_parquet(SHARADAR_SEP)
    sep = sep.rename({c: c.lower() for c in sep.columns})

    print(f"  SEP columns: {sep.columns}")
    print(f"  SEP total rows: {sep.height}")

    date_col = None
    for candidate in ["date", "datekey", "calendardate"]:
        if candidate in sep.columns:
            date_col = candidate
            break
    if date_col is None:
        print("[WARN] No recognized date column in SEP - cannot compute technicals")
        return generate_synthetic_equity_prices(tickers)
    if date_col != "date":
        sep = sep.rename({date_col: "date"})

    close_col = None
    for candidate in ["closeadj", "close", "closeunadj", "lastsaleprice"]:
        if candidate in sep.columns:
            close_col = candidate
            break
    if close_col is None:
        print("[WARN] No recognized close column in SEP - cannot compute technicals")
        return generate_synthetic_equity_prices(tickers)

    display_close_col = None
    for candidate in ["closeunadj", "close", "closeadj"]:
        if candidate in sep.columns:
            display_close_col = candidate
            break

    vol_col = "volume" if "volume" in sep.columns else None

    cutoff = datetime.now() - timedelta(days=PRICE_LOOKBACK_DAYS)
    filtered = sep.filter(
        (pl.col("ticker").is_in(tickers)) & (pl.col("date") >= cutoff)
    )

    if close_col != "close":
        if "close" in filtered.columns:
            filtered = filtered.drop("close")
        filtered = filtered.rename({close_col: "close"})

    if display_close_col and display_close_col != close_col and display_close_col != "close":
        if display_close_col in filtered.columns:
            if "close_display" in filtered.columns:
                filtered = filtered.drop("close_display")
            filtered = filtered.rename({display_close_col: "close_display"})
    elif "close_display" not in filtered.columns:
        filtered = filtered.with_columns(pl.col("close").alias("close_display"))

    select_cols = ["ticker", "date", "close", "close_display"]
    if vol_col:
        select_cols.append(vol_col)
    select_cols = [c for c in select_cols if c in filtered.columns]

    filtered = filtered.select(select_cols).sort(["ticker", "date"])

    matched_tickers = filtered["ticker"].n_unique()
    print(f"  SEP: {matched_tickers}/{len(tickers)} tickers matched, {filtered.height} price rows")
    print(f"  Technical close: {close_col} | Display price: {display_close_col or close_col}")
    if matched_tickers == 0:
        print("[WARN] No SEP price data matched - check ticker format or date range")

    return filtered


# ============================================================
# FACTOR MODEL (ETF-derived macro signals)
# ============================================================
def compute_factor_signals(etf_prices):
    """v4: Redesigned factor model with dual-timeframe regime detection."""
    factors = {}

    def get_returns(ticker, window):
        df = etf_prices.filter(pl.col("ticker") == ticker).sort("date")
        if df.height < window + 1:
            return None
        closes = df["close"].to_numpy()
        return (closes[-1] / closes[-window] - 1) if closes[-window] != 0 else None

    def get_vol(ticker, window):
        df = etf_prices.filter(pl.col("ticker") == ticker).sort("date")
        if df.height < window + 1:
            return None
        closes = df["close"].to_numpy()
        log_returns = np.diff(np.log(closes[-window:]))
        return np.std(log_returns) * np.sqrt(252)

    def get_max_drawdown(ticker, window):
        df = etf_prices.filter(pl.col("ticker") == ticker).sort("date")
        if df.height < window + 1:
            return 0.0
        closes = df["close"].to_numpy()[-window:]
        peak = np.maximum.accumulate(closes)
        drawdown = (closes - peak) / peak
        return float(np.min(drawdown))

    # 1. MOMENTUM
    spy_fast = get_returns("SPY", FAST_WINDOW)
    spy_med = get_returns("SPY", SHORT_WINDOW)
    spy_slow = get_returns("SPY", LONG_WINDOW)

    if spy_fast is not None and spy_med is not None:
        mom_fast = spy_fast - (spy_med * FAST_WINDOW / SHORT_WINDOW)
        mom_slow = (spy_med - (spy_slow * SHORT_WINDOW / LONG_WINDOW)) if spy_slow is not None else 0
        mom_blended = 0.6 * mom_fast + 0.4 * mom_slow
        mom_z = float(np.clip(mom_blended / 0.015, -3, 3))
        factors["momentum"] = {
            "value": round(mom_blended * 100, 2), "z_score": round(mom_z, 2),
            "signal": "bullish" if mom_z > 0.5 else ("bearish" if mom_z < -0.5 else "neutral"),
            "description": f"SPY fast momentum (5d/20d blend): {mom_blended*100:.2f}%",
            "detail": f"SPY 5d: {spy_fast*100:.1f}% | 20d: {spy_med*100:.1f}% | 60d: {(spy_slow or 0)*100:.1f}%"
        }
    else:
        factors["momentum"] = {"value": 0, "z_score": 0.0, "signal": "neutral",
                                "description": "Insufficient SPY data", "detail": ""}

    # 2. VOLATILITY
    spy_vol_fast = get_vol("SPY", VOL_WINDOW_FAST)
    spy_vol_med = get_vol("SPY", MED_WINDOW)
    spy_vol_slow = get_vol("SPY", VOL_WINDOW_SLOW)

    if spy_vol_fast is not None and spy_vol_slow is not None:
        current_vol = max(spy_vol_fast, spy_vol_med or 0)
        vol_ratio = current_vol / max(spy_vol_slow, 0.01)
        vol_z = float(np.clip((vol_ratio - 1.0) / 0.25, -3, 3))
        factors["volatility"] = {
            "value": round(current_vol * 100, 2), "z_score": round(vol_z, 2),
            "signal": "high_vol" if vol_z > 0.5 else ("low_vol" if vol_z < -0.5 else "neutral"),
            "description": f"Vol spike: {current_vol*100:.1f}% fast vs {spy_vol_slow*100:.1f}% baseline (ratio: {vol_ratio:.2f}x)",
            "detail": f"5d vol: {spy_vol_fast*100:.1f}% | 10d: {(spy_vol_med or 0)*100:.1f}% | 30d: {spy_vol_slow*100:.1f}%"
        }
    else:
        factors["volatility"] = {"value": 0, "z_score": 0.0, "signal": "neutral",
                                  "description": "Insufficient vol data", "detail": ""}

    # 3. STRESS
    spy_dd_5d = get_max_drawdown("SPY", FAST_WINDOW)
    spy_dd_10d = get_max_drawdown("SPY", MED_WINDOW)
    tlt_fast = get_returns("TLT", FAST_WINDOW)
    gld_fast = get_returns("GLD", FAST_WINDOW)
    spy_fast_ret = spy_fast or 0

    haven_flow = 0.0
    haven_detail_parts = []
    if tlt_fast is not None:
        haven_flow += (tlt_fast - spy_fast_ret) * 0.5
        haven_detail_parts.append(f"TLT 5d: {tlt_fast*100:.1f}%")
    if gld_fast is not None:
        haven_flow += (gld_fast - spy_fast_ret) * 0.5
        haven_detail_parts.append(f"GLD 5d: {gld_fast*100:.1f}%")

    dd_component = min(spy_dd_5d, spy_dd_10d) / -0.03
    vol_spike_component = ((spy_vol_fast or 0) / max(spy_vol_slow or 0.12, 0.01) - 1.0) / 0.5
    haven_component = haven_flow / 0.02

    stress_raw = (dd_component + vol_spike_component + haven_component) / 3.0
    stress_z = float(np.clip(stress_raw, -3, 3))

    haven_detail = " | ".join(haven_detail_parts) if haven_detail_parts else "TLT/GLD data unavailable"

    factors["stress"] = {
        "value": round(stress_raw, 4), "z_score": round(stress_z, 2),
        "signal": "crisis" if stress_z > 1.0 else ("elevated" if stress_z > 0.3 else ("calm" if stress_z < -0.3 else "neutral")),
        "description": f"Stress composite: DD={spy_dd_5d*100:.1f}% | Vol ratio={vol_spike_component:.2f} | Haven flow={haven_flow*100:.1f}%",
        "detail": f"SPY 5d DD: {spy_dd_5d*100:.1f}% | 10d DD: {spy_dd_10d*100:.1f}% | {haven_detail}"
    }

    # 4. LIQUIDITY
    iwm_fast = get_returns("IWM", FAST_WINDOW)
    iwm_med = get_returns("IWM", SHORT_WINDOW)

    if iwm_fast is not None and spy_fast is not None:
        liq_fast = iwm_fast - spy_fast_ret
        liq_med = ((iwm_med or 0) - (spy_med or 0))
        liq_blended = 0.6 * liq_fast + 0.4 * liq_med
        liq_z = float(np.clip(liq_blended / 0.015, -3, 3))
        factors["liquidity"] = {
            "value": round(liq_blended * 100, 2), "z_score": round(liq_z, 2),
            "signal": "risk_on" if liq_z > 0.5 else ("risk_off" if liq_z < -0.5 else "neutral"),
            "description": f"Small/Large cap spread (fast blend): {liq_blended*100:.2f}%",
            "detail": f"IWM-SPY 5d: {liq_fast*100:.1f}% | 20d: {liq_med*100:.1f}%"
        }
    else:
        factors["liquidity"] = {"value": 0, "z_score": 0.0, "signal": "neutral",
                                 "description": "Insufficient IWM/SPY data", "detail": ""}

    # 5. QUALITY
    usmv_fast = get_returns("USMV", FAST_WINDOW)
    usmv_med = get_returns("USMV", SHORT_WINDOW)

    if usmv_fast is not None and spy_fast is not None:
        qual_fast = usmv_fast - spy_fast_ret
        qual_med = (usmv_med or 0) - (spy_med or 0)
        qual_blended = 0.6 * qual_fast + 0.4 * qual_med
        qual_z = float(np.clip(qual_blended / 0.01, -3, 3))
        factors["quality"] = {
            "value": round(qual_blended * 100, 2), "z_score": round(qual_z, 2),
            "signal": "flight_to_quality" if qual_z > 0.5 else ("risk_appetite" if qual_z < -0.5 else "neutral"),
            "description": f"Low-vol/quality vs broad market: {qual_blended*100:.2f}%",
            "detail": f"USMV-SPY 5d: {qual_fast*100:.1f}% | 20d: {qual_med*100:.1f}%"
        }
    else:
        factors["quality"] = {"value": 0, "z_score": 0.0, "signal": "neutral",
                               "description": "Insufficient USMV/SPY data", "detail": ""}

    # 6. YIELD DEMAND
    vym_fast = get_returns("VYM", FAST_WINDOW)
    vym_med = get_returns("VYM", SHORT_WINDOW)

    if vym_fast is not None and spy_fast is not None:
        div_fast = vym_fast - spy_fast_ret
        div_med = (vym_med or 0) - (spy_med or 0)
        div_blended = 0.6 * div_fast + 0.4 * div_med
        div_z = float(np.clip(div_blended / 0.01, -3, 3))
        factors["dividends"] = {
            "value": round(div_blended * 100, 2), "z_score": round(div_z, 2),
            "signal": "yield_seeking" if div_z > 0.5 else ("yield_averse" if div_z < -0.5 else "neutral"),
            "description": f"Dividend demand (VYM-SPY blend): {div_blended*100:.2f}%",
            "detail": f"VYM-SPY 5d: {div_fast*100:.1f}% | 20d: {div_med*100:.1f}%"
        }
    else:
        factors["dividends"] = {"value": 0, "z_score": 0.0, "signal": "neutral",
                                 "description": "Insufficient VYM/SPY data", "detail": ""}

    return factors


# ============================================================
# MARKET BIAS — Daily SPY directional signal  [NEW v3.1]
# ============================================================
def compute_market_bias(factors):
    """
    Synthesize a daily market direction signal from the 6 factor signals.
    Returns: label (BUY/NEUTRAL/SELL), confidence (0-1), score, components.

    Designed to be used for:
      1. Display in the Market Bias Banner at top of HTML report
      2. Confidence-gated momentum weight skew in compute_dynamic_weights()
         — BUY + conf>55%  → boost price_momentum, rsi_score, sma_cross_score
         — SELL + conf>55% → suppress them (zero-sum offset to fundamentals)
    """
    component_weights = {
        "momentum":   0.35,
        "stress":     0.25,
        "volatility": 0.15,
        "liquidity":  0.15,
        "quality":    0.05,
        "dividends":  0.05,
    }

    signal_score_map = {
        "bullish":            +1.0,
        "bearish":            -1.0,
        "crisis":             -3.0,   # hard override — crisis always sells
        "elevated":           -1.5,
        "calm":               +0.5,
        "high_vol":           -0.8,
        "low_vol":            +0.4,
        "risk_on":            +0.8,
        "risk_off":           -0.8,
        "flight_to_quality":  -0.5,
        "risk_appetite":      +0.5,
        "yield_seeking":      -0.3,
        "yield_averse":       +0.2,
        "neutral":             0.0,
    }

    composite = 0.0
    components = {}
    for factor_name, weight in component_weights.items():
        fdata = factors.get(factor_name, {})
        sig = fdata.get("signal", "neutral")
        z = fdata.get("z_score", 0.0)
        sig_score = signal_score_map.get(sig, 0.0)
        contribution = float(np.clip(sig_score * min(abs(z), 2.0) / 2.0, -1.0, 1.0))
        composite += contribution * weight
        components[factor_name] = {
            "signal": sig, "z": round(z, 2), "score": round(contribution, 3),
            "weight": weight, "contribution": round(contribution * weight, 4),
        }

    composite = float(np.clip(composite, -1.0, 1.0))

    scores = [v["score"] for v in components.values()]
    signs = [np.sign(s) for s in scores if abs(s) > 0.05]
    if signs:
        dominant_sign = np.sign(composite)
        agreement = sum(1 for s in signs if s == dominant_sign) / len(signs)
    else:
        agreement = 0.5
    confidence = round(float(abs(composite) * 0.6 + agreement * 0.4), 3)

    if composite > 0.20:
        label = "BUY"
    elif composite < -0.20:
        label = "SELL"
    else:
        label = "NEUTRAL"

    # Crisis override
    if factors.get("stress", {}).get("signal") == "crisis":
        label = "SELL"
        confidence = max(confidence, 0.85)

    return {
        "label": label,
        "score": round(composite, 4),
        "confidence": confidence,
        "components": components,
        "description": f"SPY directional bias: {label} | score {composite:+.3f} | confidence {confidence:.0%}",
    }


def compute_dynamic_weights(factors):
    weights = {c: BASELINE_WEIGHT for c in SCORING_CRITERIA}

    for factor_name, factor_data in factors.items():
        z = factor_data["z_score"]
        if factor_name not in FACTOR_WEIGHT_MAP:
            continue
        for criterion, sensitivity in FACTOR_WEIGHT_MAP[factor_name].items():
            weights[criterion] += sensitivity * z

    # Fed Rate Overlay
    rate_spread = FED_TARGET_RATE - FED_NEUTRAL_RATE
    rate_z = float(np.clip(rate_spread / 0.01, -3, 3))

    _FED_SENSITIVITY = {
        "revenue_trend":    0.00,
        "ebitda_trend":     0.02,
        "fcf_trend":        0.04,
        "debt_trend":       0.02,
        "price_momentum":  -0.04,
        "rsi_score":       -0.02,
        "sma_cross_score": -0.02,
        "upside_score":     0.02,
        "sentiment_score": -0.02,
    }
    assert abs(sum(_FED_SENSITIVITY.values())) < 1e-9, "Fed sensitivity must be zero-sum"

    for criterion, sensitivity in _FED_SENSITIVITY.items():
        weights[criterion] += sensitivity * rate_z

    weights = {k: max(v, 0.03) for k, v in weights.items()}

    upside_cap = BASELINE_WEIGHT * 0.60
    if weights.get("upside_score", 0) > upside_cap:
        excess = weights["upside_score"] - upside_cap
        weights["upside_score"] = upside_cap
        fund_criteria = ["revenue_trend", "ebitda_trend", "fcf_trend", "debt_trend"]
        for fc in fund_criteria:
            weights[fc] = weights.get(fc, 0) + excess / len(fund_criteria)

    # ── Market Bias Momentum Skew  [NEW v3.1] ────────────────
    # When market bias is BUY with confidence > 55%, boost momentum/technical weights.
    # When SELL with confidence > 55%, suppress them.
    # Max skew = ±0.03 at confidence=1.0, score=±1.0. Zero-sum vs fundamentals.
    if hasattr(compute_dynamic_weights, "_market_bias"):
        mb = compute_dynamic_weights._market_bias
        mb_score = mb.get("score", 0.0)
        mb_conf  = mb.get("confidence", 0.0)
        if mb_conf > 0.55:
            skew = mb_score * mb_conf * 0.03
            tech_criteria = ["price_momentum", "rsi_score", "sma_cross_score"]
            fund_criteria_skew = ["revenue_trend", "ebitda_trend", "fcf_trend", "debt_trend"]
            for tc in tech_criteria:
                weights[tc] = weights.get(tc, 0) + skew
            for fc in fund_criteria_skew:
                weights[fc] = weights.get(fc, 0) - skew / len(fund_criteria_skew)

    total = sum(weights.values())
    weights = {k: round(v / total, 4) for k, v in weights.items()}
    return weights


# ============================================================
# TECHNICAL INDICATORS
# ============================================================
def compute_rsi_wilder(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    alpha = 1.0 / period
    for i in range(period, len(deltas)):
        avg_gain = avg_gain * (1 - alpha) + gains[i] * alpha
        avg_loss = avg_loss * (1 - alpha) + losses[i] * alpha
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_to_score(rsi):
    if rsi >= 80:
        return -0.5
    elif rsi >= 65:
        return 1.0
    elif rsi >= 50:
        return 0.6
    elif rsi >= 30:
        return -0.3
    else:
        return 0.2


def compute_sma_cross(closes, short=20, long=50):
    if len(closes) < long:
        return 0.0
    sma_s = np.mean(closes[-short:])
    sma_l = np.mean(closes[-long:])
    if sma_l == 0:
        return 0.0
    spread = (sma_s - sma_l) / sma_l
    return float(np.clip(spread / 0.05, -1.0, 1.0))


def compute_price_momentum(closes, window=60):
    if len(closes) < window or closes[-window] == 0:
        return 0.0
    return (closes[-1] / closes[-window]) - 1.0


def compute_technicals_for_ticker(ticker, equity_prices):
    tk = equity_prices.filter(pl.col("ticker") == ticker).sort("date")
    if tk.height < SMA_LONG:
        return {"price_momentum": 0.0, "rsi_score": 0.0, "sma_cross_score": 0.0,
                "_rsi_raw": None, "_sma20": None, "_sma50": None, "_last_price": None,
                "_has_price_data": False}
    closes = tk["close"].to_numpy().astype(float)
    if "close_display" in tk.columns:
        last_price = round(float(tk["close_display"].to_numpy()[-1]), 2)
    else:
        last_price = round(float(closes[-1]), 2)
    rsi = compute_rsi_wilder(closes, RSI_PERIOD)
    return {
        "price_momentum": round(compute_price_momentum(closes, LONG_WINDOW), 4),
        "rsi_score": round(rsi_to_score(rsi), 4),
        "sma_cross_score": round(compute_sma_cross(closes, SMA_SHORT, SMA_LONG), 4),
        "_rsi_raw": round(rsi, 1),
        "_sma20": round(float(np.mean(closes[-SMA_SHORT:])), 2),
        "_sma50": round(float(np.mean(closes[-SMA_LONG:])), 2),
        "_last_price": last_price,
        "_has_price_data": True,
    }


# ============================================================
# TARGET PRICE ENGINE v3
# ============================================================
PROJECTION_QUARTERS = 4
DEFAULT_EV_EBITDA_MULTIPLE = 12.0

def _get_sector_multiples(sector):
    sm = SECTOR_MULTIPLES.get(sector, SECTOR_MULTIPLES["_default"])
    rate_spread = FED_TARGET_RATE - FED_NEUTRAL_RATE
    sens = sm["rate_sensitivity"]
    compression = 1.0 - sens * (rate_spread / 0.01)
    compression = max(compression, 0.5)
    compression = min(compression, 1.5)
    return {
        "ev_ebitda": sm["ev_ebitda"] * compression,
        "ev_rev":    sm["ev_rev"]    * compression,
        "fcf_yield": sm["fcf_yield"] / compression,
        "raw": sm,
        "compression": round(compression, 4),
        "rate_spread_bps": round(rate_spread * 10000, 1),
    }


def _theil_sen_project(series, n_forward):
    clean = np.array([v for v in series
                      if v is not None and not np.isnan(float(v))], dtype=float)
    if len(clean) < 3:
        return None, 0.0, 0.0

    n = len(clean)
    x = np.arange(n, dtype=float)

    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            if x[j] != x[i]:
                slopes.append((clean[j] - clean[i]) / (x[j] - x[i]))

    if not slopes:
        return None, 0.0, 0.0

    ts_slope = float(np.median(slopes))
    ts_intercept = float(np.median(clean - ts_slope * x))

    y_hat = ts_slope * x + ts_intercept
    ss_res = np.sum((clean - y_hat) ** 2)
    ss_tot = np.sum((clean - np.mean(clean)) ** 2)
    r2 = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    directions = np.sign(np.diff(clean))
    slope_dir = np.sign(ts_slope)
    consistency = float(np.mean(directions == slope_dir)) if len(directions) > 0 else 0.5

    projected = [ts_slope * (n + i) + ts_intercept for i in range(n_forward)]
    return projected, round(r2, 4), round(consistency, 4)


def _rolling_median_smooth(series, window=3):
    clean = [float(v) for v in series if v is not None and not np.isnan(float(v))]
    if len(clean) < window:
        return clean
    smoothed = []
    for i in range(len(clean)):
        lo = max(0, i - window + 1)
        smoothed.append(float(np.median(clean[lo:i+1])))
    return smoothed


def _model_conviction(r2, data_points, cv, data_available):
    if not data_available or data_points < 3:
        return 0.0
    r2_score     = r2
    data_score   = min(data_points / 10.0, 1.0)
    stability    = max(0.0, 1.0 - min(cv, 2.0) / 2.0)
    conviction   = 0.50 * r2_score + 0.30 * stability + 0.20 * data_score
    return round(float(np.clip(conviction, 0.0, 1.0)), 4)


def compute_target_price_and_upside(ebitda_series, debt_series, fcf_series,
                                    marketcap, last_price, cash_on_hand=0.0,
                                    shares_diluted=None, revenue_series=None,
                                    sector=None):
    if last_price is None or last_price <= 0 or marketcap is None or marketcap <= 0:
        return None, 0.0, 0.0, {}

    share_count = marketcap / last_price
    if shares_diluted:
        sc = [float(v) for v in shares_diluted
              if v is not None and not np.isnan(float(v)) and v > 0]
        if sc:
            share_count = sc[-1]

    sm = _get_sector_multiples(sector or "_default")
    latest_debt = float(next((v for v in reversed(debt_series or [])
                              if v is not None and not np.isnan(float(v))), 0.0))
    current_ev = marketcap + latest_debt - cash_on_hand

    model_pts   = {}
    convictions = {}
    detail      = {}

    # MODEL 1: EV/EBITDA
    ebitda_smoothed = _rolling_median_smooth(ebitda_series, window=3)
    ebitda_proj, ebitda_r2, ebitda_consist = _theil_sen_project(ebitda_smoothed, PROJECTION_QUARTERS)
    ebitda_clean = [v for v in ebitda_smoothed if v is not None]

    if ebitda_proj and sum(ebitda_proj) > 0:
        proj_annual_ebitda = sum(ebitda_proj)
        trailing_mult = (current_ev / (ebitda_clean[-1] * 4)) if ebitda_clean and ebitda_clean[-1] > 0 else None
        if trailing_mult:
            trailing_mult = float(np.clip(trailing_mult, 4.0, 40.0))
        sector_mult = sm["ev_ebitda"]
        if trailing_mult and 4.0 <= trailing_mult <= 40.0:
            blended_mult = 0.60 * sector_mult + 0.40 * trailing_mult
        else:
            blended_mult = sector_mult
        blended_mult = float(np.clip(blended_mult, 4.0, 40.0))

        target_ev_1 = proj_annual_ebitda * blended_mult
        debt_proj, _, _ = _theil_sen_project(debt_series, PROJECTION_QUARTERS)
        proj_debt = max(debt_proj[-1] if debt_proj else latest_debt, 0.0)
        target_eq_1 = target_ev_1 - proj_debt + cash_on_hand
        if target_eq_1 > 0:
            pt_1 = target_eq_1 / share_count
            ebitda_cv = float(np.std(ebitda_clean) / abs(np.mean(ebitda_clean))) if np.mean(ebitda_clean) != 0 else 1.0
            conv_1 = _model_conviction(ebitda_r2, len(ebitda_clean), ebitda_cv, True)
            model_pts["ev_ebitda"] = pt_1
            convictions["ev_ebitda"] = conv_1
            detail["ev_ebitda"] = {
                "proj_annual": round(proj_annual_ebitda / 1e6, 1),
                "sector_mult": round(sector_mult, 1),
                "trailing_mult": round(trailing_mult, 1) if trailing_mult else None,
                "blended_mult": round(blended_mult, 1),
                "r2": ebitda_r2,
                "consistency": ebitda_consist,
                "conviction": conv_1,
                "pt": round(pt_1, 2),
            }

    # MODEL 2: EV/Revenue
    if revenue_series:
        rev_smoothed = _rolling_median_smooth(revenue_series, window=3)
        rev_proj, rev_r2, rev_consist = _theil_sen_project(rev_smoothed, PROJECTION_QUARTERS)
        rev_clean = [v for v in rev_smoothed if v is not None]

        if rev_proj and sum(rev_proj) > 0:
            proj_annual_rev = sum(rev_proj)
            trailing_rev_mult = (current_ev / (rev_clean[-1] * 4)) if rev_clean and rev_clean[-1] > 0 else None
            if trailing_rev_mult:
                trailing_rev_mult = float(np.clip(trailing_rev_mult, 0.2, 20.0))
            sector_rev_mult = sm["ev_rev"]
            if trailing_rev_mult and 0.2 <= trailing_rev_mult <= 20.0:
                blended_rev_mult = 0.60 * sector_rev_mult + 0.40 * trailing_rev_mult
            else:
                blended_rev_mult = sector_rev_mult
            blended_rev_mult = float(np.clip(blended_rev_mult, 0.2, 20.0))

            target_ev_2 = proj_annual_rev * blended_rev_mult
            debt_proj2, _, _ = _theil_sen_project(debt_series, PROJECTION_QUARTERS)
            proj_debt2 = max(debt_proj2[-1] if debt_proj2 else latest_debt, 0.0)
            target_eq_2 = target_ev_2 - proj_debt2 + cash_on_hand
            if target_eq_2 > 0:
                pt_2 = target_eq_2 / share_count
                rev_cv = float(np.std(rev_clean) / abs(np.mean(rev_clean))) if np.mean(rev_clean) != 0 else 1.0
                conv_2 = _model_conviction(rev_r2, len(rev_clean), rev_cv, True)
                model_pts["ev_rev"] = pt_2
                convictions["ev_rev"] = conv_2
                detail["ev_rev"] = {
                    "proj_annual": round(proj_annual_rev / 1e6, 1),
                    "sector_mult": round(sector_rev_mult, 1),
                    "trailing_mult": round(trailing_rev_mult, 1) if trailing_rev_mult else None,
                    "blended_mult": round(blended_rev_mult, 1),
                    "r2": rev_r2,
                    "consistency": rev_consist,
                    "conviction": conv_2,
                    "pt": round(pt_2, 2),
                }

    # MODEL 3: FCF Yield
    fcf_smoothed = _rolling_median_smooth(fcf_series, window=3)
    fcf_proj, fcf_r2, fcf_consist = _theil_sen_project(fcf_smoothed, PROJECTION_QUARTERS)
    fcf_clean = [v for v in fcf_smoothed if v is not None]
    fcf_positive_quarters = sum(1 for v in fcf_clean if v is not None and v > 0)

    if fcf_proj and fcf_positive_quarters >= 3:
        proj_annual_fcf = sum(fcf_proj)
        if proj_annual_fcf > 0:
            required_yield = sm["fcf_yield"]
            pt_3 = (proj_annual_fcf / required_yield) / share_count
            fcf_cv = float(np.std(fcf_clean) / abs(np.mean(fcf_clean))) if np.mean(fcf_clean) != 0 else 1.0
            fcf_quality = fcf_positive_quarters / max(len(fcf_clean), 1)
            conv_3 = _model_conviction(fcf_r2, len(fcf_clean), fcf_cv, True) * fcf_quality
            model_pts["fcf_yield"] = pt_3
            convictions["fcf_yield"] = conv_3
            detail["fcf_yield"] = {
                "proj_annual_fcf": round(proj_annual_fcf / 1e6, 1),
                "required_yield": round(required_yield * 100, 2),
                "sector_anchor_yield": round(sm["raw"]["fcf_yield"] * 100, 2),
                "r2": fcf_r2,
                "consistency": fcf_consist,
                "positive_qtrs": fcf_positive_quarters,
                "conviction": round(conv_3, 4),
                "pt": round(pt_3, 2),
            }

    if not model_pts:
        return None, 0.0, 0.0, {}

    total_conviction = sum(convictions.values())
    if total_conviction <= 0:
        weights_norm = {k: 1.0 / len(model_pts) for k in model_pts}
    else:
        weights_norm = {k: v / total_conviction for k, v in convictions.items()}

    blended_pt = sum(model_pts[m] * weights_norm[m] for m in model_pts)
    blended_pt = round(blended_pt, 2)

    upside_pct = (blended_pt / last_price) - 1.0
    if upside_pct > 0:
        upside_score = float(np.clip(np.sqrt(upside_pct) * 0.7, 0, 2.0))
    else:
        upside_score = float(np.clip(upside_pct, -1.0, 0.0))

    pt_detail = {
        "models": detail,
        "conviction_weights": {k: round(v, 3) for k, v in weights_norm.items()},
        "blended_pt": blended_pt,
        "sector": sector or "Unknown",
        "sector_anchor": {
            "ev_ebitda": round(sm["ev_ebitda"], 1),
            "ev_rev": round(sm["ev_rev"], 1),
            "fcf_yield_pct": round(sm["fcf_yield"] * 100, 2),
        },
        "rate_compression": sm["compression"],
        "rate_spread_bps": sm["rate_spread_bps"],
        "fed_rate": FED_TARGET_RATE,
        "dominant_model": max(weights_norm, key=weights_norm.get) if weights_norm else "N/A",
    }

    return blended_pt, round(upside_pct, 4), round(upside_score, 4), pt_detail


# ============================================================
# ANALYST SENTIMENT + PRICE TARGETS (Finnhub)
# ============================================================
def get_finnhub_api_key():
    import os
    key = FINNHUB_API_KEY or os.environ.get("FINNHUB_API_KEY", "d6ivnd1r01qleu95pan0d6ivnd1r01qleu95pang")
    return key.strip() if key else ""


def fetch_analyst_sentiment(tickers):
    import requests
    import time

    api_key = get_finnhub_api_key()
    if not api_key:
        print("  [WARN] No Finnhub API key - sentiment scores will be 0 (neutral)")
        return {t: _empty_sentiment() for t in tickers}

    results = {}
    base_url = "https://finnhub.io/api/v1/stock/recommendation"
    success = 0
    failed = 0

    for i, ticker in enumerate(tickers):
        try:
            resp = requests.get(base_url, params={"symbol": ticker, "token": api_key}, timeout=5)
            if resp.status_code == 429:
                time.sleep(1.5)
                resp = requests.get(base_url, params={"symbol": ticker, "token": api_key}, timeout=5)

            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    latest = data[0]
                    sb = latest.get("strongBuy", 0)
                    b = latest.get("buy", 0)
                    h = latest.get("hold", 0)
                    s = latest.get("sell", 0)
                    ss = latest.get("strongSell", 0)
                    total = sb + b + h + s + ss

                    if total > 0:
                        weighted = (sb * 1.0 + b * 0.5 + h * 0.0 + s * (-0.5) + ss * (-1.0)) / total
                        results[ticker] = {
                            "sentiment_score": round(weighted, 4),
                            "strongBuy": sb, "buy": b, "hold": h, "sell": s, "strongSell": ss,
                            "total_analysts": total,
                            "period": latest.get("period", ""),
                        }
                        success += 1
                    else:
                        results[ticker] = _empty_sentiment()
                else:
                    results[ticker] = _empty_sentiment()
            else:
                results[ticker] = _empty_sentiment()
                failed += 1
        except Exception:
            results[ticker] = _empty_sentiment()
            failed += 1

        if (i + 1) % 55 == 0:
            time.sleep(5)
        elif i < len(tickers) - 1:
            time.sleep(0.3)

    print(f"  Finnhub recommendations: {success}/{len(tickers)} tickers" +
          (f", {failed} failed" if failed else ""))
    return results


def fetch_analyst_price_targets(tickers):
    import requests
    import time

    api_key = get_finnhub_api_key()
    if not api_key:
        print("  [WARN] No Finnhub API key - analyst price targets unavailable")
        return {}

    results = {}
    base_url = "https://finnhub.io/api/v1/stock/price-target"
    success = 0

    for i, ticker in enumerate(tickers):
        try:
            resp = requests.get(base_url, params={"symbol": ticker, "token": api_key}, timeout=5)
            if resp.status_code == 429:
                time.sleep(1.5)
                resp = requests.get(base_url, params={"symbol": ticker, "token": api_key}, timeout=5)

            if resp.status_code == 200:
                data = resp.json()
                if data and data.get("targetMean"):
                    results[ticker] = {
                        "target_high": data.get("targetHigh"),
                        "target_low": data.get("targetLow"),
                        "target_mean": data.get("targetMean"),
                        "target_median": data.get("targetMedian"),
                        "last_updated": data.get("lastUpdated", ""),
                    }
                    success += 1
        except Exception:
            pass

        if (i + 1) % 55 == 0:
            time.sleep(5)
        elif i < len(tickers) - 1:
            time.sleep(0.3)

    print(f"  Finnhub price targets: {success}/{len(tickers)} tickers with data")
    return results


def compute_blended_target(internal_target, pt_detail, analyst_targets, last_price):
    if internal_target is None or internal_target <= 0:
        analyst_mean = analyst_targets.get("target_mean") if analyst_targets else None
        if analyst_mean and analyst_mean > 0 and last_price and last_price > 0:
            upside = (analyst_mean / last_price) - 1.0
            return analyst_mean, round(upside, 4), round(float(np.clip(upside, -1.0, 2.0)), 4), False, "A"
        return None, 0.0, 0.0, False, "N/A"

    blended = internal_target
    if last_price and last_price > 0:
        upside_pct = (blended / last_price) - 1.0
        upside_score = float(np.clip(upside_pct, -1.0, 2.0))
    else:
        upside_pct = 0.0
        upside_score = 0.0

    analyst_mean = analyst_targets.get("target_mean") if analyst_targets else None
    divergence_flagged = False
    if analyst_mean and analyst_mean > 0 and last_price and last_price > 0:
        divergence = abs(blended - analyst_mean) / last_price
        divergence_flagged = divergence > ANALYST_DIVERGENCE_FLAG

    source = "M"
    if analyst_mean:
        source = "M*" if divergence_flagged else "M✓"

    return round(blended, 2), round(upside_pct, 4), round(upside_score, 4), divergence_flagged, source


def _empty_sentiment():
    return {"sentiment_score": 0.0, "strongBuy": 0, "buy": 0, "hold": 0,
            "sell": 0, "strongSell": 0, "total_analysts": 0, "period": ""}


# ============================================================
# FUNDAMENTAL SCREENING
# ============================================================
def compute_trend_score(values):
    clean = [float(v) for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if len(clean) < 3:
        return 0.0
    x = np.arange(len(clean))
    y = np.array(clean)
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    mean_abs = np.mean(np.abs(y))
    if mean_abs == 0:
        return 0.0
    return (slope / mean_abs) * r_value ** 2


def compute_industry_medians(sf1, sector_map):
    from collections import defaultdict
    sector_rev_growths   = defaultdict(list)
    sector_ebitda_margins = defaultdict(list)

    all_tickers = sf1["ticker"].unique().to_list()
    for ticker in all_tickers:
        sector = (sector_map or {}).get(ticker, {}).get("sector", "_default") or "_default"
        tk = sf1.filter(pl.col("ticker") == ticker).sort("datekey")
        if tk.height < 2:
            continue

        revenue = [v for v in (tk["revenue"].to_list() if "revenue" in tk.columns else [])
                   if v is not None and not (isinstance(v, float) and np.isnan(v))]
        if len(revenue) >= 2 and revenue[-2] != 0:
            qoq = (revenue[-1] - revenue[-2]) / abs(revenue[-2])
            sector_rev_growths[sector].append(qoq)

        ebitda = [v for v in (tk["ebitda"].to_list() if "ebitda" in tk.columns else [])
                  if v is not None and not (isinstance(v, float) and np.isnan(v))]
        if revenue and ebitda and revenue[-1] and revenue[-1] > 0:
            margin = ebitda[-1] / revenue[-1]
            sector_ebitda_margins[sector].append(margin)

    medians = {}
    all_sectors = set(list(sector_rev_growths.keys()) + list(sector_ebitda_margins.keys()))
    for sector in all_sectors:
        rg = sorted(sector_rev_growths[sector])
        em = sorted(sector_ebitda_margins[sector])
        medians[sector] = {
            "rev_growth_qoq": float(np.median(rg)) if rg else 0.0,
            "ebitda_margin":  float(np.median(em)) if em else 0.0,
            "n":              max(len(rg), len(em)),
        }

    all_rg = [v for lst in sector_rev_growths.values() for v in lst]
    all_em = [v for lst in sector_ebitda_margins.values() for v in lst]
    medians["_default"] = {
        "rev_growth_qoq": float(np.median(all_rg)) if all_rg else 0.0,
        "ebitda_margin":  float(np.median(all_em)) if all_em else 0.0,
        "n":              len(all_rg),
    }
    return medians


def screen_stocks(sf1, equity_prices, adr_tickers=None, biotech_tickers=None,
                  sentiment_data=None, sector_map=None, industry_medians=None):
    latest = sf1.group_by("ticker").agg(
        pl.col("datekey").max().alias("latest_date"),
        pl.col("marketcap").last().alias("marketcap"),
    ).filter(
        (pl.col("marketcap") >= MARKET_CAP_MIN) & (pl.col("marketcap") <= MARKET_CAP_MAX)
    )
    eligible_tickers = latest["ticker"].to_list()

    if adr_tickers:
        before = len(eligible_tickers)
        eligible_tickers = [t for t in eligible_tickers if t not in adr_tickers]
        excluded = before - len(eligible_tickers)
        if excluded > 0:
            print(f"  {excluded} ADRs excluded from screen")

    if biotech_tickers:
        before = len(eligible_tickers)
        eligible_tickers = [t for t in eligible_tickers if t not in biotech_tickers]
        excluded = before - len(eligible_tickers)
        if excluded > 0:
            print(f"  {excluded} biotech/pharma tickers excluded from screen")

    results = []
    no_price_count = 0

    for ticker in eligible_tickers:
        tk = sf1.filter(pl.col("ticker") == ticker).sort("datekey")
        if tk.height < MIN_QUARTERS:
            continue

        revenue = tk["revenue"].to_list() if "revenue" in tk.columns else []
        ebitda = tk["ebitda"].to_list() if "ebitda" in tk.columns else []
        debt = tk["debt"].to_list() if "debt" in tk.columns else []

        if "fcf" in tk.columns:
            fcf = tk["fcf"].to_list()
        elif "ncfo" in tk.columns and "capex" in tk.columns:
            ncfo_arr = tk["ncfo"].to_numpy()
            capex_arr = tk["capex"].to_numpy()
            fcf = (ncfo_arr - capex_arr).tolist()
        else:
            fcf = []

        latest_debt_val = debt[-1] if debt else None
        latest_fcf_val = fcf[-1] if fcf else None

        cash_on_hand = 0.0
        for cash_col in ["cashnequsd", "cashneq", "cash"]:
            if cash_col in tk.columns:
                cash_vals = tk[cash_col].to_list()
                last_cash = cash_vals[-1] if cash_vals else None
                if last_cash is not None and not (isinstance(last_cash, float) and np.isnan(last_cash)):
                    cash_on_hand = float(last_cash)
                break

        if latest_debt_val is not None and not (isinstance(latest_debt_val, float) and np.isnan(latest_debt_val)):
            net_debt = float(latest_debt_val) - cash_on_hand
            if net_debt > 0:
                annual_fcf = 0.0
                if latest_fcf_val is not None and not (isinstance(latest_fcf_val, float) and np.isnan(latest_fcf_val)):
                    annual_fcf = float(latest_fcf_val) * 4
                total_coverage = cash_on_hand + max(annual_fcf, 0)
                coverage_ratio = total_coverage / net_debt if net_debt > 0 else float('inf')
                if coverage_ratio < MIN_DEBT_COVERAGE:
                    continue

        if len(revenue) >= 2:
            r_curr = revenue[-1]
            r_prev = revenue[-2]
            if (r_curr is not None and r_prev is not None
                    and not (isinstance(r_curr, float) and np.isnan(r_curr))
                    and not (isinstance(r_prev, float) and np.isnan(r_prev))
                    and r_prev != 0):
                rev_qoq = (r_curr - r_prev) / abs(r_prev)
                if rev_qoq < -0.20:
                    continue

        if industry_medians and len(revenue) >= 2:
            sector_key = (sector_map or {}).get(ticker, {}).get("sector", "_default") or "_default"
            med = industry_medians.get(sector_key, industry_medians.get("_default", {}))

            beats = 0

            r_curr2 = revenue[-1]; r_prev2 = revenue[-2]
            if (r_curr2 is not None and r_prev2 is not None
                    and not (isinstance(r_curr2, float) and np.isnan(r_curr2))
                    and not (isinstance(r_prev2, float) and np.isnan(r_prev2))
                    and r_prev2 != 0):
                ticker_qoq = (r_curr2 - r_prev2) / abs(r_prev2)
                if ticker_qoq > med.get("rev_growth_qoq", 0.0):
                    beats += 1

            ebitda_latest = ebitda[-1] if ebitda else None
            rev_latest    = revenue[-1] if revenue else None
            if (ebitda_latest is not None and rev_latest and rev_latest > 0
                    and not (isinstance(ebitda_latest, float) and np.isnan(ebitda_latest))):
                ticker_margin = ebitda_latest / rev_latest
                if ticker_margin > med.get("ebitda_margin", 0.0):
                    beats += 1

            if len(revenue) >= 4:
                clean_rev = [v for v in revenue if v is not None and not (isinstance(v, float) and np.isnan(v))]
                if len(clean_rev) >= 3:
                    if clean_rev[-1] > clean_rev[-3]:
                        beats += 1

            if beats < 2:
                continue

        rev_trend = compute_trend_score(revenue)
        ebitda_trend = compute_trend_score(ebitda)
        fcf_trend = compute_trend_score(fcf)
        debt_trend = -compute_trend_score(debt)

        techs = compute_technicals_for_ticker(ticker, equity_prices)
        if not techs.get("_has_price_data"):
            no_price_count += 1

        mktcap = latest.filter(pl.col("ticker") == ticker)["marketcap"].to_list()[0]

        shares_diluted = None
        for shares_col in ["shareswadil", "shareswa", "sharesbas"]:
            if shares_col in tk.columns:
                shares_diluted = tk[shares_col].to_list()
                break

        ticker_sector = (sector_map or {}).get(ticker, {}).get("sector", "_default")

        if techs.get("_last_price") and techs["_last_price"] > 0:
            internal_tp, _, _, internal_pt_detail = compute_target_price_and_upside(
                ebitda, debt, fcf, mktcap, techs["_last_price"],
                cash_on_hand=cash_on_hand,
                shares_diluted=shares_diluted,
                revenue_series=revenue,
                sector=ticker_sector)
        else:
            internal_tp = None
            internal_pt_detail = {}

        import json as _json_screen

        # Industry outperformance deltas
        ind_rev_qoq      = None
        ind_ebitda_margin = None
        ind_rev_qoq_delta = None
        ind_margin_delta  = None
        if industry_medians:
            sector_key2 = (sector_map or {}).get(ticker, {}).get("sector", "_default") or "_default"
            med2 = industry_medians.get(sector_key2, industry_medians.get("_default", {}))
            if len(revenue) >= 2 and revenue[-2] and revenue[-2] != 0:
                r_c = revenue[-1]; r_p = revenue[-2]
                if (r_c is not None and r_p is not None
                        and not (isinstance(r_c, float) and np.isnan(r_c))
                        and not (isinstance(r_p, float) and np.isnan(r_p))):
                    ind_rev_qoq = (r_c - r_p) / abs(r_p)
                    ind_rev_qoq_delta = ind_rev_qoq - med2.get("rev_growth_qoq", 0.0)
            ebitda_l = ebitda[-1] if ebitda else None
            rev_l    = revenue[-1] if revenue else None
            if (ebitda_l is not None and rev_l and rev_l > 0
                    and not (isinstance(ebitda_l, float) and np.isnan(ebitda_l))):
                ind_ebitda_margin = ebitda_l / rev_l
                ind_margin_delta  = ind_ebitda_margin - med2.get("ebitda_margin", 0.0)

        # ── Implied Valuation Multiples  [NEW v3.1] ──────────
        # Computed here so they're available in the HTML expand panel
        implied_ev_ebitda = None
        implied_ev_rev    = None
        implied_p_fcf     = None

        if techs.get("_last_price") and techs["_last_price"] > 0 and mktcap > 0:
            lp = techs["_last_price"]
            latest_debt_v = next((float(v) for v in reversed(debt)
                                  if v is not None and not (isinstance(v, float) and np.isnan(v))), 0.0)
            curr_ev = mktcap + latest_debt_v - cash_on_hand

            ebitda_l_v = ebitda[-1] if ebitda else None
            if (ebitda_l_v is not None and not (isinstance(ebitda_l_v, float) and np.isnan(ebitda_l_v))
                    and ebitda_l_v > 0 and curr_ev > 0):
                implied_ev_ebitda = round(curr_ev / (ebitda_l_v * 4), 1)

            rev_l_v = revenue[-1] if revenue else None
            if (rev_l_v is not None and not (isinstance(rev_l_v, float) and np.isnan(rev_l_v))
                    and rev_l_v > 0 and curr_ev > 0):
                implied_ev_rev = round(curr_ev / (rev_l_v * 4), 1)

            fcf_l_v = fcf[-1] if fcf else None
            if (fcf_l_v is not None and not (isinstance(fcf_l_v, float) and np.isnan(fcf_l_v))
                    and fcf_l_v > 0 and mktcap > 0):
                implied_p_fcf = round(mktcap / (fcf_l_v * 4), 1)

        results.append({
            "ticker": ticker,
            "sector": (sector_map or {}).get(ticker, {}).get("sector", ""),
            "industry": (sector_map or {}).get(ticker, {}).get("industry", ""),
            "marketcap": mktcap,
            "revenue_trend": round(rev_trend, 4),
            "ebitda_trend": round(ebitda_trend, 4),
            "fcf_trend": round(fcf_trend, 4),
            "debt_trend": round(debt_trend, 4),
            "price_momentum": techs["price_momentum"],
            "rsi_score": techs["rsi_score"],
            "sma_cross_score": techs["sma_cross_score"],
            "upside_score": 0.0,
            "sentiment_score": (sentiment_data or {}).get(ticker, {}).get("sentiment_score", 0.0),
            "analyst_count": (sentiment_data or {}).get(ticker, {}).get("total_analysts", 0),
            "analyst_buy": (sentiment_data or {}).get(ticker, {}).get("strongBuy", 0) + (sentiment_data or {}).get(ticker, {}).get("buy", 0),
            "analyst_hold": (sentiment_data or {}).get(ticker, {}).get("hold", 0),
            "analyst_sell": (sentiment_data or {}).get(ticker, {}).get("sell", 0) + (sentiment_data or {}).get(ticker, {}).get("strongSell", 0),
            "last_price": techs.get("_last_price"),
            "internal_target": internal_tp,
            "pt_detail_json": _json_screen.dumps(internal_pt_detail) if internal_pt_detail else "{}",
            "target_price": None,
            "analyst_target_mean": None,
            "analyst_divergence_flag": False,
            "pt_source": "N/A",
            "upside_pct": 0.0,
            "rsi_raw": techs.get("_rsi_raw"),
            "sma20": techs.get("_sma20"),
            "sma50": techs.get("_sma50"),
            "latest_revenue": revenue[-1] if revenue else None,
            "latest_ebitda": ebitda[-1] if ebitda else None,
            "latest_fcf": fcf[-1] if fcf else None,
            "latest_debt": debt[-1] if debt else None,
            "cash_on_hand": cash_on_hand,
            "ind_rev_qoq":        ind_rev_qoq,
            "ind_ebitda_margin":  ind_ebitda_margin,
            "ind_rev_qoq_delta":  ind_rev_qoq_delta,
            "ind_margin_delta":   ind_margin_delta,
            "implied_ev_ebitda":  implied_ev_ebitda,
            "implied_ev_rev":     implied_ev_rev,
            "implied_p_fcf":      implied_p_fcf,
        })

    if no_price_count > 0:
        print(f"  [WARN] {no_price_count}/{len(results)+no_price_count} tickers had no price data in SEP")

    return pl.DataFrame(results) if results else pl.DataFrame()


def apply_blended_targets(screened, analyst_price_targets):
    if screened.height == 0:
        return screened

    import json as _json_bt
    blended_targets = []
    blended_upsides = []
    blended_scores  = []
    analyst_means   = []
    div_flags       = []
    pt_sources      = []

    for i in range(screened.height):
        row = screened.row(i, named=True)
        ticker = row["ticker"]
        internal_tp = row.get("internal_target")
        last_price  = row.get("last_price")
        analyst_data = analyst_price_targets.get(ticker, {})
        pt_detail   = _json_bt.loads(row.get("pt_detail_json", "{}"))

        blended_tp, upside_pct, upside_sc, div_flag, src = compute_blended_target(
            internal_tp, pt_detail, analyst_data, last_price)

        blended_targets.append(blended_tp)
        blended_upsides.append(upside_pct)
        blended_scores.append(upside_sc)
        analyst_means.append(analyst_data.get("target_mean"))
        div_flags.append(div_flag)
        pt_sources.append(src)

    drop_cols = [c for c in ["target_price", "upside_pct", "upside_score",
                              "analyst_target_mean", "analyst_divergence_flag", "pt_source"]
                 if c in screened.columns]
    if drop_cols:
        screened = screened.drop(drop_cols)

    screened = screened.with_columns([
        pl.Series("target_price",            blended_targets),
        pl.Series("upside_pct",              blended_upsides),
        pl.Series("upside_score",            blended_scores),
        pl.Series("analyst_target_mean",     analyst_means),
        pl.Series("analyst_divergence_flag", div_flags),
        pl.Series("pt_source",               pt_sources),
    ])
    return screened


def apply_dynamic_scores(screened, weights):
    if screened.height == 0:
        return screened
    for col in SCORING_CRITERIA:
        mean_val = screened[col].mean()
        std_val = screened[col].std()
        if std_val and std_val > 0:
            screened = screened.with_columns(((pl.col(col) - mean_val) / std_val).alias(f"{col}_z"))
        else:
            screened = screened.with_columns(pl.lit(0.0).alias(f"{col}_z"))
    score_expr = pl.lit(0.0)
    for col in SCORING_CRITERIA:
        score_expr = score_expr + pl.col(f"{col}_z") * weights[col]
    screened = screened.with_columns(score_expr.alias("composite_score"))
    return screened.sort("composite_score", descending=True)


def apply_sector_cap(scored, max_per_sector=MAX_PER_SECTOR, max_results=MAX_RESULTS):
    if scored.height == 0:
        return scored

    sector_counts = {}
    keep_indices = []

    for i in range(scored.height):
        row = scored.row(i, named=True)
        sector = row.get("sector", "Unknown") or "Unknown"

        if sector_counts.get(sector, 0) < max_per_sector:
            keep_indices.append(i)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        if len(keep_indices) >= max_results:
            break

    return scored[keep_indices]


# ============================================================
# HTML REPORT
# ============================================================
def generate_html_report(factors, weights, baseline_weights, screened, run_time,
                         industry_medians=None, market_bias=None):
    """
    v3.1: Added market_bias parameter for Market Bias Banner.
    Added Relative Valuation Grid in expand panel.
    """
    signal_colors = {
        "bullish": "#22c55e", "bearish": "#ef4444", "neutral": "#94a3b8",
        "high_vol": "#ef4444", "low_vol": "#22c55e",
        "risk_on": "#22c55e", "risk_off": "#ef4444",
        "yield_seeking": "#22c55e", "yield_averse": "#ef4444",
        "flight_to_quality": "#ef4444", "risk_appetite": "#22c55e",
        "crisis": "#ff0000", "elevated": "#f59e0b", "calm": "#22c55e",
    }
    signal_labels = {
        "bullish": "^ BULLISH", "bearish": "v BEARISH", "neutral": "- NEUTRAL",
        "high_vol": "^ HIGH VOL", "low_vol": "v LOW VOL",
        "risk_on": "^ RISK ON", "risk_off": "v RISK OFF",
        "yield_seeking": "^ YIELD SEEK", "yield_averse": "v YIELD AVERSE",
        "flight_to_quality": "! FLIGHT TO QUALITY", "risk_appetite": "^ RISK APPETITE",
        "crisis": "!! CRISIS", "elevated": "! ELEVATED STRESS", "calm": "- CALM",
    }

    # ── Market Bias Banner  [NEW v3.1] ────────────────────────
    mb = market_bias or {"label": "NEUTRAL", "score": 0.0, "confidence": 0.0, "components": {}}
    mb_label = mb.get("label", "NEUTRAL")
    mb_score = mb.get("score", 0.0)
    mb_conf  = mb.get("confidence", 0.0)
    mb_comps = mb.get("components", {})

    mb_color = {"BUY": "#22c55e", "SELL": "#ef4444", "NEUTRAL": "#c8a84e"}[mb_label]
    mb_bg    = {"BUY": "#021a0e", "SELL": "#1c0000", "NEUTRAL": "#1a1500"}[mb_label]

    mb_comp_html = ""
    for fname, fcomp in mb_comps.items():
        c_val = fcomp.get("contribution", 0)
        c_color = "#22c55e" if c_val > 0.005 else ("#ef4444" if c_val < -0.005 else "#64748b")
        mb_comp_html += (
            f'<span style="font-size:0.65rem; font-family:\'JetBrains Mono\',monospace; '
            f'color:var(--text-dim); margin-right:0.8rem;">'
            f'{fname}: <span style="color:{c_color}">{c_val:+.3f}</span></span>'
        )

    conf_bar_pct = round(mb_conf * 100)
    conf_color = "#22c55e" if mb_conf > 0.65 else ("#c8a84e" if mb_conf > 0.45 else "#64748b")

    skew_note = (
        "<strong style='color:#22c55e'>Momentum weights BOOSTED</strong> — technical signals skewed toward buy side"
        if mb_label == "BUY" and mb_conf > 0.55 else
        "<strong style='color:#ef4444'>Momentum weights SUPPRESSED</strong> — technicals downweighted, fundamentals/FCF dominant"
        if mb_label == "SELL" and mb_conf > 0.55 else
        "Momentum weights unchanged — confidence below 55% threshold"
    )

    market_bias_banner = f"""
    <div style="background:{mb_bg}; border:2px solid {mb_color}40; border-radius:10px; padding:1rem 1.5rem; margin-bottom:1.5rem; display:flex; align-items:center; gap:2rem; flex-wrap:wrap;">
        <div style="min-width:100px;">
            <div style="font-size:0.6rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.1em; margin-bottom:0.2rem;">Daily Market Bias</div>
            <div style="font-size:0.55rem; color:var(--text-dim); margin-bottom:0.4rem;">SPY Directional Signal</div>
            <div style="font-size:2.2rem; font-weight:800; color:{mb_color}; font-family:'JetBrains Mono',monospace; letter-spacing:0.05em; line-height:1;">{mb_label}</div>
        </div>
        <div>
            <div style="font-size:0.6rem; color:var(--text-dim); margin-bottom:0.2rem;">Composite Score</div>
            <div style="font-size:1.4rem; font-weight:700; color:{mb_color}; font-family:'JetBrains Mono',monospace;">{mb_score:+.3f}</div>
            <div style="font-size:0.55rem; color:var(--text-dim); margin-top:0.1rem;">range: -1.0 to +1.0</div>
        </div>
        <div>
            <div style="font-size:0.6rem; color:var(--text-dim); margin-bottom:0.2rem;">Signal Confidence</div>
            <div style="font-size:1.4rem; font-weight:700; color:{conf_color}; font-family:'JetBrains Mono',monospace;">{mb_conf:.0%}</div>
            <div style="width:90px; height:5px; background:var(--navy-mid); border-radius:3px; margin-top:0.3rem;">
                <div style="width:{conf_bar_pct}%; height:100%; background:{conf_color}; border-radius:3px; transition:width 0.3s;"></div>
            </div>
        </div>
        <div style="flex:1; min-width:240px;">
            <div style="font-size:0.6rem; color:var(--text-dim); margin-bottom:0.35rem; text-transform:uppercase; letter-spacing:0.06em;">Factor Contributions</div>
            <div style="display:flex; flex-wrap:wrap; gap:0.1rem;">{mb_comp_html}</div>
        </div>
        <div style="font-size:0.68rem; color:var(--text-dim); max-width:240px; line-height:1.6; border-left:1px solid {mb_color}30; padding-left:1.25rem;">
            {skew_note}
        </div>
    </div>"""

    # Factor cards
    factor_cards = ""
    for fname, fdata in factors.items():
        sig = fdata["signal"]
        color = signal_colors.get(sig, "#94a3b8")
        label = signal_labels.get(sig, "- NEUTRAL")
        z = fdata["z_score"]
        bar_width = min(abs(z) / 3 * 100, 100)
        margin = "margin-left: 50%" if z >= 0 else f"margin-left: {50 - bar_width}%"
        factor_cards += f"""
        <div class="factor-card">
            <div class="factor-header">
                <span class="factor-name">{fname.upper()}</span>
                <span class="factor-signal" style="color: {color}">{label}</span>
            </div>
            <div class="factor-desc">{fdata['description']}</div>
            <div class="factor-detail">{fdata.get('detail', '')}</div>
            <div class="z-bar-container">
                <div class="z-bar-center"></div>
                <div class="z-bar" style="width: {bar_width}%; background: {color}; {margin}; opacity: 0.7;"></div>
            </div>
            <div class="z-label">z-score: {z:+.2f}</div>
        </div>
        """

    # Weight table
    nice_names = {
        "revenue_trend": "Revenue", "ebitda_trend": "EBITDA", "fcf_trend": "Free Cash Flow",
        "debt_trend": "Debt Reduction", "price_momentum": "Price Momentum (60d)",
        "rsi_score": "RSI Signal (14d)", "sma_cross_score": "SMA Cross (20/50)",
        "upside_score": "Blended Target Upside", "sentiment_score": "Analyst Consensus",
    }
    categories = {
        "revenue_trend": "Fundamental", "ebitda_trend": "Fundamental",
        "fcf_trend": "Fundamental", "debt_trend": "Fundamental",
        "price_momentum": "Technical", "rsi_score": "Technical",
        "sma_cross_score": "Technical",
        "upside_score": "Valuation", "sentiment_score": "Sentiment",
    }
    weight_rows = ""
    last_cat = ""
    for criterion in SCORING_CRITERIA:
        bw = baseline_weights[criterion]
        dw = weights[criterion]
        diff = dw - bw
        diff_color = "#22c55e" if diff > 0.01 else ("#ef4444" if diff < -0.01 else "#94a3b8")
        cat = categories[criterion]
        cat_cell = ""
        if cat != last_cat:
            cat_count = sum(1 for c in SCORING_CRITERIA if categories[c] == cat)
            cat_cell = f'<td rowspan="{cat_count}" class="cat-cell">{cat}</td>'
            last_cat = cat
        weight_rows += f"""
        <tr>
            {cat_cell}
            <td>{nice_names[criterion]}</td>
            <td>{bw:.1%}</td>
            <td style="color: {diff_color}; font-weight: 600;">{dw:.1%}</td>
            <td style="color: {diff_color};">{diff:+.1%}</td>
        </tr>
        """

    # Regime badges
    regime_badges = ""
    for fname, fdata in factors.items():
        sig = fdata["signal"]
        c = signal_colors.get(sig, "#334155")
        lbl = signal_labels.get(sig, "NEUTRAL")
        regime_badges += f'<span class="regime-badge" style="background: {c}20; color: {c}; border: 1px solid {c}40;">{fname.upper()}: {lbl}</span>'

    # Results rows
    import json as _json_html
    result_rows = ""
    if screened.height > 0:
        screened_sorted = screened.sort(["sector", "composite_score"], descending=[False, True])
        prev_sector = None
        sector_rank = {}
        global_i = 0
        for _loop_i in range(screened_sorted.height):
            row = screened_sorted.row(_loop_i, named=True)
            i = global_i
            global_i += 1

            row_sector = row.get("sector") or "Unknown"
            if row_sector != prev_sector:
                sector_count = sum(1 for r in screened_sorted.iter_rows(named=True)
                                   if (r.get("sector") or "Unknown") == row_sector)
                result_rows += f"""
            <tr style="background:#0d1e38; border-top: 2px solid var(--border);">
                <td colspan="15" style="padding:0.5rem 1rem;">
                    <span style="font-size:0.75rem; font-weight:700; color:var(--gold); text-transform:uppercase; letter-spacing:0.08em;">{row_sector}</span>
                    <span style="font-size:0.65rem; color:var(--text-dim); margin-left:0.75rem;">{sector_count} companies — ranked by composite score</span>
                </td>
            </tr>"""
                prev_sector = row_sector
                sector_rank[row_sector] = 0
            sector_rank[row_sector] = sector_rank.get(row_sector, 0) + 1
            rank_display = sector_rank[row_sector]

            mktcap_b = row["marketcap"] / 1e9
            score = row["composite_score"]
            score_color = "#22c55e" if score > 0.5 else ("#c8a84e" if score > 0 else "#ef4444")
            rsi_raw = row.get("rsi_raw")
            if rsi_raw is not None:
                rsi_color = "#22c55e" if 50 <= rsi_raw <= 70 else ("#ef4444" if rsi_raw > 80 or rsi_raw < 30 else "#94a3b8")
                rsi_str = f"{rsi_raw:.0f}"
            else:
                rsi_color = "#64748b"
                rsi_str = "N/A"
            mom_pct = row["price_momentum"] * 100
            sma_val = row["sma_cross_score"]
            last_price = row.get("last_price") or 0
            target_price = row.get("target_price")
            upside_pct = (row.get("upside_pct") or 0) * 100
            upside_color = "#22c55e" if upside_pct > 10 else ("#c8a84e" if upside_pct > 0 else "#ef4444")

            pt_src = row.get("pt_source", "N/A")
            div_flag = row.get("analyst_divergence_flag", False)
            analyst_tp = row.get("analyst_target_mean")
            div_badge = ' <span style="font-size:0.55rem;color:#f59e0b;font-weight:700" title="Diverges >40% from analyst consensus">⚠</span>' if div_flag else ''
            if target_price:
                tp_str = f"${target_price:.2f}"
                src_color = "#94a3b8" if "M" in str(pt_src) else "#c8a84e"
                tp_str += f' <span style="font-size:0.55rem;color:{src_color}">{pt_src}</span>{div_badge}'
            else:
                tp_str = "N/A"

            price_str = f"${last_price:.2f}" if last_price else "N/A"
            sma_str = f'{"+" if sma_val > 0 else ""}{sma_val:.2f}' if sma_val != 0 or row.get("sma20") else "N/A"
            upside_str = f"{upside_pct:+.1f}%" if target_price else "N/A"

            sent_score = row.get("sentiment_score") or 0.0
            analyst_count = row.get("analyst_count") or 0
            analyst_buy = row.get("analyst_buy") or 0
            analyst_hold = row.get("analyst_hold") or 0
            analyst_sell = row.get("analyst_sell") or 0
            if analyst_count > 0:
                sent_color = "#22c55e" if sent_score > 0.25 else ("#ef4444" if sent_score < -0.1 else "#c8a84e")
                sent_str = f"{analyst_buy}B/{analyst_hold}H/{analyst_sell}S"
            else:
                sent_color = "#64748b"
                sent_str = "N/A"

            def tc(v):
                return "pos" if v > 0 else "neg"

            pt_detail = {}
            try:
                pt_detail = _json_html.loads(row.get("pt_detail_json", "{}"))
            except Exception:
                pass

            # Industry vs Peers panel
            rev_qoq_val    = row.get("ind_rev_qoq")
            margin_val     = row.get("ind_ebitda_margin")
            rev_qoq_delta  = row.get("ind_rev_qoq_delta")
            margin_delta   = row.get("ind_margin_delta")
            ticker_sector  = row.get("sector", "")
            med_row        = (industry_medians or {}).get(ticker_sector, (industry_medians or {}).get("_default", {}))

            def _delta_html(val, delta, fmt):
                if val is None or delta is None:
                    return '<span style="color:var(--text-dim)">N/A</span>'
                col = "#22c55e" if delta >= 0 else "#ef4444"
                sign = "+" if delta >= 0 else ""
                return f'<span style="color:var(--text)">{fmt(val)}</span> <span style="color:{col};font-size:0.65rem">({sign}{fmt(delta)} vs peers)</span>'

            peers_panel_html = ""
            if rev_qoq_val is not None or margin_val is not None:
                med_rg  = med_row.get("rev_growth_qoq")
                med_em  = med_row.get("ebitda_margin")
                n_peers = med_row.get("n", 0)
                peers_panel_html = f"""
                            <div style="min-width:220px;">
                                <div style="font-size:0.7rem; color:var(--gold-dim); font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.5rem;">
                                    Industry vs Peers ({ticker_sector}, n≈{n_peers})
                                </div>
                                <div style="font-size:0.72rem; font-family:'JetBrains Mono',monospace; line-height:2.0; color:var(--text);">
                                    <div>Rev Growth QoQ: {_delta_html(rev_qoq_val, rev_qoq_delta, lambda v: f'{v*100:+.1f}%')}</div>
                                    <div>Sector median: <span style="color:var(--text-dim)">{f'{med_rg*100:+.1f}%' if med_rg is not None else 'N/A'}</span></div>
                                    <div style="margin-top:0.4rem;">EBITDA Margin: {_delta_html(margin_val, margin_delta, lambda v: f'{v*100:.1f}%')}</div>
                                    <div>Sector median: <span style="color:var(--text-dim)">{f'{med_em*100:.1f}%' if med_em is not None else 'N/A'}</span></div>
                                    <div style="margin-top:0.6rem; font-size:0.65rem; color:var(--text-dim);">
                                        Passed outperformance gate (≥2/3 metrics beat sector median)
                                    </div>
                                </div>
                            </div>"""

            # ── Relative Valuation Grid  [NEW v3.1] ──────────────
            rel_val_html = ""
            if pt_detail and pt_detail.get("sector_anchor"):
                sa = pt_detail["sector_anchor"]
                row_ev_ebitda = row.get("implied_ev_ebitda")
                row_ev_rev    = row.get("implied_ev_rev")
                row_p_fcf     = row.get("implied_p_fcf")
                # Convert FCF yield% anchor to P/FCF multiple
                fcf_yield_pct = sa.get("fcf_yield_pct", 4.5)
                fcf_anchor_multiple = round(100.0 / fcf_yield_pct, 1) if fcf_yield_pct and fcf_yield_pct > 0 else None

                def _mult_row_html(label, ticker_val, anchor_val):
                    if ticker_val is None or anchor_val is None or anchor_val == 0:
                        return (f'<tr><td style="color:var(--text-dim);padding:0.25rem 0.4rem;">{label}</td>'
                                f'<td colspan="3" style="color:var(--text-dim);padding:0.25rem 0.4rem;">N/A</td></tr>')
                    pct_of_anchor = ticker_val / anchor_val
                    discount = 1.0 - pct_of_anchor   # positive = trading cheaper
                    cheap  = discount > 0.10
                    pricey = discount < -0.10
                    val_color = "#22c55e" if cheap else ("#ef4444" if pricey else "#94a3b8")
                    disc_str = f"{discount*100:+.0f}%"
                    badge = "CHEAP" if cheap else ("PRICEY" if pricey else "FAIR")
                    return (f'<tr style="font-size:0.68rem;">'
                            f'<td style="color:var(--text-dim);padding:0.25rem 0.4rem;white-space:nowrap;">{label}</td>'
                            f'<td style="font-family:\'JetBrains Mono\',monospace;padding:0.25rem 0.4rem;">{ticker_val:.1f}x</td>'
                            f'<td style="color:var(--text-dim);padding:0.25rem 0.4rem;">{anchor_val:.1f}x</td>'
                            f'<td style="padding:0.25rem 0.4rem;">'
                            f'<span style="color:{val_color};font-weight:700;">{disc_str}</span>'
                            f' <span style="font-size:0.6rem;color:{val_color};background:{val_color}20;padding:0.1rem 0.3rem;border-radius:3px;">{badge}</span>'
                            f'</td></tr>')

                rel_val_html = f"""
                            <div style="min-width:280px;">
                                <div style="font-size:0.7rem; color:var(--gold-dim); font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.5rem;">
                                    Valuation vs Sector Peers
                                </div>
                                <table style="width:100%; border-collapse:collapse; font-family:'JetBrains Mono',monospace;">
                                    <thead>
                                        <tr style="font-size:0.6rem; color:var(--text-dim); border-bottom:1px solid var(--border);">
                                            <th style="text-align:left;padding:0.2rem 0.4rem;">Metric</th>
                                            <th style="text-align:left;padding:0.2rem 0.4rem;">Stock</th>
                                            <th style="text-align:left;padding:0.2rem 0.4rem;">Sector Anchor</th>
                                            <th style="text-align:left;padding:0.2rem 0.4rem;">vs Peers</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {_mult_row_html("EV/EBITDA", row_ev_ebitda, sa.get("ev_ebitda"))}
                                        {_mult_row_html("EV/Revenue", row_ev_rev, sa.get("ev_rev"))}
                                        {_mult_row_html("P/FCF", row_p_fcf, fcf_anchor_multiple)}
                                    </tbody>
                                </table>
                                <div style="font-size:0.62rem; color:var(--text-dim); margin-top:0.5rem; line-height:1.5;">
                                    Anchors are rate-adjusted ({FED_TARGET_RATE*100:.2f}% Fed rate applied).<br>
                                    <span style="color:#22c55e;">Green = trading below sector median</span> — this is why it passed the screen.
                                </div>
                            </div>"""

            val_panel = ""
            if pt_detail and pt_detail.get("models"):
                models = pt_detail["models"]
                conv_weights = pt_detail.get("conviction_weights", {})
                dominant = pt_detail.get("dominant_model", "N/A")
                sector_anchor = pt_detail.get("sector_anchor", {})
                rate_compression = pt_detail.get("rate_compression", 1.0)
                rate_spread_bps = pt_detail.get("rate_spread_bps", 0)
                analyst_ref_str = f"${analyst_tp:.2f}" if analyst_tp else "N/A"

                model_rows_html = ""
                model_labels = {"ev_ebitda": "EV/EBITDA", "ev_rev": "EV/Revenue", "fcf_yield": "FCF Yield"}
                for mname, mdata in models.items():
                    mlabel = model_labels.get(mname, mname)
                    mpt = mdata.get("pt", "N/A")
                    mr2 = mdata.get("r2", 0)
                    mconv = mdata.get("conviction", 0)
                    mw = conv_weights.get(mname, 0)
                    mbm = mdata.get("blended_mult") or mdata.get("required_yield")
                    msa = mdata.get("sector_mult") or mdata.get("sector_anchor_yield")
                    is_dom = "font-weight:700;color:var(--gold)" if mname == dominant else ""
                    model_rows_html += f"""
                    <tr style="font-size:0.68rem;">
                        <td style="{is_dom}">{mlabel}</td>
                        <td>${mpt:.2f}</td>
                        <td>{mr2:.2f}</td>
                        <td>{mconv:.2f}</td>
                        <td style="font-weight:600">{mw:.0%}</td>
                        <td>{msa}</td>
                        <td>{mbm}</td>
                    </tr>"""

                rate_color = "#ef4444" if rate_spread_bps > 0 else "#22c55e"
                comp_pct = (1 - rate_compression) * 100
                comp_str = f'{comp_pct:+.1f}% compression' if rate_spread_bps > 0 else f'{abs(comp_pct):.1f}% expansion'

                val_panel = f"""
                <tr class="val-panel-row" id="vp-{i}" style="display:none">
                    <td colspan="15" style="padding: 0.75rem 1rem; background: #0d1e38; border-bottom: 2px solid var(--border);">
                        <div style="display:flex; gap:2rem; align-items:flex-start; flex-wrap:wrap;">
                            <div style="flex:1; min-width:320px;">
                                <div style="font-size:0.7rem; color:var(--gold-dim); font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.5rem;">
                                    Valuation Model Breakdown
                                </div>
                                <table style="width:100%; border-collapse:collapse; font-family:'JetBrains Mono',monospace;">
                                    <thead>
                                        <tr style="font-size:0.6rem; color:var(--text-dim); border-bottom:1px solid var(--border);">
                                            <th style="text-align:left;padding:0.2rem 0.3rem">Model</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">PT</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">R²</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">Conv</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">Wt</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">Sector Anchor</th>
                                            <th style="text-align:left;padding:0.2rem 0.3rem">Used</th>
                                        </tr>
                                    </thead>
                                    <tbody>{model_rows_html}</tbody>
                                </table>
                            </div>
                            <div style="min-width:200px;">
                                <div style="font-size:0.7rem; color:var(--gold-dim); font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.5rem;">
                                    Sector Calibration
                                </div>
                                <div style="font-size:0.72rem; font-family:'JetBrains Mono',monospace; line-height:1.8; color:var(--text);">
                                    <div>Sector: <span style="color:var(--gold)">{pt_detail.get('sector','N/A')}</span></div>
                                    <div>EV/EBITDA anchor: <span style="color:var(--text)">{sector_anchor.get('ev_ebitda','N/A')}x</span></div>
                                    <div>EV/Rev anchor: <span style="color:var(--text)">{sector_anchor.get('ev_rev','N/A')}x</span></div>
                                    <div>FCF yield anchor: <span style="color:var(--text)">{sector_anchor.get('fcf_yield_pct','N/A')}%</span></div>
                                    <div style="margin-top:0.4rem;">Fed rate: <span style="color:{rate_color}">{FED_TARGET_RATE*100:.2f}%</span></div>
                                    <div>Rate spread: <span style="color:{rate_color}">{rate_spread_bps:+.0f}bps vs neutral</span></div>
                                    <div>Multiple adj: <span style="color:{rate_color}">{comp_str}</span></div>
                                </div>
                            </div>
                            <div style="min-width:180px;">
                                <div style="font-size:0.7rem; color:var(--gold-dim); font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.5rem;">
                                    Analyst Reference
                                </div>
                                <div style="font-size:0.72rem; font-family:'JetBrains Mono',monospace; line-height:1.8; color:var(--text);">
                                    <div>Analyst consensus: <span style="color:var(--text-dim)">{analyst_ref_str}</span></div>
                                    <div>Internal PT: <span style="color:var(--gold)">${target_price:.2f}</span></div>
                                    {'<div style="color:#f59e0b;font-weight:600">⚠ Divergence >40% flagged</div>' if div_flag else '<div style="color:#22c55e">✓ Within 40% of analyst</div>' if analyst_tp else '<div style="color:var(--text-dim)">No analyst data</div>'}
                                    <div style="margin-top:0.4rem;">Dominant model: <span style="color:var(--gold)">{model_labels.get(dominant, dominant)}</span></div>
                                </div>
                            </div>
                            {peers_panel_html}
                            {rel_val_html}
                        </div>
                    </td>
                </tr>"""

            result_rows += f"""
            <tr class="result-row" onclick="toggleValPanel({i})" style="cursor:pointer">
                <td class="rank">{rank_display}</td>
                <td class="ticker">{row['ticker']}<br><span class="industry-label">{row.get('industry', '') or row.get('sector', '')}</span></td>
                <td>${mktcap_b:.1f}B</td>
                <td>{price_str}</td>
                {f'<td class="{tc(row["revenue_trend"])}">{row["revenue_trend"]:.3f}</td>'}
                {f'<td class="{tc(row["ebitda_trend"])}">{row["ebitda_trend"]:.3f}</td>'}
                {f'<td class="{tc(row["fcf_trend"])}">{row["fcf_trend"]:.3f}</td>'}
                {f'<td class="{tc(row["debt_trend"])}">{row["debt_trend"]:.3f}</td>'}
                <td class="{tc(mom_pct)}">{mom_pct:+.1f}%</td>
                <td style="color: {rsi_color}">{rsi_str}</td>
                <td class="{tc(sma_val)}">{sma_str}</td>
                <td style="color: {upside_color}">{tp_str}</td>
                <td style="color: {upside_color}; font-weight: 600;">{upside_str}</td>
                <td style="color: {sent_color}" title="{sent_score:+.2f}">{sent_str}</td>
                <td style="color: {score_color}; font-weight: 700;">{score:.3f}</td>
            </tr>
            {val_panel}
            """

    no_results = '<tr><td colspan="15" style="text-align:center; color: #64748b; padding: 2rem;">No stocks matched screening criteria</td></tr>'

    exclusion_note = "Excl. ADRs, Biotech/Pharma"
    cap_label = MARKET_CAP_PRESET.upper() if MARKET_CAP_PRESET != "custom" else "CUSTOM"
    rate_spread_bps_display = round((FED_TARGET_RATE - FED_NEUTRAL_RATE) * 10000, 1)
    rate_sign = "+" if rate_spread_bps_display >= 0 else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RCG Dynamic Factor Screener v3.1</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
    --navy: #0a1628; --navy-light: #111d33; --navy-mid: #162240;
    --gold: #c8a84e; --gold-dim: #8b7635;
    --text: #e2e8f0; --text-dim: #64748b;
    --green: #22c55e; --red: #ef4444; --blue: #3b82f6; --border: #1e3050;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'DM Sans', sans-serif; background: var(--navy); color: var(--text); min-height: 100vh; padding: 2rem; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
.header {{ display: flex; justify-content: space-between; align-items: center; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }}
.header h1 {{ font-size: 1.5rem; font-weight: 700; color: var(--gold); }}
.header .subtitle {{ font-size: 0.8rem; color: var(--text-dim); margin-top: 0.25rem; }}
.header .timestamp {{ font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-dim); }}
.section-title {{ font-size: 1.1rem; font-weight: 600; color: var(--gold); margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }}
.factor-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
.factor-card {{ background: var(--navy-light); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }}
.factor-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }}
.factor-name {{ font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 600; color: var(--gold); letter-spacing: 0.05em; }}
.factor-signal {{ font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; font-weight: 600; }}
.factor-desc {{ font-size: 0.75rem; color: var(--text); margin-bottom: 0.25rem; line-height: 1.4; }}
.factor-detail {{ font-size: 0.7rem; color: var(--text-dim); margin-bottom: 0.75rem; font-family: 'JetBrains Mono', monospace; }}
.z-bar-container {{ position: relative; height: 6px; background: var(--navy-mid); border-radius: 3px; margin-bottom: 0.35rem; overflow: hidden; }}
.z-bar-center {{ position: absolute; left: 50%; top: 0; width: 1px; height: 100%; background: var(--text-dim); }}
.z-bar {{ position: absolute; top: 0; height: 100%; border-radius: 3px; }}
.z-label {{ font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; color: var(--text-dim); text-align: right; }}
.weights-section {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-bottom: 2rem; }}
.weight-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
.weight-table th {{ text-align: left; padding: 0.5rem 0.75rem; color: var(--gold-dim); font-weight: 600; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }}
.weight-table td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }}
.weight-table .cat-cell {{ font-family: 'DM Sans', sans-serif; font-weight: 600; color: var(--blue); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; border-right: 2px solid var(--border); vertical-align: middle; }}
.methodology {{ background: var(--navy-light); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; font-size: 0.75rem; color: var(--text-dim); line-height: 1.6; }}
.methodology strong {{ color: var(--text); }}
.results-wrap {{ overflow-x: auto; }}
.results-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-top: 1rem; min-width: 1000px; }}
.results-table th {{ text-align: left; padding: 0.5rem 0.5rem; color: var(--gold-dim); font-weight: 600; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.03em; border-bottom: 2px solid var(--border); position: sticky; top: 0; background: var(--navy); white-space: nowrap; }}
.results-table td {{ padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; white-space: nowrap; }}
.results-table tr:hover {{ background: var(--navy-mid); }}
.results-table .rank {{ color: var(--text-dim); }}
.results-table .ticker {{ color: var(--gold); font-weight: 600; }}
.results-table .ticker .industry-label {{ display: block; font-size: 0.55rem; font-weight: 400; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px; }}
.results-table .pos {{ color: var(--green); }}
.results-table .neg {{ color: var(--red); }}
.results-table .col-label-row th {{ font-size: 0.55rem; font-weight: 400; color: var(--text-dim); text-transform: lowercase; letter-spacing: 0.02em; padding-top: 0; border-bottom: 2px solid var(--border); font-style: italic; }}
.col-group-header {{ text-align: center !important; border-bottom: 2px solid var(--border); padding: 0.3rem 0.5rem; }}
.col-fund {{ color: var(--blue) !important; }}
.col-tech {{ color: var(--gold) !important; }}
.regime-badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600; font-family: 'JetBrains Mono', monospace; margin: 0.15rem 0.25rem; }}
.regime-summary {{ margin-bottom: 1rem; padding: 1rem; background: var(--navy-light); border: 1px solid var(--border); border-radius: 8px; }}
.regime-summary .label {{ font-size: 0.7rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
.footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); font-size: 0.7rem; color: var(--text-dim); text-align: center; }}
.result-row:hover {{ background: var(--navy-mid) !important; }}
.val-panel-row td {{ padding: 0 !important; }}
</style>
<script>
function toggleValPanel(i) {{
    var row = document.getElementById('vp-' + i);
    if (!row) return;
    row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
}}
</script>
</head>
<body>
<div class="container">

    <div class="header">
        <div>
            <h1>Dynamic Factor-Weighted Screener <span style="font-size: 0.7rem; color: var(--text-dim);">v3.1</span></h1>
            <div class="subtitle">Robin Capital Group &mdash; {exclusion_note}, Debt Coverage &ge; {MIN_DEBT_COVERAGE:.0%}, Max {MAX_PER_SECTOR}/sector &mdash; Cap Filter: <strong style="color:var(--gold)">{cap_label}</strong> (${MARKET_CAP_MIN/1e9:.1f}B&ndash;${MARKET_CAP_MAX/1e9:.0f}B) &mdash; Fed Rate: <strong style="color:var(--gold)">{FED_TARGET_RATE*100:.2f}%</strong> ({rate_sign}{rate_spread_bps_display}bps vs neutral)</div>
        </div>
        <div class="timestamp">{run_time}</div>
    </div>

    <div class="regime-summary">
        <div class="label">Current Macro Regime</div>
        <div>{regime_badges}</div>
    </div>

    {market_bias_banner}

    <div class="section-title">Factor Model &mdash; ETF-Derived Signals</div>
    <div class="factor-grid">{factor_cards}</div>

    <div class="section-title">Scoring Weights &mdash; Baseline vs Dynamic (9 Criteria, Zero-Sum Constrained)</div>
    <div class="weights-section">
        <table class="weight-table">
            <thead><tr><th>Type</th><th>Criterion</th><th>Baseline</th><th>Dynamic</th><th>Shift</th></tr></thead>
            <tbody>{weight_rows}</tbody>
        </table>
        <div class="methodology">
            <strong>Methodology v3.1</strong><br><br>
            <strong>Market Bias Signal (NEW):</strong> Composite SPY directional bias derived from all 6 ETF-factor signals.
            Weights: Momentum 35%, Stress 25%, Volatility 15%, Liquidity 15%, Quality 5%, Dividends 5%.
            When confidence &gt;55%, momentum criteria weights are skewed ±up to 3% (zero-sum vs fundamentals).<br><br>
            <strong>9 scoring dimensions</strong> &mdash; 4 fundamental + 3 technical + 1 valuation + 1 sentiment &mdash; dynamically weighted
            by the current macro regime + Fed rate overlay. Sensitivity matrices are <strong>zero-sum constrained</strong>.<br><br>
            <strong>Valuation vs Peers (NEW):</strong> Each row expand now shows EV/EBITDA, EV/Revenue, and P/FCF vs
            rate-adjusted sector anchors. Green = trading cheap vs sector median. This is the primary reason these
            names survived the screen — the multiples confirm it.<br><br>
            <strong>Fundamental:</strong> Revenue, EBITDA, FCF, and Debt trends (QoQ Theil-Sen robust regression, R&sup2;-weighted)<br><br>
            <strong>Technical:</strong> Price momentum (60d return), <strong>Wilder RSI</strong> (14d EMA), SMA crossover (20d vs 50d, magnitude-scaled)<br><br>
            <strong>Valuation &mdash; Multi-Model Conviction-Weighted PT (v3):</strong>
            EV/EBITDA, EV/Revenue, FCF Yield — conviction-weighted blend. Analyst consensus = sanity check only.<br><br>
            <strong>PT Source Legend:</strong> M = internal model | M✓ = aligns with analyst | M* = diverges &gt;40% ⚠ | A = analyst only<br><br>
            <strong>Click any row</strong> to expand valuation model breakdown + sector calibration + peer comparison + relative valuation grid.
        </div>
    </div>

    <div class="section-title">Screening Results &mdash; Top {MAX_RESULTS} &mdash; Cap Range: {cap_label} (${MARKET_CAP_MIN/1e9:.1f}B&ndash;${MARKET_CAP_MAX/1e9:.0f}B) &mdash; Click row for full detail</div>
    <div class="results-wrap">
    <table class="results-table">
        <thead>
            <tr>
                <th rowspan="3">#</th>
                <th rowspan="3">Ticker</th>
                <th rowspan="3">Mkt Cap</th>
                <th rowspan="3">Price</th>
                <th colspan="4" class="col-group-header col-fund">Fundamentals</th>
                <th colspan="3" class="col-group-header col-tech">Technicals</th>
                <th colspan="2" class="col-group-header" style="color: #a78bfa !important;">Valuation</th>
                <th class="col-group-header" style="color: #f59e0b !important;">Sent.</th>
                <th rowspan="3">Score</th>
            </tr>
            <tr>
                <th>Rev</th><th>EBITDA</th><th>FCF</th><th>Debt</th>
                <th>Mom 60d</th><th>RSI</th><th>SMA</th>
                <th>Target</th><th>Upside</th>
                <th>Analysts</th>
            </tr>
            <tr class="col-label-row">
                <th>trend</th><th>trend</th><th>trend</th><th>trend</th>
                <th>% chg</th><th>level</th><th>signal</th>
                <th>$ blended</th><th>% chg</th>
                <th>B/H/S</th>
            </tr>
        </thead>
        <tbody>
            {result_rows if result_rows else no_results}
        </tbody>
    </table>
    </div>

    <div class="footer">Robin Capital Group LLC &bull; Dynamic Factor Screener v3.1 &bull; {run_time} &bull; Fed Rate: {FED_TARGET_RATE*100:.2f}% &bull; Cap: {cap_label} &bull; Market Bias: {mb_label} ({mb_conf:.0%} conf)</div>
</div>
</body>
</html>"""
    return html


# ============================================================
# MAIN
# ============================================================
def main(market_cap_preset=None, fed_target_rate=None, fed_neutral_rate=None,
         market_cap_min=None, market_cap_max=None):
    global MARKET_CAP_MIN, MARKET_CAP_MAX, MARKET_CAP_PRESET, FED_TARGET_RATE, FED_NEUTRAL_RATE

    if fed_target_rate is not None:
        FED_TARGET_RATE = fed_target_rate
    if fed_neutral_rate is not None:
        FED_NEUTRAL_RATE = fed_neutral_rate
    if market_cap_preset is not None:
        MARKET_CAP_PRESET = market_cap_preset
    if market_cap_min is not None and market_cap_max is not None:
        MARKET_CAP_PRESET = "custom"
        MARKET_CAP_MIN = market_cap_min
        MARKET_CAP_MAX = market_cap_max
    elif market_cap_preset is not None:
        MARKET_CAP_MIN, MARKET_CAP_MAX = _CAP_PRESETS.get(
            market_cap_preset.lower(), _CAP_PRESETS["all"])

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 60)
    print("  RCG Dynamic Factor-Weighted Screener  v3.1")
    print("  Fundamentals + Technicals + Macro Regime + Market Bias")
    print(f"  Cap Filter : {MARKET_CAP_PRESET.upper()} (${MARKET_CAP_MIN/1e9:.1f}B – ${MARKET_CAP_MAX/1e9:.0f}B)")
    print(f"  Fed Rate   : {FED_TARGET_RATE*100:.2f}%  (neutral: {FED_NEUTRAL_RATE*100:.2f}%)")
    print(f"  Max Results: {MAX_RESULTS}  |  Sector Cap: {MAX_PER_SECTOR}")
    print("=" * 60)

    print("\n[1/7] Loading ETF prices...")
    etf_prices = load_etf_prices()

    print("[2/7] Computing factor signals + market bias...")
    factors = compute_factor_signals(etf_prices)
    for fname, fdata in factors.items():
        print(f"  {fname:12s} | z={fdata['z_score']:+.2f} | {fdata['signal']:15s} | {fdata['description']}")

    # ── Market Bias  [NEW v3.1] ───────────────────────────────
    market_bias = compute_market_bias(factors)
    print(f"\n  MARKET BIAS: {market_bias['label']} | score={market_bias['score']:+.3f} | conf={market_bias['confidence']:.0%}")
    print(f"  {market_bias['description']}")

    # Inject market bias into weight function via function attribute (avoids signature change)
    compute_dynamic_weights._market_bias = market_bias

    baseline_weights = {c: BASELINE_WEIGHT for c in SCORING_CRITERIA}
    weights = compute_dynamic_weights(factors)
    print("\n[3/7] Dynamic weights (9 criteria, zero-sum enforced):")
    for c in SCORING_CRITERIA:
        bw, dw = baseline_weights[c], weights[c]
        if c in ("revenue_trend", "ebitda_trend", "fcf_trend", "debt_trend"):
            tag = "FUND"
        elif c == "upside_score":
            tag = "VALN"
        elif c == "sentiment_score":
            tag = "SENT"
        else:
            tag = "TECH"
        print(f"  [{tag}] {c:20s} | {bw:.1%} -> {dw:.1%} ({dw-bw:+.1%})")

    print("\n[4/7] Loading fundamentals + metadata...")
    sf1 = load_fundamentals()
    adr_tickers, biotech_tickers, sector_map = load_ticker_metadata()
    latest = sf1.group_by("ticker").agg(
        pl.col("marketcap").last().alias("marketcap"),
    ).filter((pl.col("marketcap") >= MARKET_CAP_MIN) & (pl.col("marketcap") <= MARKET_CAP_MAX))
    eligible = latest["ticker"].to_list()
    print(f"  {len(eligible)} tickers in cap range")

    print("[5/7] Loading equity prices + computing technicals (Wilder RSI)...")
    equity_prices = load_equity_prices(eligible)

    print("[4b/7] Computing industry medians for outperformance filter...")
    industry_medians = compute_industry_medians(sf1, sector_map)
    print(f"  Sector medians computed for {len(industry_medians)} sectors")

    screened = screen_stocks(sf1, equity_prices, adr_tickers, biotech_tickers,
                             sentiment_data=None, sector_map=sector_map,
                             industry_medians=industry_medians)
    print(f"  {screened.height} passed industry outperformance + fundamental + exclusion screen")

    # Day 5 (Phase 1): bumped from 2x to max(2x, 100). The 2x rule (=80) was
    # leaving 5-10 names per run with pt_source=N/A — names that ranked into
    # the final top-40 only after Finnhub data shifted scores, but never had
    # Finnhub fetched because they were rank 81-100 pre-fetch.
    SENTIMENT_POOL = min(screened.height, max(MAX_RESULTS * 2, 100))
    preliminary = apply_dynamic_scores(screened, weights)
    top_tickers = preliminary["ticker"].to_list()[:SENTIMENT_POOL]
    print(f"  Top {len(top_tickers)} candidates selected for API lookups")

    print(f"\n[6/7] Fetching Finnhub data for {len(top_tickers)} tickers...")
    sentiment_data = fetch_analyst_sentiment(top_tickers)
    analyst_price_targets = fetch_analyst_price_targets(top_tickers)

    drop_cols = [c for c in ["sentiment_score", "analyst_count", "analyst_buy", "analyst_hold", "analyst_sell"]
                 if c in screened.columns]
    if drop_cols:
        screened = screened.drop(drop_cols)

    screened = screened.with_columns(
        pl.col("ticker").map_elements(
            lambda t: sentiment_data.get(t, {}).get("sentiment_score", 0.0),
            return_dtype=pl.Float64
        ).alias("sentiment_score"),
        pl.col("ticker").map_elements(
            lambda t: sentiment_data.get(t, {}).get("total_analysts", 0),
            return_dtype=pl.Int64
        ).alias("analyst_count"),
        pl.col("ticker").map_elements(
            lambda t: sentiment_data.get(t, {}).get("strongBuy", 0) + sentiment_data.get(t, {}).get("buy", 0),
            return_dtype=pl.Int64
        ).alias("analyst_buy"),
        pl.col("ticker").map_elements(
            lambda t: sentiment_data.get(t, {}).get("hold", 0),
            return_dtype=pl.Int64
        ).alias("analyst_hold"),
        pl.col("ticker").map_elements(
            lambda t: sentiment_data.get(t, {}).get("sell", 0) + sentiment_data.get(t, {}).get("strongSell", 0),
            return_dtype=pl.Int64
        ).alias("analyst_sell"),
    )

    screened = apply_blended_targets(screened, analyst_price_targets)

    print("\n[7/7] Final scoring + sector cap...")
    scored = apply_dynamic_scores(screened, weights)

    if "upside_pct" in scored.columns:
        pre_filter = scored.height
        scored = scored.filter(pl.col("upside_pct") >= 0.0)
        filtered_out = pre_filter - scored.height
        if filtered_out > 0:
            print(f"  Removed {filtered_out} names with negative upside (long-only gate)")

    scored = apply_sector_cap(scored, MAX_PER_SECTOR, MAX_RESULTS)
    print(f"  Top {scored.height} scored and ranked (with blended targets, sector-capped)")

    if scored.height > 0:
        sectors = scored["sector"].to_list()
        from collections import Counter
        sector_dist = Counter(s if s else "Unknown" for s in sectors)
        print("  Sector distribution:")
        for sect, count in sector_dist.most_common():
            print(f"    {sect}: {count}")

    output_dir = Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "long_screener_results.csv"
    scored.write_csv(str(csv_path))
    print(f"  Results CSV: {csv_path.resolve()}")

    if screened.height > 0:
        universe_csv = output_dir / "screener_universe.csv"
        full_scored = apply_dynamic_scores(screened, weights)
        full_scored.write_csv(str(universe_csv))
        print(f"  Full universe CSV: {universe_csv.resolve()} ({full_scored.height} tickers)")

    import json as _json
    factors_path = output_dir / "factor_signals.json"
    factors_path.write_text(_json.dumps({
        "timestamp": run_time,
        "factors": factors,
        "weights": weights,
        "baseline_weights": baseline_weights,
        "market_bias": market_bias,
    }, indent=2, default=str))
    print(f"  Factor signals: {factors_path.resolve()}")

    html = generate_html_report(factors, weights, baseline_weights, scored, run_time,
                                industry_medians=industry_medians, market_bias=market_bias)
    html_path = output_dir / "dynamic_factor_screener.html"
    html_path.write_text(html)
    print(f"  HTML report: {html_path.resolve()}")

    if __name__ == "__main__" and not _is_notebook():
        import http.server
        import socket

        serve_dir = str(output_dir.resolve())
        port = 8080
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(*args, directory=serve_dir, **kwargs)

        class ReusableHTTPServer(http.server.HTTPServer):
            allow_reuse_address = True
            def server_bind(self):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                super().server_bind()

        try:
            server = ReusableHTTPServer(("0.0.0.0", port), handler)
        except OSError:
            port = 8081
            server = ReusableHTTPServer(("0.0.0.0", port), handler)

        print(f"\n  Open in browser: http://localhost:{port}/{html_path.name}")
        print(f"  Serving on port {port}... (Ctrl+C to stop)")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            server.shutdown()
    else:
        print(f"\n  Done. Open {html_path.name} in a browser to view results.")

    return scored, factors


def _is_notebook():
    try:
        from IPython import get_ipython
        shell = get_ipython().__class__.__name__
        return shell == 'ZMQInteractiveShell'
    except Exception:
        return False


if __name__ == "__main__":
    scored, factors = main()
