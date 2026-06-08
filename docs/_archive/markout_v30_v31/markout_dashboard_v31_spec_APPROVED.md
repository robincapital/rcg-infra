# Markout Dashboard v31 — Complete Redesign (APPROVED SPEC)

**Date:** 2026-05-21  
**Status:** ✅ APPROVED - READY TO BUILD  
**Agent:** RCG Quant  
**MM Requirements:**
1. ✅ No composite score — show all raw metrics
2. ✅ Basic model descriptions (what it captures)
3. ✅ 5d/10d/30d rolling Sharpe
4. ✅ Avg trade perf + max gain/loss per trade + win/loss streaks
5. ✅ Champion board sorted by win streak + best return
6. ✅ Mobile-friendly (responsive design)

---

## Design Philosophy

**v30 Problems:**
- ❌ Large static plots taking too much space
- ❌ Can't click/dive deeper easily
- ❌ No model parameters or methodology visible
- ❌ Missing timestamps, rolling Sharpe, streak tracking
- ❌ No visual steering toward better models

**v31 Solutions:**
- ✅ **Compact card grid** - 3-4 per row desktop, 1 per row mobile
- ✅ **Visual steering** - color by win/loss streaks + performance
- ✅ **Rich tooltips** - methodology, parameters, timestamps on hover
- ✅ **Dynamic expand** - click card → full detail pane
- ✅ **Professional metrics** - all raw constituents, no composite score
- ✅ **Champion board** - top models by win streak + return
- ✅ **Mobile-first** - touch-friendly, responsive grid

---

## Layout Architecture

### Desktop (1200px+)
```
┌─────────────────────────────────────────────────────────────────────┐
│  HEADER + QUICK STATS (1 line, 8 metrics)                           │
│  FILTER TOOLBAR (search, family, slippage, sort options)            │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  🏆 CHAMPION BOARD (top 5 by win streak → return → sharpe)         │
│  [Card] [Card] [Card] [Card] [Card]  ← Horizontal scroll           │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  MODEL GRID (3-4 per row)                                           │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                                  │
│  │Card │ │Card │ │Card │ │Card │                                  │
│  └─────┘ └─────┘ └─────┘ └─────┘                                  │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                                  │
│  │Card │ │Card │ │Card │ │Card │                                  │
│  └─────┘ └─────┘ └─────┘ └─────┘                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Mobile (< 768px)
```
┌───────────────────┐
│  HEADER (stacked) │
│  QUICK STATS (2×4)│
│  FILTERS          │
└───────────────────┘
┌───────────────────┐
│  CHAMPION BOARD   │
│  [Card]           │
│  [Card]           │
│  [Card]           │
└───────────────────┘
┌───────────────────┐
│  MODEL GRID (1/row)│
│  ┌───────────────┐ │
│  │    Card       │ │
│  └───────────────┘ │
│  ┌───────────────┐ │
│  │    Card       │ │
│  └───────────────┘ │
└───────────────────┘
```

---

## Component Specifications

### 1. Quick Stats Bar (Compact)

**Desktop:** Single line, 8 metrics
```
📊 47   ✓ 25   ⭐ 5   🔥 8   📈 -1.9%   ⚡ -5.6   🔄 684   ⏱️ 7.6h
Models  Trades Leaders Hot    AvgRet     AvgSharpe Trades  AvgHold
```

**Mobile:** 2×4 grid
```
📊 47 Models      ✓ 25 w/Trades
⭐ 5 Leaders      🔥 8 Hot Streak
📈 -1.9% Avg Ret  ⚡ -5.6 Avg Sharpe
🔄 684 Trades     ⏱️ 7.6h Avg Hold
```

**Colors:**
- Green: Positive metrics
- Yellow: Neutral/mixed
- Red: Negative metrics

---

### 2. Champion Board

**Criteria (in order):**
1. **Win streak ≥ 3** (primary sort)
2. **Cumulative return** (secondary sort)
3. **Sharpe ratio** (tie-breaker)

**Show top 5 models** (horizontal scroll on mobile)

```
┌──────────────────────────────────────────────────────────────────────┐
│  🏆 LEADER BOARD                                        Sort: Streak▼│
├──────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │🟢 arima_20   │  │🟢 momentum_5 │  │🟡 ema_cross  │              │
│  │🔥 5W Streak  │  │🔥 4W Streak  │  │🔥 3W Streak  │              │
│  │━━━━━━━━━━━━━ │  │━━━━━━━━━━━━━ │  │━━━━━━━━━━━━━ │              │
│  │+2.45% │ 18.6│  │+1.87% │ 12.3│  │+1.01% │ 6.8 │              │
│  │43 trades     │  │67 trades     │  │29 trades     │              │
│  │Last: 2h ago  │  │Last: 1h ago  │  │Last: 3h ago  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

**Badges:**
- 🔥 = Win streak (number of consecutive winning trades)
- ⭐ = Champion (family best)
- 🟢 = Win streak ≥ 3
- 🟡 = Win streak 1-2 or break-even
- 🔴 = Loss streak ≥ 2

---

### 3. Model Card (Compact View)

```
┌─────────────────────────────────────────────────────────────────┐
│ 🟢 arima_20                                         🔥 5W Streak │ ← Border color by streak/perf
├─────────────────────────────────────────────────────────────────┤
│ Time-Series Forecasting · Captures mean reversion in volatility│ ← What it captures
│                                                                 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                    │ ← 7d sparkline
│                                                                 │
│ ┌─────────────┬─────────────┬─────────────┬─────────────┐    │
│ │ Return      │ Sharpe      │ Hit Rate    │ Max DD      │    │
│ │ +2.45%      │ 18.6        │ 55.8%       │ -1.2%       │    │
│ │ Gross:+3.2% │ 5d: 21.3    │ L:59% S:52% │ Avg: -0.6%  │    │
│ │             │ 10d: 19.8   │             │             │    │
│ │             │ 30d: 18.6   │             │             │    │
│ └─────────────┴─────────────┴─────────────┴─────────────┘    │
│                                                                 │
│ ┌─────────────┬─────────────┬─────────────┬─────────────┐    │
│ │ Trades      │ Avg Trade   │ Max Gain    │ Max Loss    │    │
│ │ 43          │ +0.057%     │ +0.82%      │ -0.34%      │    │
│ │ L:22 S:21   │ Winners:+0.1│ (AAPL 5/21) │ (MSFT 5/18) │    │
│ │ Open: 2     │ Losers:-0.04│             │             │    │
│ └─────────────┴─────────────┴─────────────┴─────────────┘    │
│                                                                 │
│ Last signal: 2h ago · Last trade: 4h ago                       │ ← Timestamps
└─────────────────────────────────────────────────────────────────┘
```

**Border Colors:**
- 🟢 Green: Win streak ≥ 3 OR (Sharpe > 2 AND return > 0)
- 🟡 Yellow: Profitable but no streak OR (Sharpe 0-2)
- 🔴 Red: Loss streak ≥ 2 OR (return < 0 AND Sharpe < 0)
- ⚪ Gray: < 10 trades

**Card Click:** Expands to full detail pane (see §4)

---

### 4. Hover Tooltip (Rich Metadata)

```
╔══════════════════════════════════════════════════════════════════╗
║ arima_20 | Time-Series Forecasting                               ║
╠══════════════════════════════════════════════════════════════════╣
║ WHAT IT CAPTURES                                                 ║
║ • Mean reversion in short-term volatility spikes                 ║
║ • Detects over-reactions to news/events                          ║
║ • Works best in low-VIX environments                             ║
║                                                                  ║
║ METHODOLOGY                                                      ║
║ • ARIMA(2,1,1) on 20-bar rolling returns                        ║
║ • Fit on 100-bar lookback window                                ║
║ • Entry: Forecast z-score > 1.5 σ                               ║
║ • Exit: Mean reversion OR 8-hour timeout                        ║
║                                                                  ║
║ PARAMETERS                                                       ║
║ • lookback: 20 bars (30-minute resample)                        ║
║ • p=2, d=1, q=1 (order)                                         ║
║ • entry_threshold: 1.5 σ                                        ║
║ • exit_timeout: 480 minutes                                     ║
║ • refit_interval: 24 hours                                      ║
║                                                                  ║
║ TIMESTAMPS                                                       ║
║ • Last signal fired: 2026-05-21 14:32 ET (2h 18m ago)          ║
║ • Last trade closed: 2026-05-21 12:14 ET (4h 36m ago)          ║
║ • Model last trained: 2026-05-20 02:00 ET (1d 15h ago)         ║
║ • Data current through: 2026-05-21 16:00 ET (47m ago)          ║
║                                                                  ║
║ ROLLING PERFORMANCE                                              ║
║ • 5d Sharpe:  21.3 ↑ (+2.5 vs 10d)                             ║
║ • 10d Sharpe: 19.8 ↑ (+1.2 vs 30d)                             ║
║ • 30d Sharpe: 18.6 (baseline)                                   ║
║ • Trend: Improving ✓                                            ║
║                                                                  ║
║ RECENT STREAK                                                    ║
║ • Current: 5-trade win streak 🔥                                ║
║ • Last 10: W W W W W L W W L W (70% hit)                       ║
║ • Best streak: 7 wins (3 days ago)                             ║
║ • Worst streak: 2 losses (6 days ago)                          ║
╚══════════════════════════════════════════════════════════════════╝

Click to expand full detail panel
```

---

### 5. Expanded Detail Pane

**Replaces grid when card clicked**

```
┌─────────────────────────────────────────────────────────────────────┐
│ ◀ Back to Grid          arima_20 | Time-Series Forecasting         │
│                                                          🔥 5W ⭐    │
├─────────────────────────────────────────────────────────────────────┤
│ Time-Series Forecasting · Captures mean reversion in volatility    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ ┌──────────────────────────────────────────────────────────────┐  │
│ │ KEY PERFORMANCE METRICS                                       │  │
│ │ ┌──────────┬──────────┬──────────┬──────────┬──────────┐    │  │
│ │ │ Return   │ Sharpe   │ Hit Rate │ Max DD   │ Trades   │    │  │
│ │ │ +2.45%   │ 18.6     │ 55.8%    │ -1.2%    │ 43       │    │  │
│ │ │ Gross:   │ 5d: 21.3 │ Long:59% │ Avg:-0.6%│ L:22 S:21│    │  │
│ │ │ +3.21%   │10d: 19.8 │Short:52% │          │ Open: 2  │    │  │
│ │ │          │30d: 18.6 │          │          │          │    │  │
│ │ └──────────┴──────────┴──────────┴──────────┴──────────┘    │  │
│ └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│ ┌──────────────────────────────────────────────────────────────┐  │
│ │ TRADE PERFORMANCE DETAIL                                      │  │
│ │ ┌──────────────┬──────────────┬──────────────┬────────────┐ │  │
│ │ │ Avg Trade    │ Max Gain     │ Max Loss     │ Streak     │ │  │
│ │ │ +0.057%      │ +0.82%       │ -0.34%       │ 5W 🔥      │ │  │
│ │ │ Winners:+0.1%│ AAPL 5/21    │ MSFT 5/18    │ Best: 7W   │ │  │
│ │ │ Losers:-0.04%│ 135m hold    │ 98m hold     │ Worst: 2L  │ │  │
│ │ │ Payoff: 2.5x │              │              │ Last 10:   │ │  │
│ │ │              │              │              │ WWWWWLWWLW │ │  │
│ │ └──────────────┴──────────────┴──────────────┴────────────┘ │  │
│ └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│ ┌───────────────────────────────┬───────────────────────────────┐ │
│ │ EQUITY CURVE (Net vs Gross)   │ DRAWDOWN PROFILE             │ │
│ │ [Plotly interactive chart]    │ [Plotly chart]               │ │
│ │                               │                               │ │
│ └───────────────────────────────┴───────────────────────────────┘ │
│                                                                     │
│ ┌───────────────────────────────┬───────────────────────────────┐ │
│ │ CALIBRATION (10 buckets)      │ ROLLING IC (30-day window)   │ │
│ │ [Plotly bar chart]            │ [Plotly line + markers]      │ │
│ │                               │                               │ │
│ └───────────────────────────────┴───────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ TOP TICKER CONTRIBUTIONS (Net P&L, top 20)                     │ │
│ │ [Plotly horizontal bar chart]                                   │ │
│ │                                                                 │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ METHODOLOGY & PARAMETERS (from tooltip, permanently visible)    │ │
│ │ [Same rich content as hover tooltip]                            │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ RECENT TRADES (Last 15, expandable to 50)                       │ │
│ │ Date/Time │ Ticker │ Dir │ Entry │ Exit │ Hold │ Return │ Reason│ │
│ │ 5/21 14:32│ AAPL   │ L   │161.20│162.52│ 135m │ +0.82% │ Revert│ │
│ │ 5/21 11:45│ MSFT   │ S   │410.33│409.12│  98m │ +0.29% │ Target│ │
│ │ ...                                                              │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ◀ Prev Model                                           Next Model ▶│
└─────────────────────────────────────────────────────────────────────┘
```

**Navigation:**
- **◀ Back** (top-left) → returns to grid
- **◀ Prev / Next ▶** (bottom) → navigate filtered models
- **Keyboard:** Arrow keys, Escape to close

---

### 6. Filter Toolbar

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🔍 Search: [____________]  📁 Family: [All Types ▼]                │
│                                                                     │
│ Slippage: ○ 0bps  ● 5bps  ○ 10bps                                 │
│                                                                     │
│ ☑ Champions only   ☑ Has trades (≥10)   ☑ Win streak only         │
│                                                                     │
│ Sort: [Win Streak ▼] [Return] [Sharpe] [Hit Rate] [Last Signal]   │
│                                                                     │
│ Show: 🟢 Hot (streak ≥3) | 🟡 Warm (streak 1-2) | 🔴 Cold (loss) | ⚪ All │
└─────────────────────────────────────────────────────────────────────┘
```

**Default:** Sort by win streak (descending)

---

## Data Requirements (Backend Changes)

### New Fields in markouts.json

```json
{
  "models": [
    {
      "model": "arima_20",
      
      // NEW: Model description
      "description": "Time-Series Forecasting · Captures mean reversion in volatility",
      "captures": "Mean reversion in short-term volatility spikes. Detects over-reactions to news/events. Works best in low-VIX environments.",
      
      // NEW: Methodology
      "methodology": {
        "summary": "ARIMA(2,1,1) on 20-bar rolling returns",
        "entry": "Forecast z-score > 1.5 σ",
        "exit": "Mean reversion OR 8-hour timeout",
        "parameters": {
          "lookback": 20,
          "p": 2,
          "d": 1,
          "q": 1,
          "entry_threshold": 1.5,
          "exit_timeout_minutes": 480,
          "refit_interval_hours": 24
        }
      },
      
      // NEW: Timestamps
      "timestamps": {
        "last_signal": "2026-05-21T14:32:00",
        "last_trade_close": "2026-05-21T12:14:00",
        "model_trained": "2026-05-20T02:00:00",
        "data_through": "2026-05-21T16:00:00"
      },
      
      // NEW: Rolling Sharpe (multiple windows)
      "rolling_sharpe": {
        "5d": 21.3,
        "10d": 19.8,
        "30d": 18.6,
        "trend": "improving"  // or "declining" or "stable"
      },
      
      // NEW: Trade performance detail
      "trade_performance": {
        "avg_trade_pct": 0.057,
        "avg_winner_pct": 0.102,
        "avg_loser_pct": -0.041,
        "payoff_ratio": 2.49,  // avg_winner / abs(avg_loser)
        "max_gain_pct": 0.82,
        "max_gain_ticker": "AAPL",
        "max_gain_date": "2026-05-21",
        "max_loss_pct": -0.34,
        "max_loss_ticker": "MSFT",
        "max_loss_date": "2026-05-18"
      },
      
      // NEW: Win/loss streak tracking
      "streak": {
        "current": 5,          // Positive = wins, negative = losses
        "current_type": "win",  // "win" or "loss"
        "best_win_streak": 7,
        "worst_loss_streak": 2,
        "last_10": "WWWWWLWWLW"  // W=win, L=loss
      },
      
      // NEW: Hit rate by direction
      "hit_rate_long": 0.591,
      "hit_rate_short": 0.524,
      
      // NEW: Recent trades (expand from 10 to 15)
      "recent_trades": [
        {
          "datetime": "2026-05-21T14:32:00",
          "ticker": "AAPL",
          "direction": "long",
          "entry_price": 161.20,
          "exit_price": 162.52,
          "hold_minutes": 135,
          "return_pct": 0.82,
          "exit_reason": "mean_reversion"
        },
        // ... up to 15
      ],
      
      // KEEP ALL EXISTING FIELDS
      "horizon": "n/a",
      "family": "arima",
      "is_champion": true,
      "n_trades": 43,
      "n_long": 22,
      "n_short": 21,
      "n_open_at_end": 2,
      "hit_rate": 0.558,
      "avg_hold_trading_minutes": 408,
      "summary": { ... },
      "equity_gross": [ ... ],
      "equity_net_5bps": [ ... ],
      // ... etc
    }
  ]
}
```

---

## Champion Board Logic

**Sort Priority:**
1. **Current win streak** (descending)
   - Filter: `streak.current_type == "win" && streak.current >= 3`
2. **Cumulative return** (descending)
3. **Sharpe ratio** (descending)

**Show top 5 models** that meet criteria

**If < 5 models with win streaks ≥ 3:**
- Fill remaining slots with best return + Sharpe

---

## Mobile Responsiveness

### Breakpoints
- **Desktop:** ≥ 1200px → 4 cards per row
- **Tablet:** 768-1199px → 2-3 cards per row
- **Mobile:** < 768px → 1 card per row (full width)

### Touch Optimizations
- **Card tap:** Opens detail pane (replaces click)
- **Swipe left/right:** Navigate prev/next model in detail view
- **Pinch/zoom:** Disabled (fixed viewport)
- **Tooltip on touch:** Long-press (500ms) shows tooltip

### Mobile-Specific Layout
- **Quick stats:** 2×4 grid instead of 1×8 line
- **Filter toolbar:** Collapsible accordion (closed by default)
- **Champion board:** Horizontal scroll with snap
- **Detail pane:** Full-screen overlay with ◀ Back button

---

## Implementation Plan

### Phase 1: Core Redesign (4 hours)
- ✅ Compact card grid with responsive layout
- ✅ Visual border colors (green/yellow/red by streak + perf)
- ✅ Mini sparklines in cards
- ✅ Expandable detail panes
- ✅ Champion board (top 5 by streak + return)
- ✅ Mobile breakpoints + touch handlers

### Phase 2: Backend Data (2 hours, parallel)
- ✅ Add model descriptions ("what it captures")
- ✅ Add methodology summaries
- ✅ Add rolling Sharpe (5d/10d/30d)
- ✅ Add trade performance detail (avg, max gain/loss)
- ✅ Add streak tracking (current, best, worst, last 10)
- ✅ Add timestamps (signal, trade, trained)
- ✅ Compute payoff ratios

### Phase 3: Rich Tooltips (1.5 hours)
- ✅ Hover tooltips with methodology + parameters
- ✅ Timestamp formatting ("2h ago")
- ✅ Rolling Sharpe trend indicators (↑ ↓)
- ✅ Streak visualization (WWWLW)

### Phase 4: Polish (0.5 hours)
- ✅ Mobile testing (iOS Safari, Android Chrome)
- ✅ Keyboard navigation (arrows, Escape)
- ✅ Loading states
- ✅ Error handling

**Total: ~8 hours to production-ready v31**

---

## Rollback Strategy

**Files:**
- `markouts_v30_backup.{html,css,js}` ← Keep v30 as backup
- `markouts.{html,css,js}` ← Deploy v31 here
- If issues: `cp markouts_v30_backup.* outputs/markouts.*`

---

## Success Criteria

1. ✅ Page load < 2 seconds with 50 models
2. ✅ Card grid uses 85%+ of viewport (minimal waste)
3. ✅ Click card → detail pane < 300ms
4. ✅ Hover tooltip appears < 100ms
5. ✅ Win streaks clearly visible (🔥 badge)
6. ✅ Champion board auto-highlights hot models
7. ✅ Mobile: 1-column layout, touch navigation works
8. ✅ MM can assess 10 models in < 90 seconds
9. ✅ All raw metrics visible (no hidden composite scores)

---

## APPROVED - READY TO BUILD ✅

**MM Confirmation Received:**
1. No composite score ✓
2. Basic descriptions (what it captures) ✓
3. 5d/10d/30d rolling Sharpe ✓
4. Avg trade + max gain/loss + streaks ✓
5. Champion board by streak + return ✓
6. Mobile-friendly ✓

**Starting build now...**
