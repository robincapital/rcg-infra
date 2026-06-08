"""
Model descriptions for markout dashboard v31.

Maps each model stem → human-readable description of what it captures.
Used by markout_eval_publish.py to populate the dashboard JSON.
"""

MODEL_DESCRIPTIONS = {
    # ═══════════════════════════════════════════════════════════════════
    # MOMENTUM FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "momentum_5": {
        "short_desc": "Short-Term Momentum · Captures 5-bar trend continuation",
        "what_it_captures": "Price acceleration over 5 bars. Works best in trending markets with strong follow-through.",
        "entry": "Score > threshold indicates upward momentum",
        "exit": "Momentum reversal or timeout"
    },
    "momentum_10": {
        "short_desc": "Medium-Term Momentum · Captures 10-bar trend strength",
        "what_it_captures": "Sustained directional moves over 10 bars. Filters out noise from 5-bar signal.",
        "entry": "Score > threshold for established trend",
        "exit": "Trend exhaustion or mean reversion"
    },
    "momentum_20": {
        "short_desc": "Long-Term Momentum · Captures 20-bar persistent trends",
        "what_it_captures": "Multi-hour trends with high persistence. Lower turnover than shorter windows.",
        "entry": "Strong trend confirmed over 20 bars",
        "exit": "Trend break or profit target"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # MEAN REVERSION FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "mean_reversion_5": {
        "short_desc": "Fast Mean Reversion · Fades 5-bar extremes",
        "what_it_captures": "Short-term overextensions that snap back quickly. High turnover intraday strategy.",
        "entry": "|z-score| > 2.0 signals mean reversion opportunity",
        "exit": "Return to mean or stop-loss"
    },
    "mean_reversion_10": {
        "short_desc": "Moderate Mean Reversion · Fades 10-bar deviations",
        "what_it_captures": "Medium-term pullbacks in trending stocks. Balances mean reversion with trend.",
        "entry": "Price stretched beyond 10-bar average",
        "exit": "Mean reversion or trend resumes"
    },
    "mean_reversion_20": {
        "short_desc": "Swing Mean Reversion · Fades 20-bar extremes",
        "what_it_captures": "Multi-hour swings that revert to longer-term mean. Lower frequency, higher hit rate.",
        "entry": "Significant deviation from 20-bar SMA",
        "exit": "Full reversion or secondary signal"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # RSI EXTREME FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "rsi_extreme_14": {
        "short_desc": "RSI Extremes · Fades overbought/oversold (14-period)",
        "what_it_captures": "Classic RSI < 30 or > 70 reversals. Works in range-bound markets.",
        "entry": "RSI crosses extreme thresholds (30/70)",
        "exit": "RSI normalizes to 40-60 range"
    },
    "rsi_extreme_9": {
        "short_desc": "Fast RSI · Fades 9-period overbought/oversold",
        "what_it_captures": "More sensitive RSI for intraday mean reversion. Higher turnover.",
        "entry": "RSI < 25 or > 75",
        "exit": "RSI returns to midrange (45-55)"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # RANGE / BANDS FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "bollinger_pos_20": {
        "short_desc": "Bollinger Position · Fades band extremes (20-period)",
        "what_it_captures": "Price position within Bollinger Bands. Fades touches of upper/lower bands.",
        "entry": "Price at ±2σ bands signals reversion",
        "exit": "Return to midline or band break"
    },
    "bollinger_pos_20_k25": {
        "short_desc": "Wide Bollinger · Fades 2.5σ extremes",
        "what_it_captures": "Wider bands (2.5σ) for volatile stocks. Lower frequency, higher conviction.",
        "entry": "Price at ±2.5σ bands",
        "exit": "Reversion to mean or stop"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # BREAKOUT FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "donchian_break_10": {
        "short_desc": "Donchian Breakout · Buys 10-bar highs, sells lows",
        "what_it_captures": "Channel breakouts signaling new trends. Momentum continuation strategy.",
        "entry": "Price breaks above 10-bar high (or below low)",
        "exit": "Channel reversal or profit target"
    },
    "donchian_break_20": {
        "short_desc": "Donchian 20-Bar · Longer breakout confirmation",
        "what_it_captures": "20-bar channel breaks for stronger trend signals. Lower false breakouts.",
        "entry": "Price breaks 20-bar high/low",
        "exit": "Trend exhaustion or stop"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # MOVING AVERAGE CROSS FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "sma_cross_5_20": {
        "short_desc": "SMA Cross (5/20) · Fast trend changes",
        "what_it_captures": "Fast SMA crossing slow SMA signals trend change. Classic trend-following.",
        "entry": "SMA(5) crosses above/below SMA(20)",
        "exit": "Cross reversal or timeout"
    },
    "ema_cross_9_21": {
        "short_desc": "EMA Cross (9/21) · Responsive trend signals",
        "what_it_captures": "EMA crosses for faster reaction to trend changes. Fibonacci-based periods.",
        "entry": "EMA(9) crosses EMA(21)",
        "exit": "Cross reversal or profit target"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # TIME SERIES / ARIMA FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "arima_20": {
        "short_desc": "Time-Series Forecasting · ARIMA mean reversion",
        "what_it_captures": "ARIMA(2,1,1) forecasts on 20-bar returns. Fades forecast extremes > 1.5σ.",
        "entry": "Forecast z-score > 1.5 signals mean reversion",
        "exit": "Forecast normalizes or 8-hour timeout"
    },
    "ar2_forecast_30": {
        "short_desc": "AR(2) Autoregression · 30-bar forecast",
        "what_it_captures": "Simple AR(2) model on 30-bar returns. Fast computation, reasonable accuracy.",
        "entry": "AR forecast deviation > threshold",
        "exit": "Forecast error corrects"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # LINEAR REGRESSION FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "lr_slope_20": {
        "short_desc": "Linear Regression Slope · 20-bar trend strength",
        "what_it_captures": "Slope of 20-bar OLS regression. Positive slope = uptrend, negative = downtrend.",
        "entry": "|slope| > threshold indicates strong trend",
        "exit": "Slope reversal or flattening"
    },
    "lr_slope_60": {
        "short_desc": "Linear Regression 60-Bar · Longer trend detection",
        "what_it_captures": "60-bar regression slope for multi-hour trends. Filters intraday noise.",
        "entry": "Sustained slope over 60 bars",
        "exit": "Trend break"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # PATTERN FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "pattern_harami": {
        "short_desc": "Harami Pattern · Bearish/bullish reversal candles",
        "what_it_captures": "Inside-bar reversal patterns. Small bar inside large bar signals reversal.",
        "entry": "Harami pattern confirmed",
        "exit": "Pattern invalidated or target hit"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # CROSS-SECTIONAL FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "cs_rank_momentum": {
        "short_desc": "Cross-Sectional Rank · Relative momentum",
        "what_it_captures": "Rank-based long/short. Buys top decile momentum, shorts bottom decile.",
        "entry": "Rank > 90th percentile (long) or < 10th (short)",
        "exit": "Rank reverts to median"
    },
    "cs_rank_value": {
        "short_desc": "Cross-Sectional Value · Relative valuation",
        "what_it_captures": "Rank-based value strategy. Buys cheap, shorts expensive on P/E basis.",
        "entry": "Valuation rank extreme (top/bottom decile)",
        "exit": "Rank normalization"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # ENSEMBLE FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "combo_momentum": {
        "short_desc": "Momentum Ensemble · Blends 3 momentum signals",
        "what_it_captures": "Equal-weight average of momentum_5/10/20. Smooths short-term noise.",
        "entry": "Ensemble score > threshold",
        "exit": "Ensemble weakens"
    },
    "combo_meanrev": {
        "short_desc": "Mean Reversion Ensemble · Blends 3 MR signals",
        "what_it_captures": "Average of mean_reversion_5/10/20. Confirms pullback across timeframes.",
        "entry": "Ensemble extreme > threshold",
        "exit": "Ensemble reversion complete"
    },
    
    # ═══════════════════════════════════════════════════════════════════
    # META-MODEL FAMILY
    # ═══════════════════════════════════════════════════════════════════
    "meta_blend_top5": {
        "short_desc": "Meta-Model (OLS) · Blends top 5 champions",
        "what_it_captures": "OLS regression on top 5 champion signals. Learns optimal weights dynamically.",
        "entry": "Meta-score > threshold",
        "exit": "Meta-score reversal"
    },
}

# Default fallback for models not in the map
DEFAULT_DESCRIPTION = {
    "short_desc": "Quantitative Signal · Algorithmic trading strategy",
    "what_it_captures": "Proprietary signal designed to capture short-term inefficiencies.",
    "entry": "Score exceeds entry threshold",
    "exit": "Score reversal or timeout"
}

def get_description(model_stem: str) -> dict:
    """
    Get description dict for a model stem, or return default if not found.
    """
    return MODEL_DESCRIPTIONS.get(model_stem, DEFAULT_DESCRIPTION)
