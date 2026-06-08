# Markout Dashboard v31 — Complete Redesign Spec

**Date:** 2026-05-21  
**Status:** 🔨 SPECIFICATION (awaiting approval)  
**Agent:** RCG Quant  
**Request:** MM feedback on v30 - "not functional, needs dynamic interactivity, better space use, professional stats"

---

## Design Philosophy

**v30 Problems:**
- ❌ Large static plots taking too much space
- ❌ Can't click/dive deeper easily
- ❌ No model parameters or methodology visible
- ❌ Missing timestamps, rolling Sharpe, conviction scoring
- ❌ No visual steering toward better models
- ❌ Not professional enough for client-facing use

**v31 Solutions:**
- ✅ **Compact card grid** - 3-4 models per row, expand on click
- ✅ **Visual conviction scoring** - color-coded borders (green/yellow/red)
- ✅ **Rich tooltips** - parameters, methodology, timestamps on hover
- ✅ **Dynamic charts** - mini sparklines in cards, full charts on expand
- ✅ **Professional metrics** - rolling Sharpe (7d/30d/90d), last run, IC trends
- ✅ **Smart defaults** - auto-sort by conviction score, highlight champions

---

## Layout Architecture

### Page Structure (Top → Bottom)

```
┌─────────────────────────────────────────────────────────────────────┐
│  HEADER                                                              │
│  • Title + Last Refresh                                             │
│  • Quick Stats Bar (8 metrics, compact, 1 line)                     │
│  • Filter Toolbar (search, family, slippage, toggles)               │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  CHAMPION SPOTLIGHT (if champions exist)                            │
│  • Top 3 champions in horizontal cards                              │
│  • Mini equity curve + key metrics                                  │
│  • Click to expand                                                  │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  MODEL GRID (main content)                                          │
│  • Compact cards (3-4 per row)                                      │
│  • Conviction score border color (green/yellow/red)                 │
│  • Mini sparkline + 4 key metrics                                   │
│  • Hover: tooltip with parameters + methodology                     │
│  • Click: expand to full detail pane (replaces grid temporarily)    │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  CORRELATION HEATMAP (collapsible section)                          │
│  • Only visible when 3+ models selected/filtered                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Specifications

### 1. Quick Stats Bar (Compact)

**Layout:** Single horizontal strip, 8 metrics, icon + value + label

```
📊 47   ✓ 25   ⭐ 9   🎯 36%   📈 -1.9%   ⚡ -5.6   🔄 684   ⏱️ 7.6h
Models  Trades Champs Win%     AvgRet     AvgSharpe Trades  AvgHold
```

**Colors:**
- Green: Win% > 50%, AvgRet > 0, AvgSharpe > 1.0
- Yellow: Win% 40-50%, AvgRet -2% to 0%, AvgSharpe 0 to 1.0
- Red: Below thresholds

---

### 2. Champion Spotlight

**Show only if champions exist and not filtered out**

```
┌──────────────────────────────────────────────────────────────────────┐
│  🏆 CHAMPION MODELS                                                  │
├──────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ arima_20        │  │ momentum_fast   │  │ ema_cross_9_21  │     │
│  │ ━━━━━━━━━━━━    │  │ ━━━━━━━━━━━━    │  │ ━━━━━━━━━━━━    │     │
│  │ +2.45% | 18.6  │  │ +1.23% | 8.2   │  │ +0.89% | 5.4   │     │
│  │ 43 trades       │  │ 67 trades       │  │ 29 trades       │     │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

**Mini equity curve:** 7-day sparkline (green = positive, red = negative)  
**Click:** Expands to full detail pane

---

### 3. Model Card (Compact View)

```
┌─────────────────────────────────────────────────────────────────┐
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 🟢 arima_20                                      ⭐ Champion │ │ ← Green border = high conviction
│ ├─────────────────────────────────────────────────────────────┤ │
│ │ Time-Series (ARIMA/AR)                         │ ━━━━━━━━━  │ │ ← Family + mini equity curve
│ │                                                              │ │
│ │ Return: +2.45%  Sharpe: 18.6  Hit: 55.8%  DD: -1.2%        │ │ ← 4 key metrics
│ │ Trades: 43 (L:22/S:21)  Hold: 6.8h  Last: 2h ago           │ │ ← Execution stats + timestamp
│ │                                                              │ │
│ │ Conviction: 92/100 ████████████████░░░░                     │ │ ← Visual score bar
│ │ Rolling Sharpe: 7d=21.3 | 30d=18.6 | 90d=15.2              │ │ ← Time-decay trend
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Border Color (Conviction Score):**
- 🟢 Green (80-100): Deploy now
- 🟡 Yellow (60-79): Monitor closely
- 🔴 Red (0-59): Needs improvement
- ⚪ Gray: Insufficient data (< 10 trades)

**Performance Indicators (Raw, No Score):**
- Sharpe ratio (current)
- Cumulative return
- Hit rate
- Number of trades
- Max drawdown
- Current win/loss streak
- Rolling Sharpe (5d/10d/30d)
- Avg trade performance
- Max gain per trade
- Max loss per trade

**Hover Tooltip:**
```
╔═══════════════════════════════════════════════════════════╗
║ arima_20 | Time-Series Forecasting                        ║
╠═══════════════════════════════════════════════════════════╣
║ METHODOLOGY                                               ║
║ • ARIMA(2,1,1) on 20-bar returns                         ║
║ • Fit on rolling 100-bar window                          ║
║ • Entry: |z-score| > 1.5                                 ║
║ • Exit: Mean reversion or 8-hour timeout                 ║
║                                                           ║
║ PARAMETERS                                                ║
║ • lookback: 20 bars                                      ║
║ • p=2, d=1, q=1                                          ║
║ • entry_threshold: 1.5                                   ║
║ • exit_timeout: 480 min                                  ║
║                                                           ║
║ TIMESTAMPS                                                ║
║ • Last signal: 2026-05-21 14:32 ET (2h 18m ago)         ║
║ • Last trade: 2026-05-21 12:14 ET (4h 36m ago)          ║
║ • Model trained: 2026-05-20 02:00 ET (1d 15h ago)       ║
║                                                           ║
║ ROLLING SHARPE TREND                                      ║
║ • 7-day:  21.3 ↑ (+2.7 from 30d)                        ║
║ • 30-day: 18.6 ↑ (+3.4 from 90d)                        ║
║ • 90-day: 15.2                                           ║
║                                                           ║
║ CONVICTION BREAKDOWN                                      ║
║ • Sharpe contrib:    30/30 (max)                        ║
║ • Return contrib:    22/25                              ║
║ • Hit rate contrib:  18/20                              ║
║ • Sample size:       15/15 (max)                        ║
║ • Drawdown penalty:  -3/10                              ║
║ • TOTAL: 92/100 → HIGH CONVICTION                       ║
╚═══════════════════════════════════════════════════════════╝

Click to expand full detail panel
```

---

### 4. Expanded Detail Pane

**Replaces model grid when card is clicked**

```
┌─────────────────────────────────────────────────────────────────────┐
│ ◀ Back to Grid          arima_20 | Time-Series (ARIMA/AR)          │
│                                                          ⭐ Champion │
├─────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ PERFORMANCE METRICS                                             │ │
│ │ ┌──────────────┬──────────────┬──────────────┬──────────────┐  │ │
│ │ │ Cum Return   │ Sharpe Ratio │ Max Drawdown │ Hit Rate     │  │ │
│ │ │ +2.45%       │ 18.6         │ -1.2%        │ 55.8%        │  │ │
│ │ │ Gross: +3.21%│ 7d: 21.3     │ Max: -1.8%   │ Long: 59.1%  │  │ │
│ │ │              │ 30d: 18.6    │ Avg: -0.6%   │ Short: 52.4% │  │ │
│ │ │              │ 90d: 15.2    │              │              │  │ │
│ │ └──────────────┴──────────────┴──────────────┴──────────────┘  │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌───────────────────────────────┬───────────────────────────────┐ │
│ │ EQUITY CURVE (Net vs Gross)   │ DRAWDOWN PROFILE             │ │
│ │ [Plotly chart]                │ [Plotly chart]               │ │
│ └───────────────────────────────┴───────────────────────────────┘ │
│                                                                     │
│ ┌───────────────────────────────┬───────────────────────────────┐ │
│ │ CALIBRATION (10 buckets)      │ ROLLING IC (30-day)          │ │
│ │ [Plotly bar chart]            │ [Plotly line chart]          │ │
│ └───────────────────────────────┴───────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ TOP TICKER CONTRIBUTIONS (Horizontal bar, top 20)              │ │
│ │ [Plotly bar chart]                                              │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ METHODOLOGY & PARAMETERS                                        │ │
│ │ [Same content as hover tooltip, but permanently visible]        │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ RECENT TRADES (Last 10, collapsible table)                      │ │
│ │ [Table: Ticker | Entry | Exit | Hold | Return | Direction]      │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

**Navigation:**
- **◀ Back to Grid** button (top-left)
- **◀ ▶ Prev/Next** buttons (top-right, navigate within filtered set)
- **Keyboard:** Arrow keys, Escape to close

---

### 5. Filter Toolbar (Enhanced)

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🔍 Search: [____________]  📁 Family: [All Types ▼]                │
│                                                                     │
│ Slippage: ○ 0bps  ● 5bps  ○ 10bps                                 │
│                                                                     │
│ ☑ Champions only   ☑ Profitable only   ☑ Has trades (≥10)         │
│                                                                     │
│ Sort by: [Conviction ▼] [Return] [Sharpe] [Hit Rate] [Trades]     │
│                                                                     │
│ Conviction filter: 🟢 High (80+) | 🟡 Medium (60-79) | 🔴 Low (0-59) | ⚪ All │
└─────────────────────────────────────────────────────────────────────┘
```

**Default sort:** Conviction score (descending)

---

## Data Requirements (Backend Changes)

### New Fields Needed in markouts.json

```json
{
  "models": [
    {
      "model": "arima_20",
      
      // NEW: Methodology & Parameters
      "methodology": {
        "description": "ARIMA(2,1,1) time-series forecasting on 20-bar returns",
        "entry_logic": "|z-score| > 1.5 signals mean reversion opportunity",
        "exit_logic": "Mean reversion or 8-hour timeout",
        "parameters": {
          "lookback": 20,
          "p": 2,
          "d": 1,
          "q": 1,
          "entry_threshold": 1.5,
          "exit_timeout_minutes": 480
        }
      },
      
      // NEW: Timestamps
      "timestamps": {
        "last_signal": "2026-05-21T14:32:00",
        "last_trade": "2026-05-21T12:14:00",
        "model_trained": "2026-05-20T02:00:00",
        "data_through": "2026-05-21T16:00:00"
      },
      
      // NEW: Rolling Sharpe (multiple windows)
      "rolling_sharpe": {
        "7d": 21.3,
        "30d": 18.6,
        "90d": 15.2
      },
      
      // NEW: Hit rate by direction
      "hit_rate_long": 0.591,
      "hit_rate_short": 0.524,
      
      // NEW: Average drawdown (not just max)
      "avg_dd": -0.006,
      
      // NEW: Recent trades (last 10)
      "recent_trades": [
        {
          "ticker": "AAPL",
          "entry_time": "2026-05-21T09:30:00",
          "exit_time": "2026-05-21T11:45:00",
          "direction": "long",
          "hold_minutes": 135,
          "return_pct": 0.82,
          "exit_reason": "mean_reversion"
        },
        // ... up to 10
      ],
      
      // EXISTING FIELDS (keep all current data)
      "horizon": "n/a",
      "family": "arima",
      "is_champion": true,
      "n_trades": 43,
      // ... etc
    }
  ]
}
```

---

## Conviction Score Algorithm (Detailed)

```python
def calculate_conviction_score(model, slip='5bps'):
    """
    Conviction score: 0-100, higher = better deployment candidate
    
    Weights:
    - 30% Sharpe ratio (risk-adjusted return)
    - 25% Cumulative return (absolute performance)
    - 20% Hit rate (predictive accuracy)
    - 15% Sample size (confidence in estimates)
    - 10% Drawdown control (risk management)
    """
    summary = model['summary'][slip]
    
    # Component 1: Sharpe (0-5 maps to 0-30)
    sharpe = max(0, min(5, summary.get('sharpe', 0)))
    sharpe_contrib = (sharpe / 5.0) * 30
    
    # Component 2: Return (-10% to +10% maps to 0-25)
    ret = summary.get('cum_return', 0) * 100
    ret_contrib = max(0, min(25, ((ret + 10) / 20) * 25))
    
    # Component 3: Hit rate (40%-70% maps to 0-20)
    hit = model.get('hit_rate', 0) * 100
    hit_contrib = max(0, min(20, ((hit - 40) / 30) * 20))
    
    # Component 4: Sample size (10-100 trades maps to 0-15)
    trades = model.get('n_trades', 0)
    sample_contrib = max(0, min(15, ((trades - 10) / 90) * 15))
    
    # Component 5: Drawdown penalty (-20% to 0% maps to 0-10)
    max_dd = abs(summary.get('max_dd', 0) * 100)
    dd_contrib = max(0, min(10, (1 - max_dd / 20) * 10))
    
    total = sharpe_contrib + ret_contrib + hit_contrib + sample_contrib + dd_contrib
    
    return {
        'score': round(total, 1),
        'breakdown': {
            'sharpe': round(sharpe_contrib, 1),
            'return': round(ret_contrib, 1),
            'hit_rate': round(hit_contrib, 1),
            'sample_size': round(sample_contrib, 1),
            'drawdown': round(dd_contrib, 1)
        },
        'tier': 'high' if total >= 80 else 'medium' if total >= 60 else 'low'
    }
```

---

## Visual Design Standards

### Color Palette

**Conviction Tiers:**
- 🟢 High (80-100): `#22c55e` (green-500)
- 🟡 Medium (60-79): `#eab308` (yellow-500)
- 🔴 Low (0-59): `#ef4444` (red-500)
- ⚪ Insufficient data: `#64748b` (slate-500)

**Performance:**
- Positive: `#22c55e` (green)
- Negative: `#ef4444` (red)
- Neutral: `#64748b` (gray)

**Families:** (same 14-color palette as v30)

### Typography
- **Headers:** 18px DM Sans, semi-bold
- **Body:** 14px DM Sans, regular
- **Metrics:** 16px DM Sans Mono, bold
- **Labels:** 12px DM Sans, regular, dim color

### Spacing
- Card padding: 16px
- Grid gap: 20px
- Section margin: 32px

---

## Implementation Phases

### Phase 1 — Core Redesign (Priority)
- ✅ Compact model cards with conviction scoring
- ✅ Expandable detail panes
- ✅ Filter toolbar with conviction tier filter
- ✅ Quick stats bar
- ✅ Champion spotlight

**Time estimate:** 3-4 hours  
**Approval gate:** Spec review

### Phase 2 — Backend Enhancements (Parallel)
- ✅ Add methodology descriptions to models
- ✅ Add rolling Sharpe (7d/30d/90d)
- ✅ Add timestamps (last signal, last trade, model trained)
- ✅ Add recent trades (last 10)
- ✅ Compute conviction scores in JSON

**Time estimate:** 2 hours (modify markout_eval_publish.py)  
**Approval gate:** Schema review

### Phase 3 — Advanced Tooltips (After Phase 1)
- ✅ Rich hover tooltips with methodology + parameters
- ✅ Conviction score breakdown
- ✅ Rolling Sharpe trend indicators

**Time estimate:** 1 hour

---

## Rollback Strategy

**If v31 doesn't meet expectations:**
1. Keep v30 files as `markouts_v30_backup.*`
2. Deploy v31 to `markouts.html` (production)
3. If issues arise, revert via symlink swap
4. No data loss — all changes are additive

---

## Success Criteria

Dashboard is successful if:

1. ✅ **Page loads in < 2 seconds** (even with 50+ models)
2. ✅ **Card grid shows 80% of viewport** (no wasted space)
3. ✅ **Click any card → expanded pane in < 300ms**
4. ✅ **Hover tooltip appears in < 100ms** with full methodology
5. ✅ **Conviction scores steer user** (green cards = deploy now)
6. ✅ **Rolling Sharpe shows decay trends** (7d vs 30d vs 90d)
7. ✅ **Timestamps show recency** (last signal < 4h = active)
8. ✅ **MM can assess 10 models in < 2 minutes** (glance at cards)
9. ✅ **Client-ready presentation** (professional, no debug artifacts)

---

## Questions for MM (Before Build)

1. **Conviction score weights:** Are 30% Sharpe / 25% Return / 20% Hit / 15% Sample / 10% DD reasonable?
2. **Methodology source:** Should I pull from model docstrings, or do you want a central config file?
3. **Rolling Sharpe windows:** 7d/30d/90d good? Or prefer 1d/7d/30d?
4. **Recent trades limit:** Show last 10, or prefer last 20?
5. **Champion spotlight:** Auto-highlight top 3, or let you pin favorites?
6. **Mobile priority:** Should this work on iPad/mobile, or desktop-only for now?

---

**AWAITING "SHIP IT" BEFORE BUILDING**

Let me know if this spec meets your vision or if you want adjustments!
