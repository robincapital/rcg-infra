# Markout Dashboard v31 — Trade Details Enhancement

**Date:** 2026-05-21  
**Status:** 🔨 IN PROGRESS  
**Request:** MM feedback on v31 initial deployment  

---

## MM Feedback

1. ✅ "looks better" (overall design approved)
2. ❌ **Missing trade details** — need to see individual trades with:
   - Signal values (entry/exit)
   - Entry/exit prices
   - Holding period
   - Capital used
   - Symbols making up the performance
3. ❌ **Navigation issue** — detail view replaces page, should open in overlay/modal with easy close

---

## Changes Required

### 1. Trade Details Table (High Priority)

**Current state:** Detail pane shows aggregate metrics only  
**Required:** Scrollable table of all trades for the model with columns:

| Column | Source | Format | Example |
|--------|--------|--------|---------|
| Date/Time | Trade exit timestamp | MM/DD HH:MM | 05/21 14:32 |
| Ticker | Symbol | Text | AAPL |
| Direction | Long/Short | L / S | L |
| Signal (Entry) | Model score at entry | Float | 67.3 |
| Signal (Exit) | Model score at exit | Float | 42.1 |
| Entry Price | Fill price | $ | $161.20 |
| Exit Price | Fill price | $ | $162.52 |
| Hold Time | Minutes or hours | Auto-format | 2h 15m |
| Capital Used | Position size $ | $ | $12,450 |
| Return (%) | Net of slippage | % | +0.82% |
| Return ($) | Dollar P&L | $ | +$102.09 |
| Exit Reason | Why closed | Text | Mean reversion |

**Sorting:** Default newest → oldest (most recent trades first)  
**Filtering:** Add quick filters for L/S, winners/losers  
**Pagination:** Show 20 per page, load more button

### 2. Modal/Overlay Pattern (High Priority)

**Current state:** Detail view replaces entire page  
**Required:** Modal overlay that:
- Dims background (shows grid behind)
- Easy to close (X button top-right, click outside, Escape key)
- Keeps grid state (scroll position, filters)
- Can open multiple models sequentially without losing place

**Design:**
```
┌─────────────────────────────────────────────────────────────┐
│ [DIMMED GRID VISIBLE IN BACKGROUND]                         │
│                                                              │
│   ┌───────────────────────────────────────────────────────┐ │
│   │ ╳                arima_20 | Time-Series        🔥 5W ⭐│ │
│   ├───────────────────────────────────────────────────────┤ │
│   │                                                        │ │
│   │ [KEY METRICS GRID]                                     │ │
│   │                                                        │ │
│   │ [CHARTS]                                               │ │
│   │                                                        │ │
│   │ TRADE DETAILS (43 trades)                             │ │
│   │ ┌───────────────────────────────────────────────────┐ │ │
│   │ │ Date  │Ticker│Dir│Entry $│Exit $│Hold│Return│... │ │ │
│   │ │ 05/21 │ AAPL │ L │161.20│162.52│2h15│+0.82%│... │ │ │
│   │ │ 05/21 │ MSFT │ S │410.33│409.12│1h38│+0.29%│... │ │ │
│   │ │ ...                                                │ │ │
│   │ └───────────────────────────────────────────────────┘ │ │
│   │                                                        │ │
│   │                               [Load More (23 left)]   │ │
│   └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 3. Backend Data Required

**Current `markouts.json` schema has:**
- `recent_trades` array (limited to 10-15 trades, incomplete data)

**Need to add/expand:**
```json
{
  "model": "arima_20",
  "all_trades": [
    {
      "exit_timestamp": "2026-05-21T14:32:00",
      "ticker": "AAPL",
      "direction": "long",
      "signal_entry": 67.3,
      "signal_exit": 42.1,
      "entry_price": 161.20,
      "exit_price": 162.52,
      "entry_timestamp": "2026-05-21T12:17:00",
      "hold_minutes": 135,
      "capital_used": 12450.00,
      "return_pct": 0.0082,
      "return_dollars": 102.09,
      "exit_reason": "mean_reversion"
    },
    // ... all trades in lookback window
  ]
}
```

**Source:** `markout_eval.py` simulation already computes this — just need to emit it in JSON.

---

## Implementation Plan

### Phase A: Backend (1 hour)

**File:** `/home/nixos/Prod/V1/src/markout_eval_publish.py`

1. Modify `build_model_payload()` to include full trade list:
   ```python
   payload['all_trades'] = [
       {
           'exit_timestamp': trade.exit_time.isoformat(),
           'ticker': trade.ticker,
           'direction': 'long' if trade.direction > 0 else 'short',
           'signal_entry': trade.signal_at_entry,
           'signal_exit': trade.signal_at_exit,
           'entry_price': trade.entry_price,
           'exit_price': trade.exit_price,
           'entry_timestamp': trade.entry_time.isoformat(),
           'hold_minutes': (trade.exit_time - trade.entry_time).total_seconds() / 60,
           'capital_used': trade.position_size_dollars,
           'return_pct': trade.net_return_pct,
           'return_dollars': trade.net_return_dollars,
           'exit_reason': trade.exit_reason
       }
       for trade in sim_result.trades
   ]
   ```

2. Regenerate `markouts.json`: `python3 src/markout_eval_publish.py`

### Phase B: Frontend — Modal Pattern (30 min)

**File:** `/home/nixos/Prod/V1/src/markouts_v31.js`

1. Change `openDetailPane()` to create modal overlay instead of replacing page
2. Add close handlers (X button, click outside, Escape key)
3. Keep grid visible but dimmed in background

**CSS changes:**
```css
.detail-modal-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(15, 23, 42, 0.85);
    z-index: 200;
    overflow-y: auto;
    padding: 40px 20px;
}

.detail-modal-content {
    max-width: 1400px;
    margin: 0 auto;
    background: var(--bg-secondary);
    border-radius: 12px;
    padding: 24px;
    position: relative;
}

.close-modal-btn {
    position: absolute;
    top: 16px;
    right: 16px;
    font-size: 24px;
    cursor: pointer;
    /* X button styling */
}
```

### Phase C: Trade Details Table (1 hour)

**File:** `/home/nixos/Prod/V1/src/markouts_v31.js`

1. Add trade table rendering in detail modal:
   ```javascript
   function renderTradeTable(model) {
       const trades = model.all_trades || [];
       const html = `
           <div class="trade-details-section">
               <h3>TRADE DETAILS (${trades.length} trades)</h3>
               <div class="trade-filters">
                   <button class="filter-all active">All</button>
                   <button class="filter-longs">Longs</button>
                   <button class="filter-shorts">Shorts</button>
                   <button class="filter-winners">Winners</button>
                   <button class="filter-losers">Losers</button>
               </div>
               <div class="trade-table-container">
                   <table class="trade-table">
                       <thead>
                           <tr>
                               <th>Date/Time</th>
                               <th>Ticker</th>
                               <th>Dir</th>
                               <th>Signal Entry</th>
                               <th>Signal Exit</th>
                               <th>Entry $</th>
                               <th>Exit $</th>
                               <th>Hold</th>
                               <th>Capital $</th>
                               <th>Return %</th>
                               <th>Return $</th>
                               <th>Exit Reason</th>
                           </tr>
                       </thead>
                       <tbody id="trade-table-body">
                           ${trades.slice(0, 20).map(renderTradeRow).join('')}
                       </tbody>
                   </table>
               </div>
               ${trades.length > 20 ? `<button class="load-more-trades">Load More (${trades.length - 20} remaining)</button>` : ''}
           </div>
       `;
       return html;
   }
   
   function renderTradeRow(trade) {
       const returnClass = trade.return_pct > 0 ? 'positive' : 'negative';
       return `
           <tr class="trade-row ${returnClass}">
               <td>${formatTimestamp(trade.exit_timestamp)}</td>
               <td class="ticker-cell">${trade.ticker}</td>
               <td>${trade.direction === 'long' ? 'L' : 'S'}</td>
               <td>${trade.signal_entry.toFixed(1)}</td>
               <td>${trade.signal_exit.toFixed(1)}</td>
               <td>$${trade.entry_price.toFixed(2)}</td>
               <td>$${trade.exit_price.toFixed(2)}</td>
               <td>${formatHoldTime(trade.hold_minutes)}</td>
               <td>$${trade.capital_used.toLocaleString('en-US', {maximumFractionDigits: 0})}</td>
               <td class="${returnClass}">${formatPercent(trade.return_pct, 2)}</td>
               <td class="${returnClass}">$${trade.return_dollars.toFixed(2)}</td>
               <td class="exit-reason">${trade.exit_reason.replace(/_/g, ' ')}</td>
           </tr>
       `;
   }
   ```

2. Add CSV export button:
   ```javascript
   function exportTradesToCSV(model) {
       const trades = model.all_trades || [];
       const csv = [
           ['Date', 'Time', 'Ticker', 'Direction', 'Signal Entry', 'Signal Exit', 
            'Entry Price', 'Exit Price', 'Hold (min)', 'Capital Used', 
            'Return %', 'Return $', 'Exit Reason'].join(','),
           ...trades.map(t => [
               new Date(t.exit_timestamp).toLocaleDateString(),
               new Date(t.exit_timestamp).toLocaleTimeString(),
               t.ticker,
               t.direction,
               t.signal_entry.toFixed(1),
               t.signal_exit.toFixed(1),
               t.entry_price.toFixed(2),
               t.exit_price.toFixed(2),
               t.hold_minutes.toFixed(0),
               t.capital_used.toFixed(2),
               (t.return_pct * 100).toFixed(2),
               t.return_dollars.toFixed(2),
               t.exit_reason
           ].join(','))
       ].join('\n');
       
       // Trigger download
       const blob = new Blob([csv], { type: 'text/csv' });
       const url = URL.createObjectURL(blob);
       const a = document.createElement('a');
       a.href = url;
       a.download = `${model.model}_trades.csv`;
       a.click();
   }
   ```

### Phase D: Styling & Polish (30 min)

**File:** `/home/nixos/Prod/V1/src/markouts_v31.css`

1. Style trade table with fixed header, alternating rows
2. Add hover highlighting on rows
3. Color-code positive/negative returns
4. Make table responsive (horizontal scroll on mobile)

---

## Timeline

**Total: ~3 hours**

- Phase A (Backend): 1 hour
- Phase B (Modal): 30 min
- Phase C (Trade table): 1 hour
- Phase D (Styling): 30 min

---

## Success Criteria

1. ✅ Click model card → opens modal overlay (grid visible behind)
2. ✅ Easy to close modal (X button, click outside, Escape)
3. ✅ Trade table shows all trades with 12 columns
4. ✅ Can filter trades by L/S, winners/losers
5. ✅ Can export trades to CSV
6. ✅ Can load more trades (pagination)
7. ✅ Mobile: table scrolls horizontally
8. ✅ Opening modal preserves grid scroll position

---

**Ready to proceed?** Say "go ahead" to start implementation.
