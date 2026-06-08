"""
price_targets.py  —  RCG Shared Price Target Engine
====================================================
Single source of truth for fundamental price targets across:
  - dynamic_factor_screener_v3.py  (screener)
  - rcg_report.py                  (per-ticker analyst report)

Replaces:
  - screener.compute_target_price_and_upside()
  - screener.compute_blended_target()       (was a no-op)
  - report.compute_v3_target_price()

Adds two gates that BOTH legacy engines were missing:
  Gate A  R² floor on per-model conviction
            R² < 0.20      → drop the model entirely
            0.20 ≤ R² <0.40 → linear ramp 0→1 on conviction
            R² ≥ 0.40       → full conviction formula
  Gate B  Analyst-consensus envelope
            divergence > 75% of price (n_analysts ≥ 3) → clip model PT
            into [analyst × 0.50, analyst × 1.50] band

Preserves the report's quality machinery already in production:
  - quality-score haircut on final blended PT
  - growth-adjusted EV/Rev anchor (0.6x – 2.2x scalar)
  - mean-reversion cap (low-quality names cannot exceed sector anchor)
  - 2.5× sector ceiling on EV/Rev for healthy names
  - Emerging Growth Model 4 (TAM-discounted projection)

Preserves the screener's rate compression (Fed-rate-conditional sector multiples).
The report didn't have this; the consolidated engine applies it to both.

Public API:
  compute_target_price(...)        → returns TargetPriceResult dataclass
  envelope_to_consensus(...)       → Gate B clipping helper
  screener_compat(...)             → drop-in replacement for screener's
                                      compute_target_price_and_upside,
                                      returns (pt, upside_pct, upside_score, pt_detail)

Author: RCG / Nick Diaz
Version: 1.0  (2026-04-28)
  · v28.x  TAM model (Change D), growth overrides, scale/EPS-decay gates
  · v29.x  2Q-smoothing, fallback ladder, EPS-decay flag
  · v32    DCF model (Change E) + user price-target blend / pin (Change F)
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence, Tuple, Dict, Any


# ============================================================
# CONFIG — keep aligned with screener.SECTOR_MULTIPLES
# ============================================================
SECTOR_MULTIPLES = {
    "Technology":             {"ev_ebitda": 18.0, "ev_rev": 4.5, "fcf_yield": 0.035, "rate_sensitivity": 0.10},
    "Communication Services": {"ev_ebitda": 14.0, "ev_rev": 3.5, "fcf_yield": 0.040, "rate_sensitivity": 0.09},
    "Consumer Discretionary": {"ev_ebitda": 13.0, "ev_rev": 1.5, "fcf_yield": 0.040, "rate_sensitivity": 0.07},
    "Consumer Staples":       {"ev_ebitda": 12.0, "ev_rev": 1.2, "fcf_yield": 0.045, "rate_sensitivity": 0.04},
    "Healthcare":             {"ev_ebitda": 14.0, "ev_rev": 3.0, "fcf_yield": 0.040, "rate_sensitivity": 0.05},
    "Industrials":            {"ev_ebitda": 11.0, "ev_rev": 1.8, "fcf_yield": 0.045, "rate_sensitivity": 0.05},
    "Materials":              {"ev_ebitda":  9.0, "ev_rev": 1.4, "fcf_yield": 0.050, "rate_sensitivity": 0.04},
    "Real Estate":            {"ev_ebitda": 16.0, "ev_rev": 5.0, "fcf_yield": 0.055, "rate_sensitivity": 0.12},
    "Energy":                 {"ev_ebitda":  7.0, "ev_rev": 1.2, "fcf_yield": 0.060, "rate_sensitivity": 0.02},
    "Utilities":              {"ev_ebitda": 10.0, "ev_rev": 2.5, "fcf_yield": 0.055, "rate_sensitivity": 0.08},
    "Financials":             {"ev_ebitda": 12.0, "ev_rev": 2.5, "fcf_yield": 0.050, "rate_sensitivity": 0.02},
    "Financial Services":     {"ev_ebitda": 12.0, "ev_rev": 2.5, "fcf_yield": 0.050, "rate_sensitivity": 0.02},
    "Basic Materials":        {"ev_ebitda":  9.0, "ev_rev": 1.4, "fcf_yield": 0.050, "rate_sensitivity": 0.04},
    "_default":               {"ev_ebitda": 12.0, "ev_rev": 2.0, "fcf_yield": 0.045, "rate_sensitivity": 0.05},
}

# ─── v28.8 — Sector TAM caps (Change D) ──────────────────────────────────
# Maximum reasonable total-addressable-market size by sector. Catches typos
# (a user entering "10000" when they meant "100"). MM-set ceilings; raise
# specific sector if you have a concrete case that exceeds.
SECTOR_TAM_CAP_BILLIONS = {
    "Technology":             10_000.0,   # $10T
    "Communication Services":  5_000.0,   # $5T
    "Consumer Discretionary":  5_000.0,
    "Financials":              5_000.0,
    "Financial Services":      5_000.0,
    "Consumer Staples":        3_000.0,   # $3T
    "Industrials":             3_000.0,
    "Real Estate":             2_000.0,   # $2T
    "Healthcare":              1_000.0,   # $1T
    "Utilities":               1_000.0,
    "Materials":               1_000.0,
    "Basic Materials":         1_000.0,
    "Energy":                    500.0,   # $500B
    "_default":                1_000.0,
}

# ─── v28.8 — TAM model constants (Change D) ──────────────────────────────
# These are fixed (not user-set). Penetration / FCF margin / TAM are per
# ticker; exit multiple is derived from sector_fcf_yield.
TAM_YEARS_TO_MATURITY  = 5
TAM_DISCOUNT_RATE      = 0.10
TAM_PENETRATION_CAP    = 0.20   # 20% max — no name owns >20% of a major TAM
TAM_FCF_MARGIN_CAP     = 0.40   # 40% max — NVDA/Visa ceiling
TAM_PENETRATION_DEFAULT = 0.10
TAM_FCF_MARGIN_DEFAULT  = 0.20

# ─── v32 — DCF model constants (Change E) ────────────────────────────────
# Two-stage discounted-cash-flow model (6th model). Stage-1 FCF grows off a
# trailing-annualized base at a (decaying) growth rate over DCF_HORIZON_YEARS,
# then a Gordon-growth terminal value. Discounted at a macro WACC that floats
# with the Fed spread so the model tightens when rates are high — same spirit
# as the sector-multiple rate compression the other models use.
DCF_BASE_WACC          = 0.090   # neutral-rate WACC anchor
DCF_TERMINAL_GROWTH    = 0.025   # perpetual growth after the explicit horizon
DCF_HORIZON_YEARS      = 5
DCF_WACC_FLOOR         = 0.070
DCF_WACC_CEIL          = 0.150
DCF_MAX_STAGE1_GROWTH  = 0.30    # clamp the year-1 growth rate (no hyper-growth)
DCF_MIN_STAGE1_GROWTH  = -0.10
DCF_WACC_GTERM_GAP_MIN = 0.030   # keep (wacc - g_term) >= this so TV can't blow up
DCF_ABS_CAP_MULT       = 4.0     # PT cap as multiple of last_price (sanity)

# Gate A — R² floor on conviction.
R2_HARD_FLOOR    = 0.20   # below this, the model is killed
R2_FULL_WEIGHT   = 0.40   # at and above this, full conviction formula

# Gate B — analyst-consensus envelope.
ANALYST_DIVERGENCE_FLAG_THRESHOLD   = 0.40   # M*  flag (cosmetic)
ANALYST_DIVERGENCE_SEVERE_THRESHOLD = 0.75   # severe — clip
ANALYST_BAND_LOW                    = 0.50
ANALYST_BAND_HIGH                   = 1.50
ANALYST_MIN_N                       = 3      # min analysts to engage envelope

# Projection horizon (forward quarters).
PROJECTION_QUARTERS = 4


# ============================================================
# RESULT TYPE
# ============================================================
@dataclass
class TargetPriceResult:
    """Canonical return type for the shared engine."""

    target_price:    Optional[float] = None     # final blended, after gates + haircut
    raw_target:      Optional[float] = None     # before haircut, before envelope
    upside_pct:      float           = 0.0      # (target / last_price) - 1
    upside_score:    float           = 0.0      # screener composite score input
    pt_source:       str             = "N/A"    # "M","M✓","M*","M⚠clip","A","FB","U","N/A"
    divergence_flag: bool            = False
    quality_score:   Optional[float] = None     # 0.40 – 1.00
    quality_haircut: float           = 0.0      # 1.0 - quality_adj
    gates_fired:     list            = field(default_factory=list)
    breakdown:       dict            = field(default_factory=dict)

    def to_pt_detail(self) -> dict:
        """
        Return a dict matching the screener's existing pt_detail JSON shape so
        the HTML report's expand-panel renderer (lines 1839-1900) keeps working
        without modification.
        """
        b = self.breakdown
        return {
            "models":             b.get("models", {}),
            "conviction_weights": b.get("conviction_weights", {}),
            "blended_pt":         self.target_price,
            "raw_pt":             self.raw_target,
            "sector":             b.get("sector", "Unknown"),
            "sector_anchor":      b.get("sector_anchor", {}),
            "rate_compression":   b.get("rate_compression", 1.0),
            "rate_spread_bps":    b.get("rate_spread_bps", 0.0),
            "fed_rate":           b.get("fed_rate", 0.0),
            "dominant_model":     b.get("dominant_model", "N/A"),
            "quality_score":      self.quality_score,
            "quality_haircut":    self.quality_haircut,
            "gates_fired":        self.gates_fired,
            "growth_mult":        b.get("growth_mult"),
            "median_qoq_pct":     b.get("median_qoq_pct"),
            "ann_growth_pct":     b.get("ann_growth_pct"),
            "emerging":           b.get("emerging", False),
            "pt_source":          self.pt_source,
            "divergence_flag":    self.divergence_flag,
            # v29.6 — EPS-decay flag surfaces in the dashboard model-driver chip
            # so MM sees when a name is anchored on a decaying business
            "eps_decay_warning":  b.get("eps_decay_warning", False),
            # v32 — user price-target blend (pin/weights), when active
            "user_blend":         b.get("user_blend"),
        }


# ============================================================
# CORE STATISTICAL HELPERS
# ============================================================
def _clean(s: Optional[Sequence]) -> list:
    if not s:
        return []
    return [float(v) for v in s
            if v is not None and not (isinstance(v, float) and np.isnan(v))]


def _theil_sen(series: Sequence) -> Tuple[float, float, float]:
    """Robust trend regression. Returns (slope, intercept, r2)."""
    clean = _clean(series)
    if len(clean) < 3:
        return 0.0, 0.0, 0.0
    x = np.arange(len(clean), dtype=float)
    y = np.array(clean, dtype=float)
    slopes = []
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            if x[j] != x[i]:
                slopes.append((y[j] - y[i]) / (x[j] - x[i]))
    if not slopes:
        return 0.0, float(np.mean(y)), 0.0
    slope     = float(np.median(slopes))
    intercept = float(np.median(y) - slope * np.median(x))
    y_pred    = slope * x + intercept
    ss_res    = float(np.sum((y - y_pred) ** 2))
    ss_tot    = float(np.sum((y - np.mean(y)) ** 2))
    r2        = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, max(0.0, float(r2))


def _rolling_median(series: Sequence, window: int = 3) -> list:
    """Median-smooth a series, length-preserving."""
    out = []
    for i in range(len(series)):
        start = max(0, i - window + 1)
        win = [v for v in series[start:i + 1]
               if v is not None and not (isinstance(v, float) and np.isnan(v))]
        out.append(float(np.median(win)) if win else 0.0)
    return out


def _smooth_2q(series: Sequence) -> Optional[float]:
    """v29.0 — Conservative 2-quarter trailing mean of a numeric series.
    Returns mean of the last two non-null values, or last value if only
    one available, or None.

    Used to dampen single-quarter spikes (e.g. DELL's AI server boom)
    when computing forward-projection bases. MM directive: 'I rather
    undershoot a target than get it totally wrong.'"""
    if not series:
        return None
    clean = [v for v in series
             if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not clean:
        return None
    if len(clean) == 1:
        return float(clean[-1])
    return float((clean[-1] + clean[-2]) / 2.0)


def _model_conviction(r2: float, n: int, cv: float,
                       r2_floor: Optional[float] = None,
                       r2_full:  Optional[float] = None) -> float:
    """
    Conviction with Gate A R² floor + linear ramp.

    Below r2_floor → 0 (model is noise; will be dropped in the blend).
    Between floor and full → ramped weight (0 → full).
    At and above full → conviction = 0.50·R² + 0.30·stability + 0.20·data_score.

    NOTE: r2_floor / r2_full default to None and are read from the module
    globals R2_HARD_FLOOR / R2_FULL_WEIGHT at call time — NOT at function
    definition time. This is so callers can mutate the module constants
    (e.g. the R² floor sweep tool) and have the change take effect on
    subsequent calls without re-importing.
    """
    if r2_floor is None:
        r2_floor = R2_HARD_FLOOR
    if r2_full is None:
        r2_full = R2_FULL_WEIGHT
    if n < 3 or r2 is None:
        return 0.0
    r2 = max(0.0, float(r2))
    if r2 < r2_floor:
        return 0.0
    base = (0.50 * r2 +
            0.30 * max(0.0, 1.0 - min(cv, 2.0) / 2.0) +
            0.20 * min(1.0, n / 10.0))
    if r2 < r2_full:
        ramp = (r2 - r2_floor) / (r2_full - r2_floor)
        base *= ramp
    return float(np.clip(base, 0.0, 1.0))


# ============================================================
# RATE-CONDITIONAL SECTOR MULTIPLES
# ============================================================
def _get_sector_multiples(sector: Optional[str],
                           fed_target_rate: float,
                           fed_neutral_rate: float,
                           apply_compression: bool = True) -> dict:
    """
    Returns rate-compressed sector multiples plus raw/diagnostic values.
    Compression is multiplicative on EV multiples and inverse on FCF yield.
    """
    sm = SECTOR_MULTIPLES.get(sector, SECTOR_MULTIPLES["_default"])
    rate_spread = fed_target_rate - fed_neutral_rate
    sens        = sm["rate_sensitivity"]
    if apply_compression:
        compression = 1.0 - sens * (rate_spread / 0.01)
        compression = float(np.clip(compression, 0.5, 1.5))
    else:
        compression = 1.0
    return {
        "ev_ebitda":       sm["ev_ebitda"] * compression,
        "ev_rev":          sm["ev_rev"]    * compression,
        "fcf_yield":       sm["fcf_yield"] / compression,
        "raw":             sm,
        "compression":     round(compression, 4),
        "rate_spread_bps": round(rate_spread * 10000, 1),
    }


# ============================================================
# REVENUE GROWTH CHARACTERIZATION
# ============================================================
def _revenue_growth_stats(rev_clean: list) -> Tuple[float, float, bool, float]:
    """
    Returns (median_qoq, ann_growth, is_emerging_growth, growth_mult).
    growth_mult is the EV/Rev anchor scalar applied to standard model.
    """
    if len(rev_clean) < 4:
        return 0.0, 0.0, False, 1.0
    recent = rev_clean[-4:]
    qoq = []
    for i in range(1, len(recent)):
        if recent[i - 1] > 0:
            qoq.append((recent[i] / recent[i - 1]) - 1.0)
    if not qoq:
        return 0.0, 0.0, False, 1.0
    median_qoq = float(np.median(qoq))
    ann_growth_from_qoq = (1 + median_qoq) ** 4 - 1

    # v28.9 — Sanity-cap implied annualized growth using actual YoY growth
    # when available. QoQ × 4 over-states sustainable growth for names
    # with a single quarter spike (e.g. DELL's AI-server tailwind:
    # +23.6% QoQ → 133% annualized, but real YoY is closer to 25%).
    # If we have 5+ quarters, use the more conservative of:
    #   (a) QoQ-implied annualized: (1+median_qoq)^4 - 1
    #   (b) YoY actual: rev[-1] / rev[-5] - 1, scaled by 1.5x to allow
    #       some persistence
    if len(rev_clean) >= 5 and rev_clean[-5] is not None and rev_clean[-5] > 0:
        yoy = (rev_clean[-1] / rev_clean[-5]) - 1.0
        ann_growth = min(ann_growth_from_qoq, max(yoy * 1.5, 0.0))
    else:
        ann_growth = ann_growth_from_qoq

    # v28.7 — Change C: loosened emerging-growth trigger. Previous gate
    # (QoQ >= 25% in 2 quarters = ~144% annualized growth) was so strict
    # that even NVDA (+15.3% median QoQ ~ 76% annualized), PLTR (~70%),
    # RKLB (~55%), and SOFI (~33%) all failed to qualify. New gate fires
    # on names with median QoQ >= 5% (~22% annualized) AND at least one
    # quarter showing >= 10% growth. Excludes flat/declining names.
    high_growth_qs = sum(1 for g in qoq if g >= 0.10)
    is_emerging    = median_qoq >= 0.05 and high_growth_qs >= 1

    if   ann_growth >=  0.60: mult = 2.20
    elif ann_growth >=  0.35: mult = 1.80
    elif ann_growth >=  0.20: mult = 1.40
    elif ann_growth >=  0.10: mult = 1.15
    elif ann_growth >=  0.00: mult = 1.00
    elif ann_growth >= -0.10: mult = 0.80
    else:                     mult = 0.60
    return median_qoq, ann_growth, is_emerging, mult


# ============================================================
# QUALITY SCORE (0.40 – 1.00 multiplier on final blended PT)
# ============================================================
def _fundamental_quality_score(ebitda_series: Sequence,
                                revenue_series: Sequence,
                                fcf_series: Sequence) -> float:
    """
    Quality discount on final blended PT. 1.0 = no haircut, 0.40 = severe.
    Penalizes deteriorating fundamentals across revenue, EBITDA, FCF.
    """
    score  = 0.0
    checks = 0

    rev = _clean(revenue_series)
    if len(rev) >= 3:
        checks += 2
        slope, _, r2 = _theil_sen(rev)
        mean_abs = np.mean(np.abs(rev))
        norm_slope = (slope / mean_abs) * r2 if mean_abs > 0 else 0
        if   norm_slope >  0.02: score += 2.0
        elif norm_slope >  0.0:  score += 1.2
        elif norm_slope > -0.02: score += 0.5

    ebitda = _clean(ebitda_series)
    if len(ebitda) >= 3 and len(rev) >= 3:
        checks += 2
        positive_ratio = sum(1 for v in ebitda if v > 0) / len(ebitda)
        slope, _, r2 = _theil_sen(ebitda)
        mean_abs = np.mean(np.abs(ebitda))
        norm_slope = (slope / mean_abs) * r2 if mean_abs > 0 else 0
        if positive_ratio >= 0.75 and norm_slope > 0:
            score += 2.0
        elif positive_ratio >= 0.5:
            score += 1.0 + (0.5 if norm_slope > 0 else 0)
        elif norm_slope > 0.02:
            score += 0.5

    fcf = _clean(fcf_series)
    if len(fcf) >= 3:
        checks += 1
        pos_ratio = sum(1 for v in fcf if v > 0) / len(fcf)
        slope, _, _ = _theil_sen(fcf)
        if   pos_ratio >= 0.75:                   score += 1.0
        elif pos_ratio >= 0.50:                   score += 0.6
        elif slope > 0 and pos_ratio > 0:         score += 0.3

    if checks == 0:
        return 0.70  # no data → conservative
    quality_ratio = score / checks
    mult = 0.40 + 0.60 * quality_ratio
    return round(float(np.clip(mult, 0.40, 1.00)), 3)


# ============================================================
# PER-TICKER GROWTH OVERRIDES (user_assumptions.json)
# ============================================================
# The engine's default projection uses Theil-Sen slope over the FULL trailing
# series. Per-ticker user overrides live in src/user_assumptions.json and let
# the user replace specific projection inputs with their own forward view.
#
# Slider-midpoint baseline = trailing 6q linear-regression slope, annualized.
# (Not Theil-Sen — for the UI baseline we want OLS so the user sees a
# trend-line value that lines up with their mental model of "the last 6q's
# growth rate." Theil-Sen is the engine's robust default; OLS-6q is the
# user-facing reference point.)

_BASELINE_QUARTERS = 6


def _lr_annualized_growth(series: Sequence) -> Optional[float]:
    """
    Annualized growth rate from OLS regression slope on the last
    _BASELINE_QUARTERS values. Returns None if insufficient or non-positive mean.
    Output is a fraction (0.10 = +10% annualized).
    """
    s = _clean(series)
    if len(s) < 3:
        return None
    window = s[-_BASELINE_QUARTERS:]
    n = len(window)
    if n < 3:
        return None
    xs = np.arange(n, dtype=float)
    ys = np.asarray(window, dtype=float)
    mean_y = float(np.mean(np.abs(ys)))
    if mean_y <= 0:
        return None
    # OLS slope
    slope = float(np.cov(xs, ys, bias=True)[0, 1] / np.var(xs))
    # Convert slope (units per quarter) → annualized growth pct using mean
    return (slope * 4.0) / mean_y


def _ebitda_margin_now(ebitda_series: Sequence, revenue_series: Sequence) -> Optional[float]:
    """Most recent EBITDA / Revenue ratio, as a fraction. None if undefined."""
    e = _clean(ebitda_series); r = _clean(revenue_series)
    if not e or not r or r[-1] <= 0:
        return None
    return float(e[-1] / r[-1])


def compute_growth_baseline(*, ebitda_series, revenue_series, fcf_series, debt_series) -> dict:
    """
    Compute trailing-6q implied growth/margin baseline shown as slider centers
    in the dashboard's per-ticker Assumptions panel. Pure read of the trailing
    fundamentals — no engine state, safe to call from the server.
    """
    return {
        "rev_growth_ann_pct":      _to_pct(_lr_annualized_growth(revenue_series)),
        "fcf_growth_ann_pct":      _to_pct(_lr_annualized_growth(fcf_series)),
        "ebitda_margin_now_pct":   _to_pct(_ebitda_margin_now(ebitda_series, revenue_series)),
        # debt: paydown rate = -(slope of debt over baseline window) / latest_debt, annualized
        "debt_paydown_ann_pct":    _to_pct(
            -_lr_annualized_growth(debt_series) if _lr_annualized_growth(debt_series) is not None else None
        ),
        "window_quarters":         _BASELINE_QUARTERS,
    }


def _to_pct(x):
    """Fraction → percent rounded; None passthrough."""
    return None if x is None else round(x * 100, 2)


def _apply_growth_override(default_fwd_sum: float, latest_quarterly: float,
                            override_ann_pct: Optional[float]) -> float:
    """
    Replace a 4-quarter forward sum projection with one driven by a user-set
    annualized growth rate compounded off the latest quarterly value.

    growth_ann_pct is in PERCENT (e.g. 12.5 means +12.5%/yr). None → no change.
    """
    if override_ann_pct is None or latest_quarterly <= 0:
        return default_fwd_sum
    g = override_ann_pct / 100.0
    # Quarterly growth = (1 + g)^(1/4) - 1
    q_growth = (1.0 + g) ** 0.25 - 1.0
    total = 0.0
    v = latest_quarterly
    for _ in range(PROJECTION_QUARTERS):
        v *= (1.0 + q_growth)
        total += v
    return total


# ============================================================
# v28.8 — TAM Model (Change D)
# ============================================================
def compute_tam_model(
    *,
    tam_usd_billions:        float,
    penetration_pct:         Optional[float],
    fcf_margin_pct:          Optional[float],
    sector:                  Optional[str],
    debt_usd:                float,
    cash_usd:                float,
    shares_diluted:          float,
) -> Optional[dict]:
    """
    Explicit TAM-based valuation model (5th model alongside EV/EBITDA,
    EV/Rev, FCF Yield, Emerging Growth).

    Math:
        mature_revenue       = TAM × penetration_pct
        mature_fcf           = mature_revenue × fcf_margin_pct
        mature_equity_value  = mature_fcf × sector_fcf_multiple
        pv_equity            = mature_equity_value / (1 + r)^years
        pt_per_share         = (pv_equity - debt + cash) / shares_diluted

    Returns None when:
      - tam_usd_billions is None or <= 0  (model opted out for this name)
      - shares_diluted invalid
      - sector cap exceeded (defensive — flagged via gate)
      - resulting equity <= 0

    Otherwise returns a dict matching the existing model-output shape so
    it can blend with the rest.
    """
    if not tam_usd_billions or tam_usd_billions <= 0:
        return None
    if not shares_diluted or shares_diluted <= 0:
        return None

    # ── Apply defaults + caps ──
    pen = penetration_pct if penetration_pct is not None else TAM_PENETRATION_DEFAULT
    pen = max(0.0, min(pen, TAM_PENETRATION_CAP))
    mar = fcf_margin_pct  if fcf_margin_pct  is not None else TAM_FCF_MARGIN_DEFAULT
    mar = max(0.0, min(mar, TAM_FCF_MARGIN_CAP))

    # Sector TAM cap check — defensive against typos. If the user enters a
    # TAM larger than the sector cap, clip it (and flag for surface in the
    # report so they can see we clipped).
    sector_cap = SECTOR_TAM_CAP_BILLIONS.get(sector, SECTOR_TAM_CAP_BILLIONS["_default"])
    capped = False
    if tam_usd_billions > sector_cap:
        tam_usd_billions = sector_cap
        capped = True

    # ── Exit multiple from sector_fcf_yield ──
    # mature company trades at sector_fcf_yield ↔ exit_multiple = 1 / yield
    sm = SECTOR_MULTIPLES.get(sector, SECTOR_MULTIPLES["_default"])
    exit_fcf_mult = 1.0 / max(sm["fcf_yield"], 1e-6)

    # ── Math ──
    tam_usd            = tam_usd_billions * 1e9
    mature_revenue     = tam_usd * pen
    mature_fcf         = mature_revenue * mar
    mature_equity      = mature_fcf * exit_fcf_mult
    pv_factor          = 1.0 / ((1 + TAM_DISCOUNT_RATE) ** TAM_YEARS_TO_MATURITY)
    pv_equity          = mature_equity * pv_factor
    final_equity       = pv_equity - debt_usd + cash_usd
    if final_equity <= 0:
        return None
    pt = final_equity / shares_diluted

    return {
        "pt":                 round(pt, 2),
        # Use a high conviction (0.95) since this is an explicit MM-set
        # model — the user has decided this name needs TAM treatment.
        # Not 1.0 so it can be edged out by analyst envelope in extreme
        # divergence (per Gate B).
        "conviction":         0.95,
        "tam_usd_billions":   round(tam_usd_billions, 1),
        "tam_capped":         capped,
        "penetration_pct":    round(pen * 100, 2),
        "fcf_margin_pct":     round(mar * 100, 2),
        "exit_fcf_mult":      round(exit_fcf_mult, 2),
        "sector":             sector,
        "years_to_maturity":  TAM_YEARS_TO_MATURITY,
        "discount_rate":      round(TAM_DISCOUNT_RATE * 100, 1),
        # Pipeline of computed values (so the report can show the math)
        "mature_revenue_b":   round(mature_revenue / 1e9, 2),
        "mature_fcf_b":       round(mature_fcf / 1e9, 2),
        "mature_equity_b":    round(mature_equity / 1e9, 2),
        "pv_equity_b":        round(pv_equity / 1e9, 2),
        "final_equity_b":     round(final_equity / 1e9, 2),
    }


# ============================================================
# v32 — DCF Model (Change E)
# ============================================================
def _dcf_wacc(fed_target_rate: float, fed_neutral_rate: float) -> float:
    """Macro WACC: base anchor + Fed spread, bounded. Rising rates → higher
    discount rate → lower DCF value (mirrors the sector-multiple compression
    used by the EV models)."""
    spread = (fed_target_rate or 0.0) - (fed_neutral_rate or 0.0)
    wacc = DCF_BASE_WACC + spread
    return float(np.clip(wacc, DCF_WACC_FLOOR, DCF_WACC_CEIL))


def compute_dcf_model(
    *,
    fcf_series:        Sequence,
    revenue_series:    Sequence,
    latest_debt:       float,
    cash_on_hand:      float,
    share_count:       float,
    sector:            Optional[str],
    fed_target_rate:   float,
    fed_neutral_rate:  float,
    growth_override_ann_pct: Optional[float] = None,
) -> Optional[dict]:
    """
    Two-stage discounted-cash-flow model (6th model alongside EV/EBITDA,
    EV/Rev, FCF Yield, Emerging Growth, TAM).

    Math:
        base_fcf      = 2Q-smoothed trailing quarterly FCF × 4   (annualized)
        stage-1       = base_fcf compounded at g_t for DCF_HORIZON_YEARS,
                        where g_t decays linearly from g1 → g_term
        terminal      = FCF_N · (1 + g_term) / (wacc - g_term)
        EV            = Σ PV(stage-1 FCF) + PV(terminal)
        equity        = EV - net_debt
        pt            = equity / shares

    g1 (year-1 growth) comes from the user override when set, else the
    trailing-6q OLS revenue growth (FCF is noisier; revenue is the smoother
    driver), clamped to [DCF_MIN_STAGE1_GROWTH, DCF_MAX_STAGE1_GROWTH].

    Returns None when trailing FCF is non-positive (DCF on negative cash flow
    is meaningless — those names are valued by EV/Rev / Emerging / TAM
    instead) or when the resulting equity is non-positive.
    """
    if not share_count or share_count <= 0:
        return None

    fcf_clean = _clean(fcf_series)
    if len(fcf_clean) < 4:
        return None

    base_fcf_q = _smooth_2q(fcf_clean)
    if base_fcf_q is None or base_fcf_q <= 0:
        return None
    base_fcf_ann = base_fcf_q * 4.0

    # ── Discount rate + terminal growth ──
    wacc   = _dcf_wacc(fed_target_rate, fed_neutral_rate)
    g_term = DCF_TERMINAL_GROWTH
    # Defensive: keep a floor between wacc and g_term so the Gordon TV is finite
    if wacc - g_term < DCF_WACC_GTERM_GAP_MIN:
        wacc = g_term + DCF_WACC_GTERM_GAP_MIN

    # ── Stage-1 growth ──
    if growth_override_ann_pct is not None:
        g1 = growth_override_ann_pct / 100.0
    else:
        g1 = _lr_annualized_growth(revenue_series)
        if g1 is None:
            g1 = _lr_annualized_growth(fcf_series)
    if g1 is None:
        g1 = 0.0
    g1 = float(np.clip(g1, DCF_MIN_STAGE1_GROWTH, DCF_MAX_STAGE1_GROWTH))

    # ── Project + discount explicit horizon ──
    pv_explicit = 0.0
    fcf_t = base_fcf_ann
    n = DCF_HORIZON_YEARS
    for t in range(1, n + 1):
        # Linear decay of growth from g1 (year 1) toward g_term (year n)
        frac = (t - 1) / max(1, (n - 1))
        g_t  = g1 + (g_term - g1) * frac
        fcf_t *= (1.0 + g_t)
        pv_explicit += fcf_t / ((1.0 + wacc) ** t)

    # ── Terminal value (Gordon growth) ──
    terminal_value = fcf_t * (1.0 + g_term) / (wacc - g_term)
    pv_terminal    = terminal_value / ((1.0 + wacc) ** n)

    enterprise_value = pv_explicit + pv_terminal
    equity_value     = enterprise_value - latest_debt + cash_on_hand
    if equity_value <= 0:
        return None
    pt = equity_value / share_count

    # Conviction: anchored on FCF-series trend quality + positivity ratio,
    # same machinery as the FCF-Yield model so it blends comparably.
    _, _, r2_fcf = _theil_sen(fcf_clean)
    cv = float(np.std(fcf_clean) / np.mean(np.abs(fcf_clean))) \
        if np.mean(np.abs(fcf_clean)) > 0 else 1.0
    pos_ratio = sum(1 for v in fcf_clean if v > 0) / len(fcf_clean)
    conv = _model_conviction(r2_fcf, len(fcf_clean), cv) * pos_ratio

    return {
        "pt":              round(pt, 2),
        "r2":              round(r2_fcf, 3),
        "conviction":      round(conv, 4),
        "wacc_pct":        round(wacc * 100, 2),
        "terminal_growth_pct": round(g_term * 100, 2),
        "stage1_growth_pct":   round(g1 * 100, 2),
        "horizon_years":   n,
        "base_fcf_ann_m":  round(base_fcf_ann / 1e6, 1),
        "pv_explicit_m":   round(pv_explicit / 1e6, 1),
        "pv_terminal_m":   round(pv_terminal / 1e6, 1),
        "terminal_weight_pct": round(100.0 * pv_terminal / enterprise_value, 1)
                                 if enterprise_value > 0 else None,
        "enterprise_value_m":  round(enterprise_value / 1e6, 1),
        "sector":          sector,
        "applied_cap":     None,
    }


# ============================================================
# v32 — User price-target blend (Change F)
# ============================================================
# Allowed model keys the user may pin / weight. Must match the keys the engine
# writes into `models`.
PT_BLEND_MODELS = ("ev_ebitda", "ev_rev", "fcf_yield", "emerging_growth", "tam", "dcf")


def _resolve_user_blend(weights: dict, models: dict, pt_blend: Optional[dict],
                         gates: list) -> Tuple[dict, bool, dict]:
    """
    Apply a per-ticker user price-target blend, OVERRIDING the engine's
    conviction weights (and any TAM-dominant / emerging-boost adjustment).

    pt_blend shapes:
        {"mode": "pin",     "model": "dcf"}
        {"mode": "weights", "weights": {"ev_rev": 0.6, "dcf": 0.4}}
        None / {"mode": "off"}  → no change (auto conviction weights)

    Only models actually present in `models` (i.e. that produced a PT) are
    eligible. A pin to a model that didn't compute, or a weight set whose
    models all dropped, falls through to the auto weights with a gate note.

    Returns (weights, active, info) where `info` is a small dict recorded in
    the breakdown for the dashboard.
    """
    if not pt_blend or not isinstance(pt_blend, dict):
        return weights, False, {}
    mode = (pt_blend.get("mode") or "").lower()
    if mode in ("", "off", "auto", "none"):
        return weights, False, {}

    eligible = {k for k in models if models[k].get("pt") is not None and models[k]["pt"] > 0}

    if mode == "pin":
        model = pt_blend.get("model")
        if model in eligible:
            new_w = {k: (1.0 if k == model else 0.0) for k in models}
            gates.append(f"USER_PIN:{model}")
            if model in weights and weights.get(model, 0.0) == 0.0:
                # user pinned a model the engine had dropped (R² floor etc.)
                gates.append(f"USER_PIN_LOW_CONVICTION:{model}")
            return new_w, True, {"mode": "pin", "model": model, "applied": True}
        gates.append(f"USER_PIN_UNAVAILABLE:{model}")
        return weights, False, {"mode": "pin", "model": model, "applied": False}

    if mode == "weights":
        raw = pt_blend.get("weights") or {}
        filtered = {k: float(v) for k, v in raw.items()
                    if k in eligible and v is not None and float(v) > 0}
        total = sum(filtered.values())
        if filtered and total > 0:
            new_w = {k: 0.0 for k in models}
            for k, v in filtered.items():
                new_w[k] = v / total
            gates.append("USER_BLEND_WEIGHTS")
            return new_w, True, {"mode": "weights", "applied": True,
                                 "weights": {k: round(new_w[k], 4) for k in filtered}}
        gates.append("USER_BLEND_EMPTY")
        return weights, False, {"mode": "weights", "applied": False}

    return weights, False, {}


# ============================================================
# CORE PUBLIC API
# ============================================================
def compute_target_price(
    *,
    ebitda_series:    Sequence,
    revenue_series:   Sequence,
    fcf_series:       Sequence,
    debt_series:      Sequence,
    marketcap:        float,
    last_price:       float,
    cash_on_hand:     float            = 0.0,
    shares_diluted:   Optional[float]  = None,
    sector:           Optional[str]    = None,
    fed_target_rate:  float            = 0.0425,
    fed_neutral_rate: float            = 0.0250,
    analyst_target:   Optional[float]  = None,
    n_analysts:       int              = 0,
    apply_rate_compression: bool       = True,
    apply_quality_haircut:  bool       = True,
    apply_envelope:         bool       = True,
    growth_overrides:       Optional[dict] = None,
    tam_overrides:          Optional[dict] = None,
    eps_series:             Optional[Sequence] = None,
    pt_blend:               Optional[dict] = None,
) -> TargetPriceResult:
    """
    Compute multi-model conviction-weighted price target with all RCG guardrails.

    Models:
      1. EV/EBITDA          (clipped 4–40x trailing, blended with sector anchor)
      2. EV/Revenue         (growth-adjusted anchor, 2.5x sector ceiling)
      3. FCF Yield          (sector-required yield, quality-adjusted)
      4. Emerging Growth    (TAM projection, 25% PV discount, 50% blend weight when fired)
      5. TAM Penetration    (v28.8 — explicit MM-set TAM; dominant when fired)
      6. DCF                (v32 — two-stage discounted cash flow, Gordon terminal)

    Pipeline:
      models → R² floor (Gate A) → conviction-weighted blend
      → user blend (v32: pin / custom weights, supersedes conviction) → quality
      haircut → envelope to consensus (Gate B) → final target

    pt_blend (v32): per-ticker user override of the blend. Either
      {"mode":"pin","model":"dcf"} to publish one model, or
      {"mode":"weights","weights":{"ev_rev":0.6,"dcf":0.4}} for a custom blend.
      When active, quality haircut + consensus envelope are skipped (publish
      "in full") and pt_source is "U".
    """
    result = TargetPriceResult()

    if not (last_price and last_price > 0 and marketcap and marketcap > 0):
        result.gates_fired.append("INVALID_INPUTS")
        return result

    # Resolve share count
    share_count = float(marketcap / last_price)
    if shares_diluted is not None and shares_diluted > 0:
        share_count = float(shares_diluted)

    # Latest debt
    debt_clean   = _clean(debt_series)
    latest_debt  = debt_clean[-1] if debt_clean else 0.0
    current_ev   = marketcap + latest_debt - cash_on_hand

    sm = _get_sector_multiples(sector, fed_target_rate, fed_neutral_rate,
                                apply_compression=apply_rate_compression)

    # Pre-compute growth + quality
    rev_raw   = _clean(revenue_series)
    median_qoq, ann_growth, is_emerging, growth_mult = _revenue_growth_stats(rev_raw)
    quality   = _fundamental_quality_score(ebitda_series, revenue_series, fcf_series)

    # ─── User growth overrides ────────────────────────────────
    # When provided via the per-ticker Assumptions panel, these replace the
    # default Theil-Sen projections inside the individual model blocks.
    # None entries → that model falls through to engine default unchanged.
    overrides = growth_overrides or {}
    ov_rev    = overrides.get("rev_growth_ann_pct")
    ov_fcf    = overrides.get("fcf_growth_ann_pct")
    ov_margin = overrides.get("ebitda_margin_now_pct")  # absolute target margin %, not delta
    ov_paydn  = overrides.get("debt_paydown_ann_pct")

    # Debt override: paydown is applied to latest_debt before EV calc
    if ov_paydn is not None and latest_debt > 0:
        # Annual paydown over PROJECTION_QUARTERS/4 years
        years   = PROJECTION_QUARTERS / 4.0
        retained = max(0.0, 1.0 - (ov_paydn / 100.0) * years)
        latest_debt = latest_debt * retained
        current_ev  = marketcap + latest_debt - cash_on_hand

    net_debt  = latest_debt - cash_on_hand
    net_debt_to_rev = (net_debt / (rev_raw[-1] * 4)) if rev_raw and rev_raw[-1] > 0 else 999
    is_clean_balance_sheet = net_debt_to_rev < 0.5

    # v28.9 — Scale gate on emerging-growth model.
    # The emerging-growth math projects current quarterly revenue forward
    # using QoQ growth × decay schedule, then × (sector_mult × tier). For
    # a sub-scale name (RBRK at $1B/yr, IONQ at $50M/yr) this gives a
    # plausible mature equity value. For a mature company at $80B+/yr
    # revenue, the same compound projection produces trillions of dollars
    # of mature equity — total nonsense (e.g. DELL flipped to $4,362 PT).
    #
    # Cap emerging-growth at $5B trailing annualized revenue. Above that,
    # the company has already won its market; conventional EV/EBITDA +
    # EV/Rev + FCF Yield models do the right job.
    EMERGING_MAX_TRAILING_REVENUE = 5e9   # $5B annual
    trailing_ann_rev = (rev_raw[-1] * 4) if (rev_raw and rev_raw[-1] is not None) else 0
    is_subscale = trailing_ann_rev < EMERGING_MAX_TRAILING_REVENUE

    # v29.6 — EPS-decay gate. The Emerging Growth model assumes revenue
    # growth eventually translates into operating leverage. If a company
    # has been deeply unprofitable for years AND it's getting WORSE, the
    # growth thesis is broken; cost structure is blowing up faster than
    # the top line. We don't want to reward that with an inflated PT.
    #
    # Logic: compare TTM EPS (sum of last 4 quarters) to TTM EPS from
    # 4 quarters earlier. If recent TTM is negative AND more negative
    # than year-ago TTM, block Emerging Growth.
    is_eps_decaying = False
    if eps_series and len(eps_series) >= 8:
        eps_clean = [float(e) for e in eps_series
                     if e is not None and not (isinstance(e, float) and np.isnan(e))]
        if len(eps_clean) >= 8:
            ttm_recent = sum(eps_clean[-4:])
            ttm_yago   = sum(eps_clean[-8:-4])
            is_eps_decaying = (ttm_recent < 0) and (ttm_recent < ttm_yago)

    emerging = is_emerging and is_clean_balance_sheet and is_subscale and not is_eps_decaying
    if is_emerging and is_clean_balance_sheet and not is_subscale:
        result.gates_fired.append(
            f"EMERGING_GROWTH_SUPPRESSED_AT_SCALE:ann_rev=${trailing_ann_rev/1e9:.1f}B"
        )
    if is_emerging and is_clean_balance_sheet and is_subscale and is_eps_decaying:
        # Surface for the dashboard so MM sees why emerging was blocked
        result.gates_fired.append("EMERGING_BLOCKED_EPS_DECAY")

    # Tag the result so the report panel can show an EPS-decay warning
    # on TAM-active names too (we DON'T block TAM since MM explicitly set
    # it; just flag it visually).
    if is_eps_decaying:
        result.breakdown["eps_decay_warning"] = True

    result.quality_score = quality

    models      = {}   # canonical breakdown for HTML
    convictions = {}

    # ── MODEL 1: EV / EBITDA ─────────────────────────────────
    ebitda_smoothed = _rolling_median(ebitda_series, window=3)
    ebitda_clean    = _clean(ebitda_smoothed)
    if len(ebitda_clean) >= 3:
        slope, intercept, r2 = _theil_sen(ebitda_clean)
        proj = [slope * (len(ebitda_clean) + i) + intercept for i in range(1, PROJECTION_QUARTERS + 1)]
        fwd  = sum(proj)
        # Override: if user set rev_growth + ebitda_margin, derive projected
        # EBITDA from projected revenue × target margin instead of Theil-Sen
        if ov_rev is not None and ov_margin is not None and rev_raw and rev_raw[-1] > 0:
            target_margin = ov_margin / 100.0
            rev_fwd_for_ebitda = _apply_growth_override(
                default_fwd_sum = sum(rev_raw[-PROJECTION_QUARTERS:]) if len(rev_raw) >= PROJECTION_QUARTERS else rev_raw[-1] * PROJECTION_QUARTERS,
                latest_quarterly = rev_raw[-1],
                override_ann_pct = ov_rev,
            )
            fwd = rev_fwd_for_ebitda * target_margin
        elif ov_rev is not None and rev_raw and rev_raw[-1] > 0:
            # Rev override only — scale EBITDA by same growth rate
            fwd = _apply_growth_override(fwd, ebitda_clean[-1], ov_rev)
        cv   = float(np.std(ebitda_clean) / np.mean(np.abs(ebitda_clean))) \
                if np.mean(np.abs(ebitda_clean)) > 0 else 1.0
        if fwd > 0:
            # v29.0 — 2Q-smoothed trailing EBITDA (anti-spike). Falls back
            # to single-quarter value if only one is available.
            trailing_q = _smooth_2q(ebitda_clean) or ebitda_clean[-1]
            trailing       = trailing_q * 4
            trail_mult     = current_ev / trailing if trailing > 0 else sm["ev_ebitda"]
            trail_clipped  = float(np.clip(trail_mult, 4.0, 40.0))
            sector_anchor  = sm["ev_ebitda"]
            if quality < 0.60:
                blended_mult = min(0.60 * sector_anchor + 0.40 * trail_clipped, sector_anchor)
                applied_cap  = "MEAN_REVERSION_CAP"
            else:
                blended_mult = 0.60 * sector_anchor + 0.40 * trail_clipped
                applied_cap  = None
            blended_mult = float(np.clip(blended_mult, 4.0, 40.0))
            target_eq    = fwd * blended_mult - latest_debt + cash_on_hand
            if target_eq > 0:
                pt   = target_eq / share_count
                conv = _model_conviction(r2, len(ebitda_clean), cv)
                models["ev_ebitda"] = {
                    "pt":            round(pt, 2),
                    "r2":            round(r2, 3),
                    "conviction":    round(conv, 4),
                    "consistency":   None,
                    "blended_mult":  round(blended_mult, 1),
                    "sector_mult":   round(sector_anchor, 1),
                    "trailing_mult": round(trail_clipped, 1),
                    "proj_annual":   round(fwd / 1e6, 1),
                    "quality_score": quality,
                    "applied_cap":   applied_cap,
                }
                if conv > 0:
                    convictions["ev_ebitda"] = conv
                else:
                    result.gates_fired.append(f"R2_FLOOR_DROP:ev_ebitda(r2={r2:.3f})")

    # ── MODEL 2: EV / Revenue (growth-adjusted) ──────────────
    rev_smoothed = _rolling_median(revenue_series, window=3)
    rev_clean    = _clean(rev_smoothed)
    if len(rev_clean) >= 3:
        slope, intercept, r2 = _theil_sen(rev_clean)
        proj = [slope * (len(rev_clean) + i) + intercept for i in range(1, PROJECTION_QUARTERS + 1)]
        fwd  = sum(proj)
        # User override on revenue growth replaces Theil-Sen projection
        if ov_rev is not None:
            fwd = _apply_growth_override(fwd, rev_clean[-1], ov_rev)
        cv   = float(np.std(rev_clean) / np.mean(np.abs(rev_clean))) \
                if np.mean(np.abs(rev_clean)) > 0 else 1.0
        if fwd > 0:
            # v29.0 — 2Q-smoothed trailing revenue for EV/Rev anchor
            trailing_q_rev = _smooth_2q(rev_clean) or rev_clean[-1]
            trail_mult        = current_ev / (trailing_q_rev * 4) if trailing_q_rev > 0 else sm["ev_rev"]
            trail_clipped     = float(np.clip(trail_mult, 0.2, 20.0))
            sector_anchor     = sm["ev_rev"]
            growth_adj_anchor = sector_anchor * growth_mult
            if quality < 0.60:
                blended_mult = min(0.60 * growth_adj_anchor + 0.40 * trail_clipped, sector_anchor)
                applied_cap  = "MEAN_REVERSION_CAP"
            else:
                blended_mult = 0.60 * growth_adj_anchor + 0.40 * trail_clipped
                ceiling      = sector_anchor * 2.5
                if blended_mult > ceiling:
                    blended_mult = ceiling
                    applied_cap  = "EVREV_2_5X_CEILING"
                else:
                    applied_cap  = None
            blended_mult = float(np.clip(blended_mult, 0.2, 20.0))
            target_eq    = fwd * blended_mult - latest_debt + cash_on_hand
            if target_eq > 0:
                pt   = target_eq / share_count
                conv = _model_conviction(r2, len(rev_clean), cv)
                models["ev_rev"] = {
                    "pt":            round(pt, 2),
                    "r2":            round(r2, 3),
                    "conviction":    round(conv, 4),
                    "consistency":   None,
                    "blended_mult":  round(blended_mult, 2),
                    "sector_mult":   round(sector_anchor, 2),
                    "trailing_mult": round(trail_clipped, 2),
                    "proj_annual":   round(fwd / 1e6, 1),
                    "growth_mult":   round(growth_mult, 2),
                    "quality_score": quality,
                    "applied_cap":   applied_cap,
                }
                if conv > 0:
                    convictions["ev_rev"] = conv
                else:
                    result.gates_fired.append(f"R2_FLOOR_DROP:ev_rev(r2={r2:.3f})")

    # ── MODEL 3: FCF Yield (quality-adjusted required yield) ─
    fcf_clean    = _clean(fcf_series)
    fcf_positive = [v for v in fcf_clean if v > 0]
    if len(fcf_clean) >= 3 and len(fcf_positive) >= 3:
        slope, intercept, r2 = _theil_sen(fcf_clean)
        proj = [slope * (len(fcf_clean) + i) + intercept for i in range(1, PROJECTION_QUARTERS + 1)]
        fwd  = sum(proj)
        # FCF growth override:
        #   1. Explicit user FCF growth → use it
        #   2. User set rev growth but NOT FCF → assume FCF scales with revenue
        #      (matches typical analyst modeling: hold FCF margin flat → FCF
        #      growth = revenue growth). Without this, the FCF Yield model is
        #      "anchored" to trailing data and dampens the blended PT response
        #      to user-set revenue assumptions, which feels broken.
        #   3. Neither set → engine default (Theil-Sen on trailing FCF).
        if ov_fcf is not None and fcf_clean[-1] > 0:
            fwd = _apply_growth_override(fwd, fcf_clean[-1], ov_fcf)
        elif ov_rev is not None and fcf_clean[-1] > 0:
            fwd = _apply_growth_override(fwd, fcf_clean[-1], ov_rev)
        cv   = float(np.std(fcf_clean) / np.mean(np.abs(fcf_clean))) \
                if np.mean(np.abs(fcf_clean)) > 0 else 1.0
        if fwd > 0:
            quality_yield_adj = 1.0 + (1.0 - quality) * 0.5
            req_yield = sm["fcf_yield"] * quality_yield_adj
            pt   = (fwd / req_yield) / share_count
            conv = _model_conviction(r2, len(fcf_clean), cv) * (len(fcf_positive) / len(fcf_clean))
            models["fcf_yield"] = {
                "pt":                  round(pt, 2),
                "r2":                  round(r2, 3),
                "conviction":          round(conv, 4),
                "required_yield":      round(req_yield * 100, 2),
                "sector_anchor_yield": round(sm["raw"]["fcf_yield"] * 100, 2),
                "positive_qtrs":       len(fcf_positive),
                "quality_score":       quality,
                "applied_cap":         None,
            }
            if conv > 0:
                convictions["fcf_yield"] = conv
            else:
                result.gates_fired.append(f"R2_FLOOR_DROP:fcf_yield(r2={r2:.3f})")

    # ── v32 MODEL 6: DCF (two-stage discounted cash flow) ────
    # Runs whenever trailing FCF is positive. Growth driven by the user's
    # FCF override, else the revenue override, else trailing-6q revenue OLS.
    try:
        dcf_growth_ov = ov_fcf if ov_fcf is not None else ov_rev
        dcf_result = compute_dcf_model(
            fcf_series       = fcf_series,
            revenue_series   = revenue_series,
            latest_debt      = latest_debt,
            cash_on_hand     = cash_on_hand,
            share_count      = share_count,
            sector           = sector,
            fed_target_rate  = fed_target_rate,
            fed_neutral_rate = fed_neutral_rate,
            growth_override_ann_pct = dcf_growth_ov,
        )
        if dcf_result is not None:
            models["dcf"] = dcf_result
            if dcf_result["conviction"] > 0:
                convictions["dcf"] = dcf_result["conviction"]
            else:
                result.gates_fired.append(f"R2_FLOOR_DROP:dcf(r2={dcf_result['r2']:.3f})")
    except Exception as e:
        result.gates_fired.append(f"DCF_MODEL_ERROR:{e}")

    # ── MODEL 4: Emerging Growth (TAM-discounted projection) ─
    if emerging and len(rev_raw) >= 4:
        try:
            # v29.0 — 2Q-smoothed quarterly revenue as projection base.
            # Dampens single-quarter spikes (the kind that would project
            # forward into trillions over a 3-year decay).
            current_q_rev   = _smooth_2q(rev_raw) or rev_raw[-1]
            current_ann_rev = current_q_rev * 4
            decay = [0.80, 0.50, 0.25]
            target_mature_ann = 0.05
            rev_proj = current_ann_rev
            projected_revs = []
            # User can pin the year-1 growth rate; decay still applies for years 2 + 3
            base_ann = (ov_rev / 100.0) if ov_rev is not None else (median_qoq * 4)
            for d in decay:
                growth_this_yr = max(target_mature_ann, base_ann * d)
                rev_proj *= (1 + growth_this_yr)
                projected_revs.append(rev_proj)

            # v28.7 — Change C: retuned multiplier tiers spanning the
            # wider growth band the loosened trigger admits. Hypergrowth
            # band (≥35% QoQ) unchanged. New 3.0x/2.5x tiers cover
            # 22-46% annualized growth (NVDA, PLTR, RKLB territory).
            if   median_qoq >= 0.50: tam_mult = sm["ev_rev"] * 7.0
            elif median_qoq >= 0.35: tam_mult = sm["ev_rev"] * 5.5
            elif median_qoq >= 0.25: tam_mult = sm["ev_rev"] * 4.5  # was 4.0
            elif median_qoq >= 0.15: tam_mult = sm["ev_rev"] * 3.5  # was 3.0
            elif median_qoq >= 0.10: tam_mult = sm["ev_rev"] * 3.0  # new tier
            else:                    tam_mult = sm["ev_rev"] * 2.5  # new tier (>= 0.05)

            # v28.6 — Change B: cut the PV discount in half for high-conviction
            # growers (QoQ ≥ 35%). The multipliers above already bake in
            # conservative bias; a 25% discount on top of that was
            # belt-and-suspenders. 12.5% is more honest for true growth
            # names while keeping the discount for slower compounders.
            discount_rate = 0.125 if median_qoq >= 0.35 else 0.25
            pv_factor     = 1 / (1 + discount_rate) ** 3
            target_ev_eg  = projected_revs[-1] * tam_mult * pv_factor
            target_eq_eg  = target_ev_eg - latest_debt + cash_on_hand
            if target_eq_eg > 0:
                pt = target_eq_eg / share_count
                _, _, r2_eg = _theil_sen(rev_raw)
                cv_eg = float(np.std(rev_raw) / np.mean(np.abs(rev_raw))) \
                         if np.mean(np.abs(rev_raw)) > 0 else 1.0
                conv  = _model_conviction(r2_eg, len(rev_raw), cv_eg)
                if median_qoq >= 0.35:
                    conv = min(conv * 1.3, 1.0)
                models["emerging_growth"] = {
                    "pt":            round(pt, 2),
                    "r2":            round(r2_eg, 3),
                    "conviction":    round(conv, 4),
                    "median_qoq":    round(median_qoq * 100, 1),
                    "ann_growth":    round(ann_growth * 100, 1),
                    "tam_mult":      round(tam_mult, 1),
                    "yr3_rev_proj":  round(projected_revs[-1] / 1e6, 1),
                    "discount_rate": int(discount_rate * 100),
                    "sector_anchor": sm["ev_rev"],
                }
                if conv > 0:
                    convictions["emerging_growth"] = conv
        except Exception as e:
            result.gates_fired.append(f"EMERGING_GROWTH_ERROR:{e}")

    # ── v28.8 MODEL 5: TAM Penetration (Change D) ────────────
    # Fires only when MM has set tam_usd_billions in tam_overrides.
    # When it fires, it gets 100% blend weight (D1 design) — all other
    # models are kept in the breakdown for reference but contribute 0 to
    # the final PT. This is the "I've decided this name should be
    # valued on TAM, full stop" path.
    tam_fired = False
    if tam_overrides and tam_overrides.get("tam_usd_billions"):
        try:
            tam_result = compute_tam_model(
                tam_usd_billions=float(tam_overrides.get("tam_usd_billions") or 0),
                penetration_pct=(float(tam_overrides["penetration_pct"]) / 100.0
                                  if tam_overrides.get("penetration_pct") is not None else None),
                fcf_margin_pct=(float(tam_overrides["fcf_margin_pct"]) / 100.0
                                 if tam_overrides.get("fcf_margin_pct") is not None else None),
                sector=sector,
                debt_usd=latest_debt,
                cash_usd=cash_on_hand,
                shares_diluted=share_count,
            )
            if tam_result is not None:
                models["tam"] = tam_result
                convictions["tam"] = tam_result["conviction"]
                tam_fired = True
                result.gates_fired.append("TAM_MODEL_FIRED")
                if tam_result.get("tam_capped"):
                    result.gates_fired.append(
                        f"TAM_CAPPED_BY_SECTOR:{sector}={SECTOR_TAM_CAP_BILLIONS.get(sector, SECTOR_TAM_CAP_BILLIONS['_default'])}B")
        except Exception as e:
            result.gates_fired.append(f"TAM_MODEL_ERROR:{e}")

    # ── BLEND OR FALLBACK ────────────────────────────────────
    if not convictions:
        result.gates_fired.append("ALL_MODELS_DROPPED_BY_R2_FLOOR_OR_NEG_PROJ")

        # ─── Fallback A: trailing-median × sector multiples ────────
        # When trend-based projection fails (R² ≈ 0 on every model — common for
        # cyclicals, recently-public, post-merger, or noisy reporters), still
        # give the user *something* by anchoring to the median of the trailing
        # 8 quarters × sector multiples. MEDIAN, not mean, so an outlier
        # quarter doesn't dominate. The result carries pt_source="FB" so the
        # dashboard surfaces it as low-conviction.
        rev_8q    = [v for v in (rev_raw or [])[-8:] if v is not None]
        ebitda_8q = [v for v in (_clean(ebitda_series) or [])[-8:] if v is not None]
        if rev_8q and ebitda_8q and len(rev_8q) >= 4 and len(ebitda_8q) >= 4:
            med_rev    = float(np.median(rev_8q))
            med_ebitda = float(np.median(ebitda_8q))
            fb_pts = []
            # EV/EBITDA fallback
            if med_ebitda > 0:
                ann = med_ebitda * 4
                fb_target_eq = ann * sm["ev_ebitda"] - latest_debt + cash_on_hand
                if fb_target_eq > 0:
                    fb_pts.append(("ev_ebitda_fb", fb_target_eq / share_count))
            # EV/Revenue fallback
            if med_rev > 0:
                ann = med_rev * 4
                fb_target_eq = ann * sm["ev_rev"] - latest_debt + cash_on_hand
                if fb_target_eq > 0:
                    fb_pts.append(("ev_rev_fb", fb_target_eq / share_count))
            if fb_pts:
                # Average the surviving fallback PTs and apply a 0.5 conviction
                # haircut on top of any quality haircut (so low-conviction PTs
                # don't masquerade as production targets).
                avg_pt = sum(p for _, p in fb_pts) / len(fb_pts)
                fb_quality_haircut = 0.5   # explicit low-conviction discount
                final_fb_pt = avg_pt * fb_quality_haircut
                result.target_price = round(final_fb_pt, 2)
                result.raw_target   = round(avg_pt, 2)
                result.upside_pct   = round((final_fb_pt / last_price) - 1.0, 4)
                result.upside_score = float(np.clip(result.upside_pct, -1.0, 2.0))
                result.pt_source    = "FB"    # fallback — surfaced specially in the report
                # Embed which fallback paths fired into the breakdown so the
                # report can show them
                result.breakdown = {
                    **(result.breakdown or {}),
                    "models": {name: {"pt": round(p, 2), "applied_cap": "TRAILING_MEDIAN_FB"} for name, p in fb_pts},
                    "sector": (sm.get("sector") if isinstance(sm, dict) else None),
                    "fallback_window_quarters": min(len(rev_8q), len(ebitda_8q)),
                    "fallback_quality_haircut": fb_quality_haircut,
                }
                result.quality_score   = round((quality or 0.7) * fb_quality_haircut, 3)
                result.quality_haircut = round(1.0 - fb_quality_haircut, 3)
                result.gates_fired.append(
                    f"TRAILING_MEDIAN_FALLBACK:n={min(len(rev_8q),len(ebitda_8q))}q"
                )
                return result

        # ─── Fallback B: analyst consensus if no fundamental signal ──
        if (analyst_target and analyst_target > 0
                and n_analysts >= ANALYST_MIN_N
                and apply_envelope):
            result.target_price    = round(float(analyst_target), 2)
            result.raw_target      = result.target_price
            result.upside_pct      = round((analyst_target / last_price) - 1.0, 4)
            result.upside_score    = float(np.clip(result.upside_pct, -1.0, 2.0))
            result.pt_source       = "A"
            result.gates_fired.append("FALLBACK_TO_ANALYST_CONSENSUS")
        return result

    # ── FCF RUNAWAY CAP ──────────────────────────────────────
    # The FCF model divides projected forward FCF by a small required yield
    # (~4–6%), which amplifies any uptrend in the projection. Theil-Sen on a
    # strongly-trending positive series can produce PTs 5–10x current price.
    # When at least one other valuation model survived, cap the FCF model PT
    # at min(4 × last_price, 2 × max of other surviving valuation models).
    if "fcf_yield" in models and convictions.get("fcf_yield", 0) > 0:
        other_pts = [models[k]["pt"] for k in ("ev_ebitda", "ev_rev", "emerging_growth")
                     if k in models and convictions.get(k, 0) > 0]
        fcf_pt = models["fcf_yield"]["pt"]
        abs_cap = 4.0 * last_price
        if other_pts:
            cap = min(abs_cap, 2.0 * max(other_pts))
        else:
            cap = abs_cap
        if fcf_pt > cap:
            models["fcf_yield"]["pt"] = round(cap, 2)
            models["fcf_yield"]["applied_cap"] = "FCF_RUNAWAY_CAP"
            models["fcf_yield"]["pt_uncapped"] = round(fcf_pt, 2)
            result.gates_fired.append(
                f"FCF_RUNAWAY_CAP:{fcf_pt:.0f}->{cap:.0f}"
            )

    # ── DCF SANITY CAP ───────────────────────────────────────
    # The Gordon terminal value amplifies the stage-1 growth assumption; a
    # high g1 close to WACC can produce a runaway PT. Cap the DCF model PT at
    # min(DCF_ABS_CAP_MULT × last_price, 2 × max of other surviving models),
    # mirroring the FCF runaway guardrail.
    if "dcf" in models and convictions.get("dcf", 0) > 0:
        other_pts = [models[k]["pt"] for k in ("ev_ebitda", "ev_rev", "fcf_yield", "emerging_growth")
                     if k in models and convictions.get(k, 0) > 0]
        dcf_pt  = models["dcf"]["pt"]
        abs_cap = DCF_ABS_CAP_MULT * last_price
        cap = min(abs_cap, 2.0 * max(other_pts)) if other_pts else abs_cap
        if dcf_pt > cap:
            models["dcf"]["pt"] = round(cap, 2)
            models["dcf"]["applied_cap"] = "DCF_RUNAWAY_CAP"
            models["dcf"]["pt_uncapped"] = round(dcf_pt, 2)
            result.gates_fired.append(f"DCF_RUNAWAY_CAP:{dcf_pt:.0f}->{cap:.0f}")

    total_conv = sum(convictions.values())
    weights = {k: v / total_conv for k, v in convictions.items()}

    # v28.8 — D1 TAM-dominates: when the TAM model fires, it gets 100%
    # weight. All other models stay in the breakdown for reference but
    # contribute 0 to the final PT. MM has explicitly decided this name
    # needs TAM-based valuation; don't dilute with models we already
    # know are wrong (negative EBITDA, etc.). This SUPERSEDES the
    # emerging-growth boost logic below.
    if tam_fired:
        for k in list(weights.keys()):
            weights[k] = 1.0 if k == "tam" else 0.0
        result.gates_fired.append("TAM_DOMINATES_PT")

    # Emerging boost: when emerging fires, override to 50% / redistribute the rest.
    #
    # v28.6 — Change A: when the company has NEGATIVE trailing EBITDA AND
    # emerging fires, jump the weight to 100%. Rationale: for unprofitable
    # growth names the EV/EBITDA model returns garbage (negative implied
    # PT, or null) and the FCF Yield model returns garbage (cash burn).
    # Blending them in at 50% drags the price target down to a number
    # that doesn't reflect any model anyone would actually use to value
    # the name. With negative EBITDA the only honest valuation is
    # forward-revenue × multiple, which is exactly what the emerging
    # growth model computes.
    if "emerging_growth" in weights and emerging and not tam_fired:
        # v28.8: skip the emerging boost entirely when TAM model fires —
        # D1 means TAM is at 100% and the emerging-growth weight is
        # already 0. Don't second-guess.
        # Detect "no profits to value off" — trailing EBITDA negative on average.
        # ebitda_clean is set in MODEL 1 above (always defined since
        # _clean() returns []); checking last 4 quarters.
        trailing_ebitda = ebitda_clean[-4:] if ebitda_clean else []
        if trailing_ebitda:
            ebitda_avg = sum(trailing_ebitda) / len(trailing_ebitda)
            unprofitable = ebitda_avg < 0
        else:
            unprofitable = False

        if unprofitable:
            eg_w = 1.00
            result.gates_fired.append("EMERGING_GROWTH_DOMINANT_NEG_EBITDA")
        else:
            eg_w = 0.50
            result.gates_fired.append("EMERGING_GROWTH_BOOST")

        others = [k for k in weights if k != "emerging_growth"]
        others_total = sum(weights[k] for k in others)
        if others_total > 0 and eg_w < 1.0:
            for k in others:
                weights[k] = (weights[k] / others_total) * (1.0 - eg_w)
        elif eg_w >= 1.0:
            # Drop other model weights entirely
            for k in others:
                weights[k] = 0.0
        weights["emerging_growth"] = eg_w

    # ── v32 USER BLEND (Change F): pin a model or set custom weights ──
    # Highest precedence — supersedes conviction weights, TAM-dominant, and
    # the emerging boost. This is the MM saying "publish THIS, full stop."
    user_blend_active = False
    user_blend_info: dict = {}
    if pt_blend:
        if tam_fired and (pt_blend.get("mode") or "").lower() in ("pin", "weights"):
            # Note when the user's explicit blend overrides a TAM-dominant call
            pinned = pt_blend.get("model")
            if not (pt_blend.get("mode") == "pin" and pinned == "tam"):
                result.gates_fired.append("USER_BLEND_OVERRODE_TAM")
        weights, user_blend_active, user_blend_info = _resolve_user_blend(
            weights, models, pt_blend, result.gates_fired
        )

    raw_blend = sum(weights[k] * models[k]["pt"] for k in weights)

    # Quality haircut on final blended PT (lighter for emerging compounders).
    # v32 — skipped when a user blend is active: the MM has explicitly chosen
    # the model(s); publish their number "in full" (same philosophy as TAM).
    if apply_quality_haircut and not user_blend_active:
        if emerging:
            quality_adj = max(quality, 0.75) if quality >= 0.45 else quality
        else:
            quality_adj = quality
        post_haircut = raw_blend * quality_adj
        result.quality_haircut = round(1.0 - quality_adj, 3)
    else:
        post_haircut = raw_blend
        if user_blend_active:
            result.quality_haircut = 0.0

    result.raw_target = round(raw_blend, 2)

    # Annotate weights into models breakdown
    for k in models:
        models[k]["weight"] = round(weights.get(k, 0.0), 4)

    breakdown = {
        "models":             models,
        "conviction_weights": {k: round(v, 4) for k, v in weights.items()},
        "sector":             sector or "Unknown",
        "sector_anchor": {
            "ev_ebitda":     round(sm["ev_ebitda"], 1),
            "ev_rev":        round(sm["ev_rev"],    2),
            "fcf_yield_pct": round(sm["fcf_yield"] * 100, 2),
        },
        "rate_compression": sm["compression"],
        "rate_spread_bps":  sm["rate_spread_bps"],
        "fed_rate":         fed_target_rate,
        "dominant_model":   max(weights, key=weights.get) if weights else "N/A",
        "growth_mult":      round(growth_mult, 2),
        "median_qoq_pct":   round(median_qoq * 100, 1),
        "ann_growth_pct":   round(ann_growth * 100, 1),
        "emerging":         emerging,
    }
    if user_blend_active or user_blend_info:
        breakdown["user_blend"] = user_blend_info
    result.breakdown = breakdown

    # ── GATE B: ENVELOPE TO CONSENSUS ────────────────────────
    # v28.8 — Skip envelope clipping when TAM model fires. The MM has
    # explicitly set TAM inputs for this ticker; clipping the result
    # toward analyst consensus would override their analytical call.
    # Analyst target still appears in the report as a reference line.
    final_pt = post_haircut
    if user_blend_active:
        # v32 — user pinned/weighted the models; publish in full, skip the
        # consensus clip. Still compute the divergence flag for reference so
        # the dashboard can show how far the chosen blend sits from analysts.
        result.gates_fired.append("ENVELOPE_SKIPPED_USER_BLEND")
        result.pt_source = "U"
        if (analyst_target and analyst_target > 0
                and n_analysts >= ANALYST_MIN_N and last_price and last_price > 0):
            div = abs(final_pt - analyst_target) / last_price
            result.divergence_flag = div > ANALYST_DIVERGENCE_FLAG_THRESHOLD
    else:
        if tam_fired and apply_envelope:
            result.gates_fired.append("ENVELOPE_SKIPPED_TAM_DOMINANT")
        if apply_envelope and not tam_fired and analyst_target and analyst_target > 0:
            final_pt, src, flagged = envelope_to_consensus(
                internal_pt   = post_haircut,
                analyst_target = analyst_target,
                n_analysts    = n_analysts,
                last_price    = last_price,
            )
            result.pt_source       = src
            result.divergence_flag = flagged
            if src == "M⚠clip":
                result.gates_fired.append("ENVELOPE_CLIPPED_TO_CONSENSUS")
        else:
            result.pt_source = "M"

    result.target_price = round(float(final_pt), 2)
    result.upside_pct   = round((final_pt / last_price) - 1.0, 4)
    if result.upside_pct > 0:
        result.upside_score = float(np.clip(np.sqrt(result.upside_pct) * 0.7, 0, 2.0))
    else:
        result.upside_score = float(np.clip(result.upside_pct, -1.0, 0.0))

    return result


# ============================================================
# GATE B HELPER
# ============================================================
def envelope_to_consensus(
    internal_pt:    Optional[float],
    analyst_target: Optional[float],
    n_analysts:     int,
    last_price:     Optional[float],
    n_min:          int   = ANALYST_MIN_N,
    flag_threshold:    float = ANALYST_DIVERGENCE_FLAG_THRESHOLD,
    severe_threshold:  float = ANALYST_DIVERGENCE_SEVERE_THRESHOLD,
    band_low:       float = ANALYST_BAND_LOW,
    band_high:      float = ANALYST_BAND_HIGH,
) -> Tuple[Optional[float], str, bool]:
    """
    Returns (final_pt, source_label, divergence_flagged).

    Source labels:
        "M"        - no analyst data, model unmodified
        "M✓"       - model agrees with analyst (within flag_threshold of price)
        "M*"       - flagged divergence (> flag, ≤ severe)
        "M⚠clip"   - severe divergence, clipped to [analyst·band_low, analyst·band_high]
        "A"        - no internal model, analyst-only fallback
        "N/A"      - nothing usable
    """
    if internal_pt is None or internal_pt <= 0:
        if analyst_target and analyst_target > 0:
            return float(analyst_target), "A", False
        return None, "N/A", False

    if (not analyst_target or analyst_target <= 0
            or n_analysts < n_min
            or not last_price or last_price <= 0):
        return float(internal_pt), "M", False

    div = abs(internal_pt - analyst_target) / last_price

    if div > severe_threshold:
        lo = analyst_target * band_low
        hi = analyst_target * band_high
        clipped = float(np.clip(internal_pt, lo, hi))
        return round(clipped, 2), "M⚠clip", True

    if div > flag_threshold:
        return round(float(internal_pt), 2), "M*", True

    return round(float(internal_pt), 2), "M✓", False


# ============================================================
# SCREENER COMPATIBILITY WRAPPER
# ============================================================
def screener_compat(
    ebitda_series, debt_series, fcf_series,
    marketcap, last_price, cash_on_hand=0.0,
    shares_diluted=None, revenue_series=None, sector=None,
    *,
    fed_target_rate:  float = 0.0425,
    fed_neutral_rate: float = 0.0250,
    analyst_target:   Optional[float] = None,
    n_analysts:       int = 0,
    apply_envelope:   bool = True,
):
    """
    Drop-in replacement for the screener's compute_target_price_and_upside().

    Returns the same 4-tuple shape:
        (blended_pt, upside_pct, upside_score, pt_detail_dict)
    """
    # Resolve shares from series-style argument if present
    sd = None
    if shares_diluted:
        sd_clean = [float(v) for v in shares_diluted
                    if v is not None and not (isinstance(v, float) and np.isnan(v)) and float(v) > 0]
        sd = sd_clean[-1] if sd_clean else None

    res = compute_target_price(
        ebitda_series    = ebitda_series   or [],
        revenue_series   = revenue_series  or [],
        fcf_series       = fcf_series      or [],
        debt_series      = debt_series     or [],
        marketcap        = float(marketcap or 0),
        last_price       = float(last_price or 0),
        cash_on_hand     = float(cash_on_hand or 0),
        shares_diluted   = sd,
        sector           = sector,
        fed_target_rate  = fed_target_rate,
        fed_neutral_rate = fed_neutral_rate,
        analyst_target   = analyst_target,
        n_analysts       = n_analysts,
        apply_envelope   = apply_envelope,
    )

    return (
        res.target_price,
        round(res.upside_pct, 4),
        round(res.upside_score, 4),
        res.to_pt_detail(),
    )


# ============================================================
# REPORT COMPATIBILITY WRAPPER
# ============================================================
def report_compat(
    ebitda_series, revenue_series, fcf_series,
    marketcap, debt, cash, shares, sector,
    *,
    fed_target_rate:  float = 0.0425,
    fed_neutral_rate: float = 0.0250,
    analyst_target:   Optional[float] = None,
    n_analysts:       int = 0,
    last_price:       Optional[float] = None,
    apply_envelope:   bool = True,
):
    """
    Drop-in replacement for the report's compute_v3_target_price().
    Returns (final_target, models_dict).
    """
    if last_price is None and marketcap and shares and shares > 0:
        last_price = marketcap / shares

    res = compute_target_price(
        ebitda_series    = ebitda_series  or [],
        revenue_series   = revenue_series or [],
        fcf_series       = fcf_series     or [],
        debt_series      = [debt] if debt is not None else [],
        marketcap        = float(marketcap or 0),
        last_price       = float(last_price or 0),
        cash_on_hand     = float(cash or 0),
        shares_diluted   = float(shares) if shares else None,
        sector           = sector,
        fed_target_rate  = fed_target_rate,
        fed_neutral_rate = fed_neutral_rate,
        analyst_target   = analyst_target,
        n_analysts       = n_analysts,
        apply_envelope   = apply_envelope,
    )
    return res.target_price, res.breakdown.get("models", {})


# ============================================================
# Public namespace
# ============================================================
__all__ = [
    "compute_target_price",
    "compute_dcf_model",
    "compute_tam_model",
    "compute_growth_baseline",
    "envelope_to_consensus",
    "screener_compat",
    "report_compat",
    "TargetPriceResult",
    "PT_BLEND_MODELS",
    "SECTOR_MULTIPLES",
    "R2_HARD_FLOOR",
    "R2_FULL_WEIGHT",
    "ANALYST_DIVERGENCE_SEVERE_THRESHOLD",
]
