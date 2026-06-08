# Markout Dashboard v30 — Quick Reference Card

## 🎯 Three-Second Scan

**Summary Panel → Scatter Plots → Table**

| Metric | What It Tells You | Good | Bad |
|--------|-------------------|------|-----|
| **Profitable Count** | How many models are making money | > 50% | < 30% |
| **Avg Return** | Portfolio-level expectation | > +2% | < 0% |
| **Avg Sharpe** | Risk-adjusted efficiency | > 1.0 | < 0 |
| **Total Trades** | Sample size confidence | > 500 | < 100 |

---

## 📊 Scatter Plot Cheat Sheet

### Return vs Hit Rate (Quadrant View)

```
         ┌─────────────┬─────────────┐
         │   MIXED     │   WINNERS   │ ← Top-right = deploy these
  Return │             │             │
   +     │             │   ⭐ 🎯    │
  ───────┼─────────────┼─────────────┤
         │             │             │
   -     │   LOSERS    │  COST DRAG  │ ← Bottom = avoid/fix
         │             │             │
         └─────────────┴─────────────┘
          < 50%         > 50%
               Hit Rate
```

**Winners (top-right):** Profitable + predictive → **deploy in live portfolio**  
**Cost Drag (bottom-right):** High hit but negative return → **reduce slippage or exit faster**  
**Asymmetric Payoff (top-left):** Low hit but profitable → **wins bigger than losses, investigate why**  
**Losers (bottom-left):** Low hit + unprofitable → **retire or redesign**

### Sharpe vs Max Drawdown

```
         ┌─────────────┬─────────────┐
 Sharpe  │   SMOOTH    │   VOLATILE  │
         │             │             │
   +     │   ⭐ 🎯    │      ⚠️     │ ← High Sharpe = good
  ───────┼─────────────┼─────────────┤
         │             │             │
   -     │  DEFENSIVE  │  DANGEROUS  │ ← Low Sharpe = bad
         │             │             │
         └─────────────┴─────────────┘
          < -10%        > -10%
            Max Drawdown
```

**Smooth (top-left):** High Sharpe + low DD → **ideal for portfolio**  
**Dangerous (bottom-right):** High DD + low Sharpe → **avoid**  
**Volatile (top-right):** High Sharpe but large DD → **needs tighter stops**  
**Defensive (bottom-left):** Low vol but poor returns → **filler for diversification**

### Trades vs Hold Time

```
         ┌─────────────┬─────────────┐
  Hold   │  POSITION   │  POSITION   │
  Time   │  SIGNALS    │  SIGNALS    │
  (min)  │  (confident)│ (needs data)│
  240+   ├─────────────┼─────────────┤
         │   HF-ALGO   │  HF-ALGO    │
  < 60   │  (confident)│ (needs data)│
         │             │             │
         └─────────────┴─────────────┘
          < 10 trades   > 10 trades
```

**Vertical line at 10 trades:** Minimum for statistical confidence  
**Horizontal lines:** 60m (very short), 240m (intraday swing)  
**Left side (< 10 trades):** Insufficient data — monitor but don't deploy yet  
**Right side (> 10 trades):** Actionable sample size

---

## 🎨 Family Color Palette

| Family | Color | Typical Strategy |
|--------|-------|------------------|
| Momentum | 🔵 Blue | Trend-following, 5-120 bar |
| Mean Reversion | 🟢 Green | Fade extremes, buy dips |
| Range/Bands | 🟣 Purple | Bollinger, Keltner |
| Breakout | 🟠 Orange | Donchian, ATR channels |
| RSI Extreme | 🔴 Red | Oversold/overbought |
| MA Cross | 🔵 Cyan | SMA/EMA crossovers |
| Time Series | 🩷 Pink | ARIMA, AR forecasts |
| Cross-Sectional | 🟢 Lime | Rank-based relative value |
| Meta-Model | 🟡 Gold | OLS ensemble blends |

**Use this to:** Ensure portfolio diversification (don't pick 5 blue dots)

---

## 🔍 Workflow Examples

### Portfolio Construction (5 min)
1. Filter: ✅ **"★ Champions only"**
2. Sort table: **"Sharpe" descending**
3. Scatter check: Click top 3-5 → verify in "Smooth" quadrant (Sharpe vs DD)
4. Color diversity: Ensure 3+ different families
5. Detail panels: Review calibration + rolling IC for decay

### Model Health Check (30 sec)
1. Summary panel: Check **profitable count / with-trades ratio**
2. Return vs Hit scatter: Majority in bottom-left? → Tough market
3. Sharpe vs DD scatter: Cluster below Sharpe 0? → Systemic issue

### Research New Variants (10 min)
1. Search: **"momentum_"** (finds all momentum variants)
2. Table: Sort by **"Trades"** to see which has most data
3. Scatter: Do variants cluster? → Redundant, pick one
4. Detail: Compare calibration curves → Which is best calibrated?

### Risk Screening (2 min)
1. Sharpe vs DD scatter: Identify points with **DD < -20%**
2. Click those points → Detail panel
3. Check **n_open_at_end**: Many open positions? → Risk of concentration
4. Decide: Retire or reduce position size

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| **Scatter plots empty** | Check filters — try "Min Trades = 0" |
| **Points overlap** | Hover to see tooltip, or click to open detail |
| **Page slow** | Hard refresh (Ctrl+Shift+R), or check markouts.json size |
| **Data stale** | Click "↻ Refresh" button (top-right) |
| **Chart won't render** | Check browser console (F12) for errors |
| **Mobile: charts tiny** | Rotate device to landscape, or zoom out |

---

## 📱 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Click scatter point** | Open detail panel for that model |
| **Arrow Up/Down** | Navigate prev/next model in detail panel |
| **Escape** | Close detail panel |

---

## 🎓 Pro Tips

1. **Color clustering:** If one family dominates a quadrant, that strategy type has an edge
2. **Size matters:** Large dots = high trade count = more confidence in metrics
3. **Outliers:** Models far from clusters are either exceptional or broken — investigate
4. **Quadrant drift:** Re-run dashboard weekly, watch models migrate between quadrants
5. **Hit rate paradox:** 50% is random baseline — below that can still be profitable if wins > losses
6. **DD tolerance:** -10% is typical, -20% is aggressive, -30% is too risky for live

---

## 📖 Documentation Map

| Doc | Purpose |
|-----|---------|
| **markout_dashboard_enhancements_spec.md** | Full technical spec (for devs) |
| **markout_dashboard_v30_deployment.md** | Deployment checklist + testing |
| **markout_v30_summary_for_mm.md** | Executive summary (this overview) |
| **markout_v30_quick_reference.md** | This card (daily use) |

---

## 🔗 Quick Links

- **Dashboard:** http://localhost:8080/markouts.html
- **Refresh data:** Run `python3 src/markout_eval_publish.py` (takes ~30s for 47 models)
- **Logs:** Check `/tmp/http_server.log` if dashboard won't load
- **Source:** `/home/nixos/Prod/V1/src/markouts_v29.*`

---

**Print this card for desk reference!**
