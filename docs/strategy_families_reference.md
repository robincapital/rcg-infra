# Strategy Family Reference — Markout Dashboard v29

**Purpose:** Quick reference for understanding which models belong to which strategy methodology families.

---

## Strategy Type Taxonomy

### 📈 **MOMENTUM** (`momentum`)
**Edge Hypothesis:** Recent price direction continues in the near term

**Models:**
- `momentum_5bar` — 5-bar momentum (intraday swing)
- `momentum_21bar` — 21-bar momentum (daily trend)
- Other momentum window variants

**Typical Behavior:**
- Long when price rising strongly
- Short when price falling strongly
- Mean hold: 1-4 hours
- Works best in trending regimes

---

### 🔄 **MEAN REVERSION** (`mean_reversion`)
**Edge Hypothesis:** Price reverts to recent average after deviation

**Models:**
- `mean_rev_20` — 20-bar mean reversion
- `mean_rev_50` — 50-bar mean reversion
- Other window variants

**Typical Behavior:**
- Long when price below recent mean
- Short when price above recent mean
- Mean hold: 30min - 2 hours
- Works best in range-bound, low-vol regimes
- **Anti-correlated with momentum** (often negative correlation)

---

### 📊 **RANGE / BANDS** (`bollinger_pos`)
**Edge Hypothesis:** Price bounces off dynamic bands (Bollinger, Keltner, etc.)

**Models:**
- `bollinger_pos_20` — Bollinger position (20-bar, 2σ)
- `bollinger_pos_20_k25` — Tighter bands (2.5σ)
- `bb_squeeze_20` — Bollinger squeeze breakout

**Typical Behavior:**
- Long when price at lower band (mean reversion flavor)
- Short when price at upper band
- OR: Long on band breakouts (momentum flavor) — depends on variant
- Mean hold: 1-3 hours

---

### 🚀 **BREAKOUT** (`donchian_break`)
**Edge Hypothesis:** Price breaks recent high/low → continuation

**Models:**
- `donchian_break_10` — 10-bar channel breakout
- `donchian_break_20` — 20-bar channel breakout

**Typical Behavior:**
- Long when price breaks above N-bar high
- Short when price breaks below N-bar low
- Mean hold: 2-6 hours
- Momentum sub-family (trend-following)

---

### ⚡ **RSI EXTREME** (`rsi_extreme`)
**Edge Hypothesis:** Oversold/overbought RSI signals mean reversion

**Models:**
- `rsi_extreme_14` — 14-bar RSI < 30 (buy) / > 70 (sell)

**Typical Behavior:**
- Long when RSI deeply oversold
- Short when RSI deeply overbought
- Mean hold: 1-2 hours
- Mean reversion sub-family

---

### 〰️ **MOVING AVERAGE CROSS** (`sma_cross`, `ema_cross`)
**Edge Hypothesis:** Fast MA crossing slow MA signals trend change

**Models:**
- `sma_cross_5_20` — 5-bar SMA crosses 20-bar SMA
- `ema_cross_8_21` — 8-bar EMA crosses 21-bar EMA
- Other fast/slow combinations

**Typical Behavior:**
- Long when fast MA > slow MA (golden cross)
- Short when fast MA < slow MA (death cross)
- Mean hold: 3-8 hours (trend-following, slower turnover)
- Momentum sub-family

---

### 📐 **LINEAR REGRESSION** (`lr_slope`)
**Edge Hypothesis:** Recent price slope predicts near-term direction

**Models:**
- `lr_slope_10` — 10-bar linear regression slope
- `lr_slope_20` — 20-bar linear regression slope

**Typical Behavior:**
- Long when slope > threshold (price trending up)
- Short when slope < -threshold (price trending down)
- Mean hold: 1-4 hours
- Momentum sub-family (smoother than raw momentum)

---

### 📉 **TIME SERIES (ARIMA/AR)** (`arima`)
**Edge Hypothesis:** Statistical model forecasts next-period price

**Models:**
- `arima_1` — ARIMA(1,0,0) = AR(1) autoregressive
- `arima_20` — ARIMA(20,0,0) = AR(20) longer memory
- `ar2_10` — AR(2) with 10-bar lookback

**Typical Behavior:**
- Fits autoregressive model to recent bars
- Long when model forecasts positive return
- Short when model forecasts negative return
- Mean hold: varies (depends on forecast horizon)
- **Often low IC** — time-series models struggle with noisy intraday data

---

### 🔬 **STATISTICAL PATTERNS** (`pattern`)
**Edge Hypothesis:** Microstructure anomalies (Hurst, Kalman, OU mean reversion)

**Models:**
- `hurst_20` — Hurst exponent (measures trendiness vs noise)
- `kalman_20` — Kalman filter state estimate
- `ou_halflife` — Ornstein-Uhlenbeck mean reversion half-life

**Typical Behavior:**
- Hurst: Long when H > 0.5 (trending), short when H < 0.5 (mean-reverting)
- Kalman: Long/short based on deviation from filtered state
- OU: Long/short based on distance from equilibrium level
- Mean hold: 1-4 hours
- **Advanced stat methods** — often high IC but low capacity

---

### 🌐 **CROSS-SECTIONAL** (`cross_sectional`)
**Edge Hypothesis:** Relative strength vs peers predicts outperformance

**Models:**
- `relative_strength_rank_5bar` — Rank vs sector on 5-bar momentum
- `sector_relative_momentum` — Outperform/underperform sector median
- `pca_residual` — PCA factor model residual (idiosyncratic edge)

**Typical Behavior:**
- Long names outperforming their sector
- Short names underperforming their sector
- Mean hold: 2-6 hours
- **Diversifies well** with single-name technical models (low correlation)

---

### 🎯 **ENSEMBLE / COMBO** (`ensemble`)
**Edge Hypothesis:** Combine multiple signals to smooth noise

**Models:**
- `combo_meanrev` — Ensemble of mean-reversion signals (RSI + Bollinger + Z-score)

**Typical Behavior:**
- Averages scores from multiple base models
- More robust than single-signal models
- Mean hold: varies (depends on components)

---

### 🧠 **META-MODEL (OLS)** (`meta_blend`)
**Edge Hypothesis:** OLS fit across all tournament entrants → Stage 1 blend

**Models:**
- `meta_blend_30min` — OLS on 30min forward returns
- `meta_blend_60min` — OLS on 60min forward returns
- `meta_blend_4h` — OLS on 4h forward returns

**Typical Behavior:**
- **Combines ALL tournament signals** with learned weights
- Long/short based on weighted ensemble forecast
- Mean hold: varies by horizon (30min = fast, 4h = slower)
- **Highest Sharpe** expected (diversifies across uncorrelated base models)

---

### 📰 **BBG COMPOSITE** (`bbg_composite`)
**Edge Hypothesis:** Bloomberg predictive score captures analyst consensus + news sentiment

**Models:**
- `bbg_predictive_composite` — Bloomberg proprietary signal

**Typical Behavior:**
- Long when BBG composite > threshold
- Short when BBG composite < -threshold
- Mean hold: 4-12 hours (slower update cadence than technicals)
- **Fundamental flavor** — complements technical signals

---

## Strategy Type Use Cases

### **"I want pure technical momentum"**
Filter to: `momentum` + `donchian_break` + `lr_slope` + `ema_cross`  
Sort by: Sharpe  
Look for: Positive correlation with each other (all ride trends)

### **"I want mean reversion for range-bound markets"**
Filter to: `mean_reversion` + `bollinger_pos` + `rsi_extreme`  
Sort by: Return  
Look for: Negative correlation with momentum family

### **"I want diversification — what's uncorrelated?"**
1. Look at Correlation Matrix (bottom of dashboard)
2. Find red/blue cells (low/negative correlation)
3. Example: `cross_sectional` often low-corr with `momentum`
4. Example: `pattern` (Hurst) low-corr with `mean_reversion`

### **"I want the kitchen sink — best combined signal"**
Filter to: `meta_blend`  
Sort by: Sharpe  
Pick: `meta_blend_60min` (usually highest Sharpe, best diversification)

### **"I want to compare parameter variants within one approach"**
1. Search: "momentum" (shows all momentum variants)
2. Sort by: Return
3. Compare: `momentum_5bar` vs `momentum_21bar` → see which window length works better
4. Click each → compare calibration curves

### **"I want statistical rigor — which models have monotonic calibration?"**
1. Sort by: Hit Rate desc (models with strong directional edge)
2. Click top rows → check calibration chart
3. Good calibration: bars monotonically increase left-to-right (more positive score → higher avg return)
4. Bad calibration: random scatter (score doesn't predict return)

---

## Correlation Patterns (Typical)

| Family A | Family B | Expected Correlation | Why |
|----------|----------|----------------------|-----|
| Momentum | Mean Reversion | **Negative (-0.3 to -0.6)** | Opposite bets (trending vs reverting) |
| Momentum | Breakout | **Positive (+0.5 to +0.8)** | Both ride trends |
| Mean Reversion | Bollinger | **Positive (+0.7 to +0.9)** | Both buy dips, sell rallies |
| Cross-Sectional | Momentum | **Low (+0.1 to +0.3)** | Different alpha sources (relative vs absolute) |
| Meta-Blend | All others | **Medium (+0.3 to +0.6)** | Weighted average of base models |
| ARIMA | All others | **Low (-0.1 to +0.2)** | Noisy, low IC → weak signal |

---

## Champion Selection Logic

**For each (strategy type, horizon), the dashboard picks ONE champion:**

1. Filter to models with **n_trades ≥ 3** (small-sample guard)
2. Sort by **cum_return_net** at 5 bps slippage
3. Top model = **family champion** (gets ★ badge)
4. Champions go into the **correlation matrix**

**Example:**
- Momentum family: `momentum_5bar` has highest return → champion
- Mean Reversion family: `mean_rev_20` has highest return → champion
- These two appear in the correlation matrix → likely negative correlation

---

## When to Use Each Strategy Type

| Regime | Best Families | Avoid |
|--------|---------------|-------|
| **Trending (VIX < 15, steady SPY direction)** | Momentum, Breakout, MA Cross, LR Slope | Mean Reversion, RSI Extreme |
| **Range-bound (VIX 15-20, choppy SPY)** | Mean Reversion, Bollinger, RSI Extreme | Momentum, Breakout |
| **High vol (VIX > 25)** | Cross-Sectional, Meta-Blend (diversified) | Single-name technicals (whipsaw) |
| **News-driven (earnings week)** | BBG Composite, Meta-Blend | Pure technicals (ignore fundamentals) |

---

**Pro Tip:** Use the **Strategy Type filter** to rapidly switch between families and see which methodology is working in current market conditions. If momentum is bleeding → switch filter to mean reversion → see if reversals are working instead.

---

**Status:** Reference guide for v29 table-first interface  
**Last updated:** 2026-05-19
