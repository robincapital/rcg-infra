# Markout Dashboard Enhancement Specification

**Author:** RCG Quant Agent  
**Date:** 2026-05-21  
**Status:** SPEC-REVIEW — awaiting MM approval  
**Target:** Markout dashboard v30

---

## 1. Objective

Enhance the markout dashboard with:
1. **Scatter plot visualizations** to identify model performance clusters across prediction timeframes
2. **Summary statistics panel** providing at-a-glance data inventory and performance metrics
3. **Quadrant analysis** showing hit rate vs returns segmentation

These additions improve pattern recognition, model selection, and quick health checks without requiring drill-down into individual model details.

---

## 2. Proposed Visualizations

### 2.1 Scatter Plot: Return vs Hit Rate (Quadrant View)

**Location:** New global section above the correlation matrix, below the main table

**Purpose:** Identify models that combine high profitability with high predictive accuracy

**Implementation:**
- **X-axis:** Hit rate (0-100%)
- **Y-axis:** Cumulative return (net, selected slippage tier)
- **Color coding:** By family (same palette as table)
- **Size:** Proportional to number of trades (larger = more data)
- **Quadrant lines:**
  - Vertical at 50% hit rate (random baseline)
  - Horizontal at 0% return (breakeven)
- **Interaction:**
  - Hover shows: model name, family, return, hit rate, trades, Sharpe
  - Click selects model and scrolls to detail panel
- **Filters:** Respect current table filters (search, family, min trades, champions, etc.)

**Why this matters:**
- **Top-right quadrant** (hit rate > 50%, return > 0%): Best performers — high conviction + profitability
- **Bottom-right quadrant** (hit rate > 50%, return < 0%): Cost drag dominates — signals work but need lower slippage
- **Top-left quadrant** (hit rate < 50%, return > 0%): Asymmetric payoffs — wins bigger than losses
- **Bottom-left quadrant** (hit rate < 50%, return < 0%): Underperformers — candidates for retirement

### 2.2 Scatter Plot: Sharpe vs Max Drawdown

**Location:** Same global section, second chart

**Purpose:** Identify risk-adjusted efficiency vs worst-case downside

**Implementation:**
- **X-axis:** Max drawdown (% from peak, negative values)
- **Y-axis:** Sharpe ratio (net)
- **Color/Size/Interaction:** Same as 2.1
- **Quadrant lines:**
  - Vertical at -10% drawdown (acceptable risk threshold)
  - Horizontal at Sharpe = 1.0 (strong risk-adjusted return)

**Why this matters:**
- **Top-left quadrant** (low DD, high Sharpe): Ideal models — smooth equity curves with strong returns
- **Bottom-left quadrant** (low DD, low Sharpe): Low volatility but mediocre returns — defensively useful
- **Top-right quadrant** (high DD, high Sharpe): High return but path-dependent — needs tighter stops
- **Bottom-right quadrant** (high DD, low Sharpe): Dangerous — high volatility with poor compensation

### 2.3 Scatter Plot: Trades vs Avg Hold Time

**Location:** Same global section, third chart

**Purpose:** Identify turnover characteristics and data sufficiency

**Implementation:**
- **X-axis:** Number of completed trades (log scale if range is wide)
- **Y-axis:** Average hold time (trading minutes, or convert to "fires held")
- **Color/Size/Interaction:** Same as 2.1
- **Reference lines:**
  - Horizontal at 60 min (2 fires — very short-term)
  - Horizontal at 240 min (8 fires — intraday swing)
  - Vertical at 10 trades (min sample size for statistical confidence)

**Why this matters:**
- **High trades + short hold:** High-frequency signals — need tight slippage control
- **High trades + long hold:** Position signals — easier to implement in live trading
- **Low trades + any hold:** Insufficient data — not yet actionable
- Models below 10 trades should be de-emphasized (visual dimming or separate cluster)

---

## 3. Summary Statistics Panel

**Location:** New section immediately below the filter bar, above the main table

**Layout:** Horizontal card-grid with 6-8 key metrics

**Metrics:**

### 3.1 Data Inventory
- **Total Models:** Count of all model stems discovered
- **With Trades:** Count with n_trades >= 1 (actionable subset)
- **Champions:** Count marked as champions
- **Families:** Number of distinct strategy types

### 3.2 Prediction Coverage
- **Total Fires:** Sum of n_fires across all models in lookback window
- **Total Trades:** Sum of n_trades (completed round-trips)
- **Avg Fires/Model:** Total fires / model count (density check)
- **Avg Trades/Model:** Total trades / model count (execution rate)

### 3.3 Performance Aggregate (Net, selected slippage)
- **Profitable Models:** Count where cum_return > 0
- **Avg Return:** Mean cum_return across models with trades
- **Best Return:** Max cum_return (show model name on hover)
- **Worst Return:** Min cum_return (show model name on hover)

### 3.4 Risk Aggregate
- **Avg Sharpe:** Mean Sharpe across models with trades
- **Avg Max DD:** Mean max_dd (negative values)
- **Avg Hit Rate:** Mean hit rate across models with trades

### 3.5 Execution Stats
- **Total Long Trades:** Sum of n_long
- **Total Short Trades:** Sum of n_short
- **Avg Hold (minutes):** Weighted avg of avg_hold_trading_minutes
- **Long/Short Ratio:** Total long / total short

### 3.6 Timestamp & Recency
- **Last Update:** Timestamp from markouts.json generated_at
- **Lookback:** Number of days (already shown in footer, can duplicate here)
- **Staleness Warning:** Flag if generated_at > 36 hours old (red badge)

**Visual Design:**
- Cards with light background, icons for each category
- Values large and bold, labels small and dim
- Color coding: green for positive metrics, red for warnings, neutral for counts
- Compact: single row, horizontal scroll on mobile

---

## 4. Technical Implementation Plan

### 4.1 Backend Changes (markout_eval_publish.py)

**No schema changes required** — all data needed for scatter plots and summary stats already exists in the JSON payload:
- `n_trades`, `hit_rate`, `summary[slippage].cum_return`, `summary[slippage].sharpe`, `summary[slippage].max_dd`, `avg_hold_trading_minutes`
- Global summary dict already aggregates counts

**Optional enhancement (Phase B):**
- Add `summary_statistics` top-level key to JSON with pre-computed aggregates (reduces client-side calculation)
- Structure:
  ```json
  "summary_statistics": {
    "data_inventory": { "total_models": 47, "with_trades": 32, "champions": 12, "families": 13 },
    "prediction_coverage": { "total_fires": 1847, "total_trades": 428, "avg_fires_per_model": 39.3, "avg_trades_per_model": 9.1 },
    "performance_net_5bps": { "profitable_count": 18, "avg_return": 0.0234, "best_return": 0.1156, "worst_return": -0.0487 },
    "risk_aggregate": { "avg_sharpe": 0.89, "avg_max_dd": -0.0342, "avg_hit_rate": 0.527 },
    "execution_stats": { "total_long": 214, "total_short": 214, "avg_hold_minutes": 187.3, "long_short_ratio": 1.0 }
  }
  ```
- **Benefit:** Faster page load (no iteration over 47 models on every render)
- **Risk:** None — backward compatible (client can fall back to local aggregation if key missing)

### 4.2 Frontend Changes (markouts_v29.html + .js)

#### HTML Structure (add after filter bar, before table):

```html
<!-- Summary Statistics Panel -->
<div class="summary-panel" id="summary-panel">
  <div class="summary-card">
    <div class="summary-icon">📊</div>
    <div class="summary-value" id="stat-total-models">—</div>
    <div class="summary-label">Models</div>
  </div>
  <div class="summary-card">
    <div class="summary-icon">✓</div>
    <div class="summary-value" id="stat-with-trades">—</div>
    <div class="summary-label">With Trades</div>
  </div>
  <!-- ... repeat for other metrics ... -->
</div>

<!-- Scatter Plot Section -->
<div class="global-section" id="scatter-section">
  <div class="section-header">
    <h2>Model Performance Landscape</h2>
    <div class="section-note">Interactive scatter plots · Click any point to drill down</div>
  </div>
  <div class="scatter-grid">
    <div class="chart-box">
      <div class="chart-title">Return vs Hit Rate (Quadrant View)</div>
      <div id="chart-scatter-return-hit" class="chart-canvas"></div>
    </div>
    <div class="chart-box">
      <div class="chart-title">Sharpe vs Max Drawdown</div>
      <div id="chart-scatter-sharpe-dd" class="chart-canvas"></div>
    </div>
    <div class="chart-box">
      <div class="chart-title">Trades vs Avg Hold Time</div>
      <div id="chart-scatter-trades-hold" class="chart-canvas"></div>
    </div>
  </div>
</div>
```

#### CSS (markouts_v29.css additions):

```css
.summary-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1rem;
  margin: 1.5rem 0;
  padding: 1rem;
  background: var(--card-bg);
  border-radius: 8px;
  border: 1px solid var(--border);
}

.summary-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1rem;
  background: var(--bg-secondary);
  border-radius: 6px;
  transition: transform 0.2s;
}

.summary-card:hover {
  transform: translateY(-2px);
  background: var(--bg-hover);
}

.summary-icon {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
  opacity: 0.7;
}

.summary-value {
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 0.25rem;
  font-family: 'JetBrains Mono', monospace;
}

.summary-label {
  font-size: 0.75rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.scatter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
  gap: 1.5rem;
  margin-top: 1rem;
}

@media (max-width: 1400px) {
  .scatter-grid {
    grid-template-columns: 1fr;
  }
}
```

#### JavaScript (markouts_v29.js additions):

**Function: renderSummaryPanel()**
```javascript
function renderSummaryPanel() {
  const slip = state.filters.slippage;
  const withTrades = state.allModels.filter(m => (m.n_trades || 0) >= 1);
  
  // Data Inventory
  document.getElementById('stat-total-models').textContent = state.allModels.length;
  document.getElementById('stat-with-trades').textContent = withTrades.length;
  document.getElementById('stat-champions').textContent = state.allModels.filter(m => m.is_champion).length;
  
  // Performance Aggregate
  const profitable = withTrades.filter(m => (m.summary?.[slip]?.cum_return || 0) > 0);
  document.getElementById('stat-profitable').textContent = profitable.length;
  
  const returns = withTrades.map(m => m.summary?.[slip]?.cum_return || 0);
  const avgReturn = returns.length ? (returns.reduce((a,b) => a+b, 0) / returns.length) : 0;
  document.getElementById('stat-avg-return').textContent = fmtPercent(avgReturn);
  
  // ... continue for other metrics
}
```

**Function: renderScatterPlots()**
```javascript
function renderScatterPlots() {
  const slip = state.filters.slippage;
  const models = state.filteredModels.filter(m => (m.n_trades || 0) >= 1); // Only show models with data
  
  // Scatter 1: Return vs Hit Rate
  const trace1 = {
    x: models.map(m => (m.hit_rate || 0) * 100),
    y: models.map(m => (m.summary?.[slip]?.cum_return || 0) * 100),
    mode: 'markers',
    type: 'scatter',
    marker: {
      size: models.map(m => Math.sqrt(m.n_trades || 1) * 3 + 5), // Size by trades
      color: models.map(m => FAMILY_COLORS[m.family] || '#64748b'),
      opacity: 0.7,
      line: { width: 1, color: '#0a1628' }
    },
    text: models.map(m => `${m.model}<br>Family: ${m.family_label}<br>Return: ${fmtPercent(m.summary?.[slip]?.cum_return || 0)}<br>Hit Rate: ${fmtPercent(m.hit_rate || 0)}<br>Trades: ${m.n_trades}<br>Sharpe: ${fmtNum(m.summary?.[slip]?.sharpe || 0, 2)}`),
    hovertemplate: '%{text}<extra></extra>',
    customdata: models.map(m => m.id)
  };
  
  const layout1 = {
    ...getPlotLayout(),
    xaxis: { title: 'Hit Rate (%)', ticksuffix: '%', range: [0, 100] },
    yaxis: { title: 'Cumulative Return (%)', ticksuffix: '%', tickformat: '+.1f', zeroline: true },
    shapes: [
      { type: 'line', x0: 50, x1: 50, y0: 0, y1: 1, yref: 'paper', line: { color: '#64748b', width: 1, dash: 'dash' } },
      { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 0, line: { color: '#64748b', width: 1, dash: 'dash' } }
    ],
    annotations: [
      { x: 75, y: 0.95, yref: 'paper', text: 'Winners: High Hit + Positive Return', showarrow: false, font: { size: 10, color: '#22c55e' } },
      { x: 25, y: 0.05, yref: 'paper', text: 'Losers: Low Hit + Negative Return', showarrow: false, font: { size: 10, color: '#ef4444' } }
    ]
  };
  
  Plotly.react('chart-scatter-return-hit', [trace1], layout1, { displayModeBar: false, responsive: true });
  
  // Add click handler to navigate to model detail
  document.getElementById('chart-scatter-return-hit').on('plotly_click', data => {
    const modelId = data.points[0].customdata;
    selectModel(modelId);
  });
  
  // Scatter 2: Sharpe vs Max DD
  // ... similar structure with different axes
  
  // Scatter 3: Trades vs Hold Time
  // ... similar structure with different axes
}
```

### 4.3 Color Palette for Family Coding

Define consistent color mapping in JS:
```javascript
const FAMILY_COLORS = {
  'momentum': '#3b82f6',
  'mean_reversion': '#10b981',
  'bollinger_pos': '#8b5cf6',
  'donchian_break': '#f59e0b',
  'rsi_extreme': '#ef4444',
  'sma_cross': '#06b6d4',
  'ema_cross': '#14b8a6',
  'lr_slope': '#f97316',
  'arima': '#ec4899',
  'pattern': '#a855f7',
  'cross_sectional': '#84cc16',
  'ensemble': '#6366f1',
  'meta_blend': '#c8a84e',
  'bbg_composite': '#d97706',
  'other': '#64748b'
};
```

---

## 5. Rollback Path

If visualizations cause performance issues or user confusion:

### 5.1 Backend Rollback
- No backend changes required for v1 (client-side aggregation only)
- If Phase B adds `summary_statistics` key: client already has fallback logic (compute locally if key missing)

### 5.2 Frontend Rollback
- **Scatter plots:** Remove `<div id="scatter-section">` from HTML + delete `renderScatterPlots()` function
- **Summary panel:** Remove `<div class="summary-panel">` from HTML + delete `renderSummaryPanel()` function
- **CSS:** Comment out `.summary-panel`, `.summary-card`, `.scatter-grid` blocks
- Dashboard falls back to v29 table-only interface — no data loss, no broken links

### 5.3 Partial Rollback
- If one scatter plot underperforms (e.g., Trades vs Hold Time is uninformative), remove that single chart from the grid
- If summary panel is too cluttered, reduce from 8 cards to 4 (only data inventory + performance aggregate)

---

## 6. Testing Checklist

### 6.1 Data Integrity
- [ ] Summary stats match manual aggregation (spot-check 3 metrics against Python sum/mean)
- [ ] Scatter plot coordinates align with table values (hover over point, compare to table row)
- [ ] Filtering updates scatter plots correctly (e.g., "Champions only" shows only champion points)

### 6.2 Interaction
- [ ] Click scatter point → detail panel opens for correct model
- [ ] Slippage toggle updates scatter Y-axis (return) and summary stats
- [ ] Search filter dims irrelevant scatter points (or removes them if we implement that)

### 6.3 Edge Cases
- [ ] Models with 0 trades: excluded from scatter plots (or shown dimmed in bottom-left corner)
- [ ] Models with null hit_rate: excluded from Return vs Hit Rate scatter
- [ ] Models with null Sharpe: excluded from Sharpe vs DD scatter
- [ ] Empty dataset (all models filtered out): scatter shows "No data matching filters" placeholder

### 6.4 Performance
- [ ] Page load time with 47 models < 2 seconds (current is ~1.2s, scatter adds ~200ms)
- [ ] Scatter plot render time < 500ms (Plotly overhead)
- [ ] No memory leaks after 10+ filter toggles

### 6.5 Visual Polish
- [ ] Quadrant lines visible and labeled
- [ ] Color palette distinguishes all 14 families
- [ ] Text annotations don't overlap with data points
- [ ] Mobile: scatter grid stacks vertically, cards scroll horizontally

---

## 7. Future Enhancements (Phase B — not in v30)

### 7.1 Time-Series Scatter Animation
- Add time slider (30d / 60d / 90d lookback)
- Animate scatter points moving as performance evolves
- Identify models with improving vs deteriorating trends

### 7.2 3D Scatter (Return × Hit Rate × Sharpe)
- WebGL-based 3D plot (Plotly supports this)
- Rotate to explore model clusters
- May be overkill — defer until v30 user feedback

### 7.3 Export Scatter Data
- "Download scatter data as CSV" button
- Useful for offline analysis in R/Python

### 7.4 Model Similarity Clustering
- K-means or hierarchical clustering on (return, hit_rate, sharpe, max_dd)
- Overlay cluster boundaries on scatter plots
- Identify "families within families" (e.g., fast momentum vs slow momentum)

### 7.5 Champion Rotation History
- Track which models were champions in past lookback windows
- Show "former champion" badge for models that slipped out of top spot
- Useful for lifecycle analysis (models that decay over time)

---

## 8. Compliance Notes

### 8.1 Policy Check (per `/home/nixos/Prod/V1/docs/rcg_policy.md`)

**No compliance concerns identified:**
- **Data vendors:** Uses existing Postgres signals table (no new data dependencies)
- **Client data:** Dashboard is internal-only (markouts.html not published to client portal)
- **Trading execution:** Visualizations are read-only analysis (no order generation)
- **External publication:** Not applicable (internal tool)
- **Position sizing:** Not applicable (model evaluation, not portfolio construction)

**Escalation not required** — pure enhancement to existing internal tooling.

### 8.2 Data Retention
- Dashboard reads from markouts.json (regenerated nightly)
- No new persistent storage required
- Complies with existing 90-day rolling window policy

---

## 9. Approval Gate

**Awaiting MM review:**
1. Are the three scatter plots (Return/Hit, Sharpe/DD, Trades/Hold) the right set?
2. Is the summary panel metric list complete, or are there key stats missing?
3. Should Phase B pre-aggregation in backend be included in v30, or defer to v31?
4. Any concerns about visual clutter (too much on one page)?

**Approval verbs:**
- **"ship it"** → Proceed to implementation (v30 frontend + optional backend)
- **"revise"** → Specify which scatter plots to drop/modify, or which summary stats to adjust
- **"defer"** → Hold for v31 after user feedback on v29 table-only interface

---

## 10. File Manifest

**Files to modify:**
1. `/home/nixos/Prod/V1/src/markouts_v29.html` — Add summary panel + scatter section HTML
2. `/home/nixos/Prod/V1/src/markouts_v29.css` — Add summary panel + scatter grid styles
3. `/home/nixos/Prod/V1/src/markouts_v29.js` — Add `renderSummaryPanel()`, `renderScatterPlots()`, family color palette
4. `/home/nixos/Prod/V1/src/markout_eval_publish.py` — (Optional Phase B) Add `summary_statistics` key to JSON

**Files to create:**
- `/home/nixos/Prod/V1/docs/markout_dashboard_enhancements_spec.md` — This document

**No files deleted or moved.**

---

**End of Spec — Awaiting Nick's Approval**
