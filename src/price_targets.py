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
    pt_source:       str             = "N/A"    # "M", "M✓", "M*", "M⚠clip", "A", "N/A"
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
    ann_growth = (1 + median_qoq) ** 4 - 1
    high_growth_qs = sum(1 for g in qoq if g >= 0.25)
    is_emerging    = high_growth_qs >= 2 and median_qoq >= 0.25

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
) -> TargetPriceResult:
    """
    Compute multi-model conviction-weighted price target with all RCG guardrails.

    Models:
      1. EV/EBITDA          (clipped 4–40x trailing, blended with sector anchor)
      2. EV/Revenue         (growth-adjusted anchor, 2.5x sector ceiling)
      3. FCF Yield          (sector-required yield, quality-adjusted)
      4. Emerging Growth    (TAM projection, 25% PV discount, 50% blend weight when fired)

    Pipeline:
      models → R² floor (Gate A) → conviction-weighted blend → quality haircut
      → envelope to consensus (Gate B) → final target
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
    emerging = is_emerging and is_clean_balance_sheet

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
            trailing       = ebitda_clean[-1] * 4
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
            trail_mult        = current_ev / (rev_clean[-1] * 4) if rev_clean[-1] > 0 else sm["ev_rev"]
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

    # ── MODEL 4: Emerging Growth (TAM-discounted projection) ─
    if emerging and len(rev_raw) >= 4:
        try:
            current_ann_rev = rev_raw[-1] * 4
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

            if   median_qoq >= 0.50: tam_mult = sm["ev_rev"] * 7.0
            elif median_qoq >= 0.35: tam_mult = sm["ev_rev"] * 5.5
            elif median_qoq >= 0.25: tam_mult = sm["ev_rev"] * 4.0
            else:                    tam_mult = sm["ev_rev"] * 3.0

            discount_rate = 0.25
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
                    "discount_rate": 25,
                    "sector_anchor": sm["ev_rev"],
                }
                if conv > 0:
                    convictions["emerging_growth"] = conv
        except Exception as e:
            result.gates_fired.append(f"EMERGING_GROWTH_ERROR:{e}")

    # ── BLEND OR FALLBACK ────────────────────────────────────
    if not convictions:
        result.gates_fired.append("ALL_MODELS_DROPPED_BY_R2_FLOOR_OR_NEG_PROJ")
        # Fall back to analyst target if available — no fundamental signal
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

    total_conv = sum(convictions.values())
    weights = {k: v / total_conv for k, v in convictions.items()}

    # Emerging boost: when emerging fires, override to 50% / redistribute the rest
    if "emerging_growth" in weights and emerging:
        eg_w = 0.50
        others = [k for k in weights if k != "emerging_growth"]
        others_total = sum(weights[k] for k in others)
        if others_total > 0:
            for k in others:
                weights[k] = (weights[k] / others_total) * (1.0 - eg_w)
        weights["emerging_growth"] = eg_w
        result.gates_fired.append("EMERGING_GROWTH_BOOST")

    raw_blend = sum(weights[k] * models[k]["pt"] for k in weights)

    # Quality haircut on final blended PT (lighter for emerging compounders)
    if apply_quality_haircut:
        if emerging:
            quality_adj = max(quality, 0.75) if quality >= 0.45 else quality
        else:
            quality_adj = quality
        post_haircut = raw_blend * quality_adj
        result.quality_haircut = round(1.0 - quality_adj, 3)
    else:
        post_haircut = raw_blend

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
    result.breakdown = breakdown

    # ── GATE B: ENVELOPE TO CONSENSUS ────────────────────────
    final_pt = post_haircut
    if apply_envelope and analyst_target and analyst_target > 0:
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
    "envelope_to_consensus",
    "screener_compat",
    "report_compat",
    "TargetPriceResult",
    "SECTOR_MULTIPLES",
    "R2_HARD_FLOOR",
    "R2_FULL_WEIGHT",
    "ANALYST_DIVERGENCE_SEVERE_THRESHOLD",
]
