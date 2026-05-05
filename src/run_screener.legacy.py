"""
run_screener.py  —  RCG Screener Entry Point
=============================================
Manual controls for sentiment override + filter toggles.
Edit the CONFIG section, then exec() this from Jupyter.
"""

import sys

# Clear cached imports
for mod in list(sys.modules.keys()):
    if 'screener' in mod:
        del sys.modules[mod]

sys.path.insert(0, '/home/nixos/Prod/V1/src')
import dynamic_factor_screener_v3 as screener

# ══════════════════════════════════════════════════════════════════
#  CONFIG — CHANGE THESE BEFORE EACH RUN
# ══════════════════════════════════════════════════════════════════

# -- Sentiment Override --
SENTIMENT_OVERRIDE = "BEARISH"   # "BULLISH" | "BEARISH" | "NEUTRAL" | None
OVERRIDE_FACTOR    = 0.75        # 0.0 = no effect  |  1.0 = full force

# -- Filters --
EXCL_ADRS          = True        # Exclude ADRs
EXCL_BIOTECH       = True        # Exclude biotech/pharma
DEBT_COVERAGE      = True        # Require cash+FCF covers >= 50% net debt
SECTOR_CAP         = True        # Cap names per sector
MAX_PER_SECTOR     = 5           # Max names per sector (only if SECTOR_CAP=True)

# -- Market Cap --
# "all"=$0.5B-$200B | "small"=$0.5B-$2B | "mid"=$2B-$10B | "large"=$10B-$200B | "custom"
CAP_PRESET         = "all"
CAP_MIN            = 500e6       # Used only if CAP_PRESET = "custom"
CAP_MAX            = 200e9       # Used only if CAP_PRESET = "custom"

# -- Fed Rates --
FED_TARGET_RATE    = 0.03625
FED_NEUTRAL_RATE   = 0.0300

# ══════════════════════════════════════════════════════════════════

OVERRIDE_WEIGHT_SHIFT = {
    "BULLISH": {
        "revenue_trend":    0.04,  "ebitda_trend":     0.06,
        "fcf_trend":       -0.02,  "debt_trend":      -0.06,
        "price_momentum":   0.14,  "rsi_score":        0.08,
        "sma_cross_score":  0.10,  "upside_score":    -0.04,
        "sentiment_score":  0.06,
    },
    "BEARISH": {
        "revenue_trend":    0.02,  "ebitda_trend":     0.06,
        "fcf_trend":        0.14,  "debt_trend":       0.16,
        "price_momentum":  -0.14,  "rsi_score":       -0.10,
        "sma_cross_score": -0.08,  "upside_score":     0.12,
        "sentiment_score":  0.02,
    },
    "NEUTRAL": {
        "revenue_trend":    0.0,   "ebitda_trend":     0.0,
        "fcf_trend":        0.0,   "debt_trend":       0.0,
        "price_momentum":   0.0,   "rsi_score":        0.0,
        "sma_cross_score":  0.0,   "upside_score":     0.0,
        "sentiment_score":  0.0,
    },
}
OVERRIDE_SENTIMENT_SCORE = {"BULLISH": 1.0, "NEUTRAL": 0.0, "BEARISH": -1.0}

# ── Apply market cap constants directly to module ─────────────────
CAP_RANGES = {
    "all":    (500e6,   200e9),
    "small":  (500e6,   2e9),
    "mid":    (2e9,     10e9),
    "large":  (10e9,    200e9),
    "custom": (CAP_MIN, CAP_MAX),
}
cap_min, cap_max = CAP_RANGES.get(CAP_PRESET, CAP_RANGES["all"])
screener.MARKET_CAP_MIN = cap_min
screener.MARKET_CAP_MAX = cap_max

# ── Sector cap + debt coverage directly on module ─────────────────
if not DEBT_COVERAGE:
    screener.MIN_DEBT_COVERAGE = 0.0

if SECTOR_CAP:
    screener.MAX_PER_SECTOR = MAX_PER_SECTOR
else:
    screener.MAX_PER_SECTOR = 9999   # effectively unlimited

# ── ADR / Biotech filter patch ────────────────────────────────────
_orig_screen = screener.screen_stocks
def _patched_screen(sf1, equity_prices, adr_tickers=None, biotech_tickers=None,
                    sentiment_data=None, sector_map=None, industry_medians=None):
    return _orig_screen(
        sf1, equity_prices,
        adr_tickers      = adr_tickers     if EXCL_ADRS    else None,
        biotech_tickers  = biotech_tickers if EXCL_BIOTECH else None,
        sentiment_data   = sentiment_data,
        sector_map       = sector_map,
        industry_medians = industry_medians,
    )
screener.screen_stocks = _patched_screen

# ── Sentiment weight shift ────────────────────────────────────────
if SENTIMENT_OVERRIDE:
    base_sent = OVERRIDE_SENTIMENT_SCORE[SENTIMENT_OVERRIDE] * OVERRIDE_FACTOR
    wt_shifts = OVERRIDE_WEIGHT_SHIFT[SENTIMENT_OVERRIDE]

    print(f"[OVERRIDE] {SENTIMENT_OVERRIDE}  factor={OVERRIDE_FACTOR:.0%}  sent={base_sent:+.2f}")

    _orig_weights = screener.compute_dynamic_weights
    def _patched_weights(factors):
        weights = _orig_weights(factors)
        for criterion, shift in wt_shifts.items():
            if criterion in weights:
                weights[criterion] += shift * OVERRIDE_FACTOR
        weights = {k: max(v, 0.03) for k, v in weights.items()}
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}
        return weights
    screener.compute_dynamic_weights = _patched_weights

    _orig_fetch = screener.fetch_analyst_sentiment
    def _patched_fetch(tickers):
        data = _orig_fetch(tickers)
        for t in data:
            live = data[t].get("sentiment_score", 0.0)
            data[t]["sentiment_score"] = round(
                live * (1 - OVERRIDE_FACTOR) + base_sent * OVERRIDE_FACTOR, 4
            )
        return data
    screener.fetch_analyst_sentiment = _patched_fetch

    _orig_bias = screener.compute_market_bias
    def _patched_bias(factors):
        bias = _orig_bias(factors)
        new_score = round(
            bias.get("score", 0.0) * (1 - OVERRIDE_FACTOR)
            + OVERRIDE_SENTIMENT_SCORE[SENTIMENT_OVERRIDE] * OVERRIDE_FACTOR, 4
        )
        bias["score"]              = new_score
        bias["sentiment_override"] = SENTIMENT_OVERRIDE
        bias["override_factor"]    = OVERRIDE_FACTOR
        if new_score > 0.15:    bias["label"] = "BUY"
        elif new_score < -0.15: bias["label"] = "SELL"
        else:                   bias["label"] = "NEUTRAL"
        return bias
    screener.compute_market_bias = _patched_bias

    _orig_render = screener.generate_html_report
    def _patched_render(*args, **kwargs):
        html = _orig_render(*args, **kwargs)
        ov_color = "#22c55e" if SENTIMENT_OVERRIDE == "BULLISH" else (
                   "#ef4444" if SENTIMENT_OVERRIDE == "BEARISH" else "#c8a84e")
        ov_bg    = "#021a0e" if SENTIMENT_OVERRIDE == "BULLISH" else (
                   "#1c0000" if SENTIMENT_OVERRIDE == "BEARISH" else "#1a1500")
        bull_shifts = [(k,v) for k,v in wt_shifts.items() if v*OVERRIDE_FACTOR > 0.01]
        bear_shifts = [(k,v) for k,v in wt_shifts.items() if v*OVERRIDE_FACTOR < -0.01]
        up_tags   = " ".join(
            f'<span style="color:#22c55e;font-size:0.65rem;margin-right:0.5rem;">'
            f'+ {k.replace("_score","").replace("_trend","")}</span>'
            for k,v in sorted(bull_shifts, key=lambda x:-abs(x[1]))[:4])
        down_tags = " ".join(
            f'<span style="color:#ef4444;font-size:0.65rem;margin-right:0.5rem;">'
            f'- {k.replace("_score","").replace("_trend","")}</span>'
            for k,v in sorted(bear_shifts, key=lambda x:-abs(x[1]))[:4])
        filter_tags = "".join(
            f'<span style="font-size:0.6rem;color:{"#22c55e" if active else "#64748b"};'
            f'margin-right:0.6rem;font-family:\'JetBrains Mono\',monospace;">'
            f'{"+" if active else "x"} {label}</span>'
            for label, active in [
                ("ADR excl", EXCL_ADRS), ("Biotech excl", EXCL_BIOTECH),
                ("Debt gate", DEBT_COVERAGE), (f"Sec cap {MAX_PER_SECTOR}", SECTOR_CAP),
                (f"Cap: {CAP_PRESET}", True),
            ]
        )
        ov_banner = (
            f'<div style="background:{ov_bg};border:2px solid {ov_color};'
            f'border-radius:10px;padding:0.85rem 1.5rem;margin-bottom:1rem;'
            f'display:flex;align-items:center;gap:2rem;flex-wrap:wrap;">'
            f'<div>'
            f'<div style="font-size:0.55rem;color:{ov_color};text-transform:uppercase;'
            f'letter-spacing:0.12em;font-weight:700;margin-bottom:0.2rem;">MANUAL OVERRIDE ACTIVE</div>'
            f'<div style="font-size:2rem;font-weight:800;color:{ov_color};'
            f'font-family:\'JetBrains Mono\',monospace;line-height:1;">{SENTIMENT_OVERRIDE}</div>'
            f'</div>'
            f'<div>'
            f'<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.2rem;">Strength</div>'
            f'<div style="font-size:1.4rem;font-weight:700;color:{ov_color};'
            f'font-family:\'JetBrains Mono\',monospace;">{OVERRIDE_FACTOR:.0%}</div>'
            f'</div>'
            f'<div>'
            f'<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.2rem;">Sent Score</div>'
            f'<div style="font-size:1.4rem;font-weight:700;color:{ov_color};'
            f'font-family:\'JetBrains Mono\',monospace;">{base_sent:+.2f}</div>'
            f'</div>'
            f'<div style="flex:1;min-width:180px;">'
            f'<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.3rem;'
            f'text-transform:uppercase;letter-spacing:0.06em;">Weight Shifts</div>'
            f'<div>{up_tags}</div><div style="margin-top:0.2rem;">{down_tags}</div>'
            f'</div>'
            f'<div style="min-width:220px;">'
            f'<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.3rem;'
            f'text-transform:uppercase;letter-spacing:0.06em;">Active Filters</div>'
            f'<div>{filter_tags}</div>'
            f'</div>'
            f'</div>'
        )
        html = html.replace(
            '<div style="background:', ov_banner + '\n    <div style="background:', 1
        )
        return html
    screener.generate_html_report = _patched_render

# ── Print config summary ──────────────────────────────────────────
print(f"  Cap Filter         : {CAP_PRESET.upper()} (${cap_min/1e9:.1f}B – ${cap_max/1e9:.0f}B)")
print(f"  Excl. ADRs         : {'ON' if EXCL_ADRS else 'OFF'}")
print(f"  Excl. Biotech      : {'ON' if EXCL_BIOTECH else 'OFF'}")
print(f"  Debt Coverage Gate : {'ON' if DEBT_COVERAGE else 'OFF'}")
print(f"  Sector Cap         : {'ON (max ' + str(MAX_PER_SECTOR) + '/sector)' if SECTOR_CAP else 'OFF'}")

# ── Run ───────────────────────────────────────────────────────────
result = screener.main(
    market_cap_preset = CAP_PRESET,
    fed_target_rate   = FED_TARGET_RATE,
    fed_neutral_rate  = FED_NEUTRAL_RATE,
)
