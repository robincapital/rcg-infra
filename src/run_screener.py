"""
run_screener.py  —  RCG Screener Entry Point  (v2 — price_targets integrated)
==============================================================================
Manual controls for sentiment override + filter toggles + price target engine.
Edit the CONFIG section, then exec() this from Jupyter.

CHANGES in v2 (2026-04-28):
  - Added USE_NEW_PRICE_TARGETS feature flag for price_targets.py engine
  - Added PT_APPLY_ENVELOPE, PT_R2_FLOOR, PT_R2_FULL tuning controls
  - Added BLOOMBERG_INTRADAY_MAX (Phase 5 prep — not yet wired downstream)
  - Monkeypatches fetch_analyst_price_targets to capture numberOfAnalysts
    from Finnhub (so Gate B can engage without n_analysts heuristic)
  - Patches compute_target_price_and_upside + apply_blended_targets to use
    the new shared engine when USE_NEW_PRICE_TARGETS=True

Reverting to legacy: set USE_NEW_PRICE_TARGETS = False. No file edits required.
"""

import sys

# Clear cached imports
for mod in list(sys.modules.keys()):
    if 'screener' in mod or mod == 'price_targets':
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

# -- Price Target Engine v2 (price_targets.py) --
USE_NEW_PRICE_TARGETS  = True    # master switch — set False to revert to legacy engine
PT_APPLY_ENVELOPE      = True    # Gate B: clip extreme model PTs to analyst band
PT_R2_FLOOR            = 0.20    # Gate A: drop models with R² < floor
PT_R2_FULL             = 0.40    # full conviction at and above this R²

# -- Bloomberg Intraday (Phase 5 prep — not yet consumed) --
BLOOMBERG_INTRADAY_MAX = 50      # cap on intraday tickers; staged for dynamic watchlist
# -- Phase 2A signal capture --
CAPTURE_SIGNALS        = True    # write all signals to rcg_signals Postgres DB
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

# ══════════════════════════════════════════════════════════════════
#  PRICE TARGET ENGINE v2 — price_targets.py integration
# ══════════════════════════════════════════════════════════════════
if USE_NEW_PRICE_TARGETS:
    try:
        import price_targets as pt_engine
        pt_engine.R2_HARD_FLOOR  = PT_R2_FLOOR
        pt_engine.R2_FULL_WEIGHT = PT_R2_FULL

        # ── Capture Finnhub numberOfAnalysts on the price-target fetch ─────
        # The legacy screener.fetch_analyst_price_targets discards Finnhub's
        # numberOfAnalysts field. We need it for Gate B to know whether to
        # engage the envelope clip. Wrap the function and add the field.
        import requests as _pt_requests, time as _pt_time
        _orig_fetch_pt = screener.fetch_analyst_price_targets
        def _patched_fetch_pt(tickers):
            api_key = screener.get_finnhub_api_key()
            if not api_key:
                return _orig_fetch_pt(tickers)
            results = {}
            base_url = "https://finnhub.io/api/v1/stock/price-target"
            success = 0
            for i, ticker in enumerate(tickers):
                try:
                    resp = _pt_requests.get(base_url,
                        params={"symbol": ticker, "token": api_key}, timeout=5)
                    if resp.status_code == 429:
                        _pt_time.sleep(1.5)
                        resp = _pt_requests.get(base_url,
                            params={"symbol": ticker, "token": api_key}, timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data and data.get("targetMean"):
                            results[ticker] = {
                                "target_high":         data.get("targetHigh"),
                                "target_low":          data.get("targetLow"),
                                "target_mean":         data.get("targetMean"),
                                "target_median":       data.get("targetMedian"),
                                "number_of_analysts":  data.get("numberOfAnalysts", 0),
                                "last_updated":        data.get("lastUpdated", ""),
                            }
                            success += 1
                except Exception:
                    pass
                if (i + 1) % 55 == 0:
                    _pt_time.sleep(5)
                elif i < len(tickers) - 1:
                    _pt_time.sleep(0.3)
            print(f"  Finnhub price targets: {success}/{len(tickers)} tickers with data "
                  f"(numberOfAnalysts captured)")
            return results
        screener.fetch_analyst_price_targets = _patched_fetch_pt

        # ── Per-ticker model engine — replaces compute_target_price_and_upside ─
        # Called inside screen_stocks. No analyst data available here yet, so
        # envelope is OFF at this stage. Envelope engages later in the pipeline,
        # in apply_blended_targets, where analyst data is in scope.
        _orig_compute_pt = screener.compute_target_price_and_upside
        def _patched_compute_pt(ebitda_series, debt_series, fcf_series,
                                  marketcap, last_price, cash_on_hand=0.0,
                                  shares_diluted=None, revenue_series=None,
                                  sector=None):
            return pt_engine.screener_compat(
                ebitda_series   = ebitda_series,
                debt_series     = debt_series,
                fcf_series      = fcf_series,
                marketcap       = marketcap,
                last_price      = last_price,
                cash_on_hand    = cash_on_hand,
                shares_diluted  = shares_diluted,
                revenue_series  = revenue_series,
                sector          = sector,
                fed_target_rate = FED_TARGET_RATE,
                fed_neutral_rate= FED_NEUTRAL_RATE,
                analyst_target  = None,
                n_analysts      = 0,
                apply_envelope  = False,
            )
        screener.compute_target_price_and_upside = _patched_compute_pt

        # ── Post-fetch envelope pass — replaces apply_blended_targets ──────
        # Runs after Finnhub analyst data is fetched. Engages Gate B clip.
        _orig_apply_blended = screener.apply_blended_targets
        def _patched_apply_blended(screened, analyst_price_targets):
            if screened.height == 0:
                return _orig_apply_blended(screened, analyst_price_targets)
            import json as _json_pt
            import polars as _pl

            blended_pts, upside_pcts, upside_scs = [], [], []
            analyst_means, div_flags, sources = [], [], []
            new_pt_details = []
            envelope_clips     = 0
            envelope_flags     = 0
            envelope_fallbacks = 0

            for i in range(screened.height):
                row = screened.row(i, named=True)
                ticker     = row["ticker"]
                last_price = row.get("last_price")
                internal_tp = row.get("internal_target")
                pt_detail   = _json_pt.loads(row.get("pt_detail_json", "{}"))
                analyst_d   = analyst_price_targets.get(ticker, {})
                atarget     = analyst_d.get("target_mean")

                # Prefer the price-target endpoint's numberOfAnalysts (now
                # captured), fall back to the recommendation endpoint's
                # analyst_count, fall back to a 3-analyst heuristic when
                # target_mean exists. Last resort defends Gate B engagement.
                n_analysts = int(analyst_d.get("number_of_analysts") or 0)
                if n_analysts < 3:
                    n_analysts = int(row.get("analyst_count") or 0)
                if n_analysts < 3 and atarget and atarget > 0:
                    n_analysts = 3   # heuristic: Finnhub price-target endpoint
                                     # generally only returns data for covered names

                # Decide pt_source. Three pathways:
                #   (1) model PT exists + analyst exists → envelope_to_consensus decides
                #   (2) model PT exists, no analyst → publish model as "M"
                #   (3) model PT is None (all models dropped) but analyst exists → fallback "A"
                #   (4) no model, no analyst → "N/A"
                if PT_APPLY_ENVELOPE and atarget and atarget > 0 and last_price and last_price > 0:
                    if internal_tp and internal_tp > 0:
                        # (1) Normal envelope path
                        final_pt, src, flagged = pt_engine.envelope_to_consensus(
                            internal_pt   = float(internal_tp),
                            analyst_target= float(atarget),
                            n_analysts    = n_analysts,
                            last_price    = float(last_price),
                        )
                        if src == "M⚠clip":
                            envelope_clips += 1
                            gates = pt_detail.get("gates_fired", []) or []
                            if "ENVELOPE_CLIPPED_TO_CONSENSUS" not in gates:
                                gates.append("ENVELOPE_CLIPPED_TO_CONSENSUS")
                            pt_detail["gates_fired"] = gates
                            pt_detail["pt_source"]   = src
                            pt_detail["raw_pt"]      = float(internal_tp)
                        elif src == "M*":
                            envelope_flags += 1
                    elif n_analysts >= 3:
                        # (3) Fallback to analyst — model returned None,
                        # analyst data is usable. Same outcome as
                        # compute_target_price's built-in fallback path.
                        final_pt = float(atarget)
                        src      = "A"
                        flagged  = False
                        envelope_fallbacks += 1
                        gates = pt_detail.get("gates_fired", []) or []
                        if "FALLBACK_TO_ANALYST_CONSENSUS" not in gates:
                            gates.append("FALLBACK_TO_ANALYST_CONSENSUS")
                        pt_detail["gates_fired"] = gates
                        pt_detail["pt_source"]   = "A"
                    else:
                        # No model + insufficient analyst coverage
                        final_pt = None
                        src      = "N/A"
                        flagged  = False
                else:
                    # (2) Model exists but no analyst data — or envelope OFF.
                    # Or (4): nothing usable at all.
                    final_pt = internal_tp
                    src      = "M" if internal_tp else "N/A"
                    flagged  = False

                if final_pt and last_price and last_price > 0:
                    upside_pct = (final_pt / last_price) - 1.0
                    if upside_pct > 0:
                        upside_sc = float(min((upside_pct ** 0.5) * 0.7, 2.0))
                    else:
                        upside_sc = float(max(upside_pct, -1.0))
                else:
                    upside_pct, upside_sc = 0.0, 0.0

                blended_pts.append(round(final_pt, 2) if final_pt else None)
                upside_pcts.append(round(upside_pct, 4))
                upside_scs.append(round(upside_sc, 4))
                analyst_means.append(atarget)
                div_flags.append(flagged)
                sources.append(src)
                new_pt_details.append(_json_pt.dumps(pt_detail))

            drop_cols = [c for c in ["target_price", "upside_pct", "upside_score",
                                       "analyst_target_mean", "analyst_divergence_flag",
                                       "pt_source", "pt_detail_json"]
                         if c in screened.columns]
            if drop_cols:
                screened = screened.drop(drop_cols)

            screened = screened.with_columns([
                _pl.Series("target_price",            blended_pts),
                _pl.Series("upside_pct",              upside_pcts),
                _pl.Series("upside_score",            upside_scs),
                _pl.Series("analyst_target_mean",     analyst_means),
                _pl.Series("analyst_divergence_flag", div_flags),
                _pl.Series("pt_source",               sources),
                _pl.Series("pt_detail_json",          new_pt_details),
            ])

            print(f"[PT-ENGINE] Gate B applied: "
                  f"{envelope_clips} clipped (M⚠clip), "
                  f"{envelope_flags} flagged (M*), "
                  f"{envelope_fallbacks} analyst fallback (A)")
            return screened
        screener.apply_blended_targets = _patched_apply_blended

        print(f"[PT-ENGINE] price_targets.py active "
              f"(R²-floor={PT_R2_FLOOR}, full={PT_R2_FULL}, "
              f"envelope={'ON' if PT_APPLY_ENVELOPE else 'OFF'})")

    except ImportError as e:
        print(f"[PT-ENGINE] price_targets.py not found — using legacy engine. ({e})")
        USE_NEW_PRICE_TARGETS = False
else:
    print("[PT-ENGINE] USE_NEW_PRICE_TARGETS=False — using legacy engine")

# ══════════════════════════════════════════════════════════════════
#  SENTIMENT WEIGHT SHIFT
# ══════════════════════════════════════════════════════════════════
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
                ("PT v2", USE_NEW_PRICE_TARGETS),
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
print(f"  Price Target Engine: {'v2 (price_targets.py)' if USE_NEW_PRICE_TARGETS else 'LEGACY'}")

# ══════════════════════════════════════════════════════════════════
#  PHASE 2A: SIGNAL CAPTURE — captures every signal to rcg_signals DB
# ══════════════════════════════════════════════════════════════════
if CAPTURE_SIGNALS:
    try:
        import screener_capture_patch as cap
        cap.install(
            screener_module = screener,
            config = {
                "sentiment_override": SENTIMENT_OVERRIDE,
                "override_factor":    OVERRIDE_FACTOR,
                "cap_preset":         CAP_PRESET,
                "use_new_pt":         USE_NEW_PRICE_TARGETS,
                "pt_envelope":        PT_APPLY_ENVELOPE,
                "pt_r2_floor":        PT_R2_FLOOR,
                "pt_r2_full":         PT_R2_FULL,
                "excl_adrs":          EXCL_ADRS,
                "excl_biotech":       EXCL_BIOTECH,
                "sector_cap":         SECTOR_CAP,
                "max_per_sector":     MAX_PER_SECTOR,
                "fed_target_rate":    FED_TARGET_RATE,
            },
        )
        print("[CAPTURE] signals_db hooks installed")
    except Exception as e:
        print(f"[CAPTURE] install failed (run will continue without capture): {e}")

# ── Run ───────────────────────────────────────────────────────────
result = screener.main(
    market_cap_preset = CAP_PRESET,
    fed_target_rate   = FED_TARGET_RATE,
    fed_neutral_rate  = FED_NEUTRAL_RATE,
)
