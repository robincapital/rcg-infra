# IB Model Account API Integration — Technical Spec

**Trading & Risk Hat Assessment**  
**Date:** 2026-05-21  
**Status:** OPERATIONAL CONTEXT CLARIFICATION  
**Prepared for:** Nick Diaz (Managing Member)  

---

## Executive Summary

**CRITICAL CONTEXT REVISION:** RCG is **already live** with client trading on Interactive Brokers. This is NOT a "future execution phase" — it's optimizing an existing operational workflow.

**Current Setup:**
- ✅ 3 long-only strategies actively managed: Rates, Global Macro, Equities
- ✅ Multiple client accounts grouped via **IB Model Accounts** functionality
- ✅ IB handles account-level allocation (pro-rata by NAV to target position)
- ✅ Nick manually manages: target weights, ticker adds/removes, rebalancing
- ✅ Core book optimized via Markowitz efficient frontier

**Goal:** Automate order submission via IB API for:
1. **Model-level orders** (entire group of accounts, single instruction)
2. **Account-level orders** (individual client account, override model)

This document analyzes **IB API capabilities** for Model Accounts and designs the integration architecture.

---

## 1. IB Model Accounts — How They Work

### What is an IB Model Account?

**IB's "Model Portfolio" feature** allows advisors to:
- **Group multiple client accounts** under a single "model" (e.g., "RCG Equities Strategy")
- **Set target allocations** (e.g., AAPL = 5%, MSFT = 3%, ...) at the MODEL level
- **Execute trades once** — IB automatically:
  - Calculates each account's share allocation (pro-rata by NAV)
  - Submits separate child orders to the exchange for each account
  - Allocates fills back to accounts (FIFO, avg price, or pro-rata by shares)

**Key Benefit:** Advisor changes target allocation → IB calculates rebalance orders for all accounts in the model automatically.

### IB Model Account Identifier

Each model has:
- **Model Code** (string, e.g., `"RCG_EQUITIES"`) — human-readable name
- **Model Account ID** (integer, IB-assigned) — used in API calls
- **Parent Account** — advisor's master account (e.g., `F123456`) that "owns" the model

**Client accounts** in the model:
- Each has a unique **Account ID** (e.g., `U234567`, `U234568`, ...)
- Linked to the model via IB's "Account Management" console (web UI or API)

---

## 2. IB API Capabilities — Model Accounts

### API Version Requirements

**Interactive Brokers TWS API** (latest stable: v10.19, May 2024):
- ✅ **Model Portfolio API** support added in v9.76 (2019)
- ✅ Supports: create model, update allocations, query positions, execute to model
- ⚠️ **Financial Advisor (FA) mode required** — Model Accounts are part of IB's FA offering

**Python Wrappers:**
- `ib_insync` (most popular, high-level) — **full Model Account support** since v0.9.60
- `ibapi` (official IB Python client) — **full support**, lower-level

### What the API Allows

#### ✅ Model-Level Operations

**1. Query Model Details**
```python
# ib_insync example
models = ib.reqFamilyAllocations()
# Returns list of models with codes, account lists, allocation profiles
```

**2. Query Model Positions**
```python
# Get aggregated positions across all accounts in model
positions = ib.reqPositions()  # Filter by model account ID
# Returns: ticker, total_shares, avg_cost, market_value
```

**3. Query Model NAV**
```python
# Get total NAV across all accounts in the model
account_values = ib.reqAccountSummary(group='RCG_EQUITIES', tags='NetLiquidation')
# Returns: sum of all client NAVs in the model
```

**4. Update Target Allocations**
```python
# Set target weights at model level (IB calculates per-account shares)
ib.reqUpdateFamilyAllocations([
    {'symbol': 'AAPL', 'alloc_pct': 5.0},
    {'symbol': 'MSFT', 'alloc_pct': 3.0},
    # ... rest of model
])
# IB automatically generates child orders for each account
```

**5. Execute to Model (Single Order for All Accounts)**
```python
# Place order at model level → IB splits across accounts
contract = Stock('AAPL', 'SMART', 'USD')
order = MarketOrder('BUY', totalQuantity=1000)  # Total shares across all accounts
order.faGroup = 'RCG_EQUITIES'  # Model code
order.faMethod = 'NetLiq'  # Allocation method (NetLiq, AvailableEquity, EqualQuantity)
ib.placeOrder(contract, order)
# IB creates child orders: Account A gets X shares, Account B gets Y shares, ...
```

#### ✅ Account-Level Operations (Override Model)

**1. Query Individual Account Positions**
```python
# Get positions for a specific client account
positions = ib.reqPositions(account='U234567')
# Returns: per-account holdings
```

**2. Query Individual Account NAV**
```python
account_summary = ib.reqAccountSummary(account='U234567', tags='NetLiquidation,TotalCashValue')
# Returns: NAV, cash, buying power for that account
```

**3. Place Order for Individual Account (Bypass Model)**
```python
# Execute to single account (ignores model allocation)
contract = Stock('AAPL', 'SMART', 'USD')
order = MarketOrder('BUY', totalQuantity=100)
order.account = 'U234567'  # Specific client account
ib.placeOrder(contract, order)
# Only this account gets the order
```

#### ⚠️ What the API Does NOT Allow

**1. Cannot Modify Account-to-Model Linkage via API**
- Adding/removing accounts from a model must be done via **IB Account Management web console**
- API is read-only for model membership

**2. Cannot Override IB's Allocation Logic Mid-Trade**
- When you submit a model-level order, IB decides per-account allocation (via `faMethod`)
- You cannot say "Account A gets 60 shares, Account B gets 40" at order submission
- **Workaround:** Place separate account-level orders if you need custom split

**3. Cannot Query "What-If" Allocation Before Submitting Order**
- IB doesn't expose a `calculate_allocation(model, total_shares)` endpoint
- You must calculate locally: `account_shares = (account_NAV / model_NAV) × total_shares`

**4. Cannot Fractional Shares via Model Orders**
- IB Model Accounts only support **whole shares** (even if individual account trading allows fractional)
- Rounding errors accumulate — must handle residuals

---

## 3. Integration Architecture — Two Pathways

### Path A: Model-Level Orders (Primary Workflow)

**Use Case:** Rebalancing all accounts in "RCG Equities" strategy to new target allocations.

**Flow:**
```
1. Nick's signal system computes new target weights (e.g., AAPL 5% → 6%)
2. Python script:
   a. Query model NAV (sum of all account NAVs)
   b. Convert % weights → dollar amounts → shares (round to lots)
   c. Query current model positions
   d. Calculate diff: target_shares - current_shares = delta
   e. Submit model-level order for delta shares
3. IB receives order → calculates per-account allocation (pro-rata by NAV)
4. IB submits child orders to exchange
5. Fills come back → IB allocates to accounts (avg price)
6. Script polls for fill confirmation → updates internal book
```

**API Calls:**
```python
# Step 2a: Get model NAV
model_nav = sum(
    ib.reqAccountSummary(account=acc, tags='NetLiquidation')
    for acc in model_accounts
)

# Step 2b: Convert weights → shares
target_shares = {
    'AAPL': int((0.06 * model_nav) / aapl_price),  # 6% weight
    'MSFT': int((0.03 * model_nav) / msft_price),  # 3% weight
    # ...
}

# Step 2c: Get current positions
current_positions = {pos.contract.symbol: pos.position 
                     for pos in ib.reqPositions() if pos.account == 'RCG_EQUITIES'}

# Step 2d: Calculate rebalance orders
orders_to_place = {
    ticker: target_shares[ticker] - current_positions.get(ticker, 0)
    for ticker in target_shares
}

# Step 2e: Submit orders
for ticker, delta_shares in orders_to_place.items():
    if abs(delta_shares) < 10:  # Skip tiny orders (< 10 shares)
        continue
    contract = Stock(ticker, 'SMART', 'USD')
    action = 'BUY' if delta_shares > 0 else 'SELL'
    order = MarketOrder(action, abs(delta_shares))
    order.faGroup = 'RCG_EQUITIES'
    order.faMethod = 'NetLiq'  # Pro-rata by account NAV
    ib.placeOrder(contract, order)
```

**Pros:**
- ✅ Single API call rebalances all accounts
- ✅ IB handles allocation math (no manual pro-rata calculation)
- ✅ Fewer orders → lower latency, simpler error handling

**Cons:**
- ⚠️ Cannot customize per-account allocation (e.g., "Account A overweight AAPL by 2%")
- ⚠️ Rounding errors: total shares must be integer, residuals discarded
- ⚠️ If one account has insufficient buying power, **entire model order fails** (IB rejects at validation step)

---

### Path B: Account-Level Orders (Override Workflow)

**Use Case:** Individual client requests custom allocation (e.g., "no TSLA in my account") or wants to frontrun model rebalance.

**Flow:**
```
1. Nick specifies: "Rebalance Account U234567 only, ignore model"
2. Python script:
   a. Query individual account NAV
   b. Query individual account positions
   c. Calculate delta shares for this account only
   d. Submit account-level order (bypass model)
3. IB executes order for this account only
4. Other accounts in model remain unchanged
```

**API Calls:**
```python
# Same as Path A, but specify account ID in order
order.account = 'U234567'  # Individual account, not model
order.faGroup = ''  # Leave blank (not a model order)
ib.placeOrder(contract, order)
```

**Pros:**
- ✅ Full control over individual account allocation
- ✅ Can handle account-specific restrictions (no TSLA, no BTC proxies, etc.)
- ✅ Doesn't affect other accounts in the model

**Cons:**
- ⚠️ More API calls (one per account if rebalancing entire book)
- ⚠️ Loses IB's block-trade fill allocation (each account gets separate fill price)
- ⚠️ More complex error handling (what if 1 of 10 accounts fails?)

---

## 4. Recommended Integration Design

### Hybrid Approach: Model-First with Account-Level Fallback

**Primary:** Use **Path A (model-level orders)** for 90% of rebalancing.

**Fallback to Path B (account-level)** when:
1. Individual account has restrictions (ticker blocklist, sector cap override)
2. Account NAV too small for model-level rounding (e.g., $5K account in $2M model → gets 0 shares of expensive tickers)
3. Testing new signals on single account before rolling to full model

### API Wrapper Design

**Module:** `src/execution/ib_model_trader.py`

**Classes:**
```python
class IBModelTrader:
    """
    IB Model Account trading interface.
    Handles both model-level and account-level order submission.
    """
    
    def __init__(self, host='127.0.0.1', port=7497, client_id=1):
        """Connect to TWS or IB Gateway."""
        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)
    
    def get_model_nav(self, model_code: str) -> float:
        """Return total NAV across all accounts in the model."""
        accounts = self._get_model_accounts(model_code)
        return sum(self._get_account_nav(acc) for acc in accounts)
    
    def get_model_positions(self, model_code: str) -> dict[str, int]:
        """Return aggregated positions (ticker → total shares) for the model."""
        # Query all accounts, sum positions by ticker
        pass
    
    def rebalance_to_target(self, model_code: str, target_weights: dict[str, float],
                           prices: dict[str, float], order_type='LIMIT') -> list[Trade]:
        """
        Rebalance model to target weights (% of NAV).
        
        Args:
            model_code: IB model code (e.g., 'RCG_EQUITIES')
            target_weights: {ticker: weight_pct}  (e.g., {'AAPL': 0.05})
            prices: {ticker: limit_price}  (None for market orders)
            order_type: 'MARKET' or 'LIMIT'
        
        Returns:
            List of Trade objects (ib_insync) for tracking fills
        """
        nav = self.get_model_nav(model_code)
        current_positions = self.get_model_positions(model_code)
        
        # Calculate target shares
        target_shares = {
            ticker: int((weight * nav) / prices[ticker])
            for ticker, weight in target_weights.items()
        }
        
        # Calculate rebalance orders
        orders = []
        for ticker, target_qty in target_shares.items():
            current_qty = current_positions.get(ticker, 0)
            delta = target_qty - current_qty
            
            if abs(delta) < 10:  # Skip tiny orders
                continue
            
            contract = Stock(ticker, 'SMART', 'USD')
            action = 'BUY' if delta > 0 else 'SELL'
            
            if order_type == 'LIMIT':
                order = LimitOrder(action, abs(delta), prices[ticker])
            else:
                order = MarketOrder(action, abs(delta))
            
            order.faGroup = model_code
            order.faMethod = 'NetLiq'  # Pro-rata by NAV
            
            trade = self.ib.placeOrder(contract, order)
            orders.append(trade)
        
        return orders
    
    def rebalance_account(self, account_id: str, target_weights: dict[str, float],
                         prices: dict[str, float]) -> list[Trade]:
        """
        Rebalance individual account (bypass model).
        Same logic as rebalance_to_target, but account-level.
        """
        # Similar to above, but:
        # - NAV = individual account NAV
        # - order.account = account_id (not order.faGroup)
        pass
    
    def add_ticker_to_model(self, model_code: str, ticker: str, weight: float,
                           price: float) -> Trade:
        """Add a new ticker to the model at specified weight."""
        # Calculate shares, submit buy order
        pass
    
    def remove_ticker_from_model(self, model_code: str, ticker: str) -> Trade:
        """Exit full position in ticker across all accounts."""
        # Query current position, submit sell order for total shares
        pass
    
    def get_pending_orders(self, model_code: str) -> list[Trade]:
        """Return all open orders for the model."""
        return [t for t in self.ib.openTrades() if t.order.faGroup == model_code]
    
    def cancel_all_orders(self, model_code: str):
        """Cancel all pending orders for the model."""
        for trade in self.get_pending_orders(model_code):
            self.ib.cancelOrder(trade.order)
```

**Pre-Trade Checks Integration:**
```python
# Before calling rebalance_to_target, run checks
from execution.pretrade_checks import PreTradeChecker

checker = PreTradeChecker(
    max_position_pct=0.15,  # 15% per name
    max_sector_pct=0.80,    # 80% per sector
    max_gross_exposure=1.00  # No leverage
)

result = checker.validate_allocation(target_weights, current_positions, nav)
if not result.allowed:
    raise ValueError(f"Pre-trade check failed: {result.reason}")

# Proceed with rebalance
trader.rebalance_to_target(...)
```

---

## 5. Data Requirements — What You Need to Track

### To Execute Model-Level Orders

**Minimum Required:**
1. ✅ **Model NAV** (from IB API: `reqAccountSummary` aggregated across accounts)
2. ✅ **Current model positions** (from IB API: `reqPositions` aggregated by ticker)
3. ✅ **Target weights** (from your signal system — already computed)
4. ✅ **Current prices** (from Bloomberg or IB real-time market data)

**You DO NOT need:**
- ❌ Individual account NAVs (IB calculates internally)
- ❌ Individual account positions (IB allocates automatically)
- ❌ Per-account custom allocations (unless using Path B)

### To Execute Account-Level Orders (Fallback)

**Required:**
1. ✅ **Individual account NAV** (from IB API: `reqAccountSummary(account=...)`)
2. ✅ **Individual account positions** (from IB API: `reqPositions(account=...)`)
3. ✅ **Account-specific restrictions** (if any — e.g., ticker blocklist, sector overrides)

---

## 6. Execution Workflows — Examples

### Workflow 1: Daily Rebalance (Full Model)

**Trigger:** End-of-day screener runs, updates target weights.

**Steps:**
```python
# 1. Load new target weights from screener output
target_weights = json.load(open('outputs/screener/target_weights.json'))
# {'AAPL': 0.06, 'MSFT': 0.03, ...}

# 2. Get current prices (from Bloomberg or IB)
prices = {ticker: get_price(ticker) for ticker in target_weights}

# 3. Run pre-trade checks
checker.validate_allocation(target_weights, current_positions, nav)

# 4. Submit rebalance orders
trader.rebalance_to_target('RCG_EQUITIES', target_weights, prices, order_type='LIMIT')

# 5. Monitor fills
while trader.get_pending_orders('RCG_EQUITIES'):
    time.sleep(5)  # Poll every 5 sec
    # Check for partial fills, adjust limits if needed

# 6. Log executed trades for compliance
log_trades_to_db(trader.ib.fills())
```

### Workflow 2: Add New Ticker to Model

**Trigger:** Nick says "Add PLTR to equities model at 2% weight."

**Steps:**
```python
# 1. Nick inputs: ticker, weight, model
ticker = 'PLTR'
weight = 0.02  # 2%
model = 'RCG_EQUITIES'

# 2. Get current price
price = get_price(ticker)

# 3. Check if adding PLTR would breach sector cap
current_positions = trader.get_model_positions(model)
new_positions = {**current_positions, ticker: weight}
checker.validate_allocation(new_positions, current_positions, nav)

# 4. Submit buy order
trader.add_ticker_to_model(model, ticker, weight, price)
```

### Workflow 3: Individual Account Override

**Trigger:** Client says "Remove TSLA from my account only, keep it in the model."

**Steps:**
```python
# 1. Identify account
account_id = 'U234567'

# 2. Get account's current TSLA position
positions = trader.ib.reqPositions(account=account_id)
tsla_qty = next((p.position for p in positions if p.contract.symbol == 'TSLA'), 0)

# 3. Submit sell order for this account only
if tsla_qty > 0:
    contract = Stock('TSLA', 'SMART', 'USD')
    order = MarketOrder('SELL', tsla_qty)
    order.account = account_id  # Account-level, bypass model
    trader.ib.placeOrder(contract, order)

# 4. Add TSLA to account's restriction list (internal tracking)
account_restrictions[account_id].append('TSLA')
```

---

## 7. Slippage & Fill Allocation — IB's Logic

### Model-Level Orders

When you submit a model-level order:
1. **IB calculates per-account allocation** at order submission time (based on `faMethod`):
   - `NetLiq`: Pro-rata by account NAV (most common)
   - `AvailableEquity`: Pro-rata by available cash + margin
   - `EqualQuantity`: Equal shares per account (ignores NAV)
2. **IB submits child orders** to the exchange (one per account)
3. **Fills come back** → IB allocates at **average fill price** across all child orders
   - If Account A fills at $100.00 and Account B at $100.05, both get allocated at $100.025
   - This is better than separate account-level orders (each gets their own fill price)

**Slippage Benefit:** Block-trade fill allocation = lower variance in execution quality across accounts.

### Account-Level Orders

Each account gets its **own fill price** → higher slippage variance.

**Example:**
- Model order: 1000 shares AAPL across 10 accounts → avg fill $150.02
- Account-level orders: Account A fills $150.00, Account B fills $150.05 (because orders hit market sequentially)

**When to Use:** Only when you NEED custom allocation (e.g., account restrictions).

---

## 8. Compliance Considerations

### Books & Records (§18)

**What to Log:**
- Every model-level order placed (timestamp, ticker, action, quantity, model_code)
- Every account-level override order (timestamp, ticker, action, quantity, account_id, reason)
- All fills (timestamp, ticker, fill_price, fill_qty, account allocations)
- Pre-trade check results (allowed/denied, reason)

**Storage:**
- Postgres `signals` table (extend with new `run_type='order_placed'`)
- OneDrive Trade Log (CSV export for 5-year retention per §18)

### Best Execution (§11.A)

**Annual Review Required:**
- Compare IB fill quality vs. VWAP (volume-weighted average price) for each ticker
- Document why IB was chosen (lowest commissions, block-trade allocation, custodian = broker)
- No action needed now (you're already using IB) — just document the annual review process

### Trade Errors (§11.D)

**If a model order fails:**
- Log error in OneDrive `Compliance / Trade Error Log / 2026 / YYYY-MM-DD_error.txt`
- Note: ticker, intended action, error message, resolution (cancel/retry/manual fix)
- Must be logged within 2 business days

---

## 9. Implementation Timeline

**Week 1 (May 22–28):**
- [ ] **Day 1:** Set up IB Gateway / TWS connection (read-only first)
- [ ] **Day 2:** Test `reqAccountSummary`, `reqPositions` for all 3 models (Rates, Macro, Equities)
- [ ] **Day 3:** Build `IBModelTrader` class (query methods only — no order placement yet)

**Week 2 (May 29 – June 4):**
- [ ] **Day 1:** Add `rebalance_to_target` method (dry-run mode — calculates orders but doesn't submit)
- [ ] **Day 2:** Integrate pre-trade checks (max position, sector cap, leverage)
- [ ] **Day 3:** Test on paper account (IB paper env, not live)

**Week 3 (June 5–11) — Pending CCO Approval:**
- [ ] **Day 1:** CCO reviews logs from paper account testing
- [ ] **Day 2:** Document execution procedures (best execution policy, trade error log SOP)
- [ ] **Day 3:** Deploy to live (start with 1 model, Equities strategy only)

**Week 4+ (June 12+):**
- [ ] Roll out to Rates and Global Macro models
- [ ] Add account-level override functionality
- [ ] Monitor slippage vs. manual execution for 2 weeks

---

## 10. Open Questions for Nick

### Model Setup
1. **How many client accounts are in each model?**
   - RCG Equities: ___
   - RCG Rates: ___
   - RCG Global Macro: ___
2. **What are the IB model codes?** (e.g., `"RCG_EQUITIES"`) — need exact strings for API calls
3. **What is your IB Financial Advisor parent account ID?** (e.g., `F123456`)

### Workflow Preferences
4. **Do you currently set target % weights in IB manually, or do you calculate dollar amounts → shares?**
   - If % weights: we can match your current workflow exactly
   - If shares: we need to reverse-engineer % from NAV
5. **How often do you rebalance?**
   - Daily? Weekly? Event-driven (on signal threshold breach)?
6. **Do you use limit orders or market orders?**
   - Limit: need pricing source (Bloomberg? IB real-time?)
   - Market: simpler but higher slippage
7. **Do any accounts have custom restrictions?** (e.g., "Account X: no TSLA, no BTC proxies")
   - If yes: need account-level restriction tracking

### Execution Preferences
8. **Do you want orders to execute immediately, or queue for batch execution at specific times?**
   - Immediate: API submits as soon as target weights change
   - Batch: API queues orders, you approve/execute manually or at scheduled time (e.g., 3:50pm daily)
9. **Do you want to review orders before submission (approval gate), or full automation?**
   - Approval gate: safer, but slower
   - Full auto: faster, but need robust pre-trade checks
10. **What's your risk tolerance for partial fills?**
    - If model order for 1000 shares only fills 800, do you:
      - A) Leave it (wait for more liquidity next time)
      - B) Chase the fill (adjust limit, resubmit for remaining 200)
      - C) Cancel and retry next day

### Data Sources
11. **Where do you currently get prices for order submission?**
    - IB real-time quotes?
    - Bloomberg (already wired)?
    - Sharadar EOD (too stale for intraday)?
12. **Do you need real-time P&L tracking, or is end-of-day reconciliation sufficient?**
    - Real-time: subscribe to IB market data feed
    - EOD: use Bloomberg/Sharadar (current setup)

---

## 11. Compliance Escalation — Revised Status

**Original Block Reason:** "No execution phase approval, Stage 4 not validated."

**Revised Understanding:** Nick is **already live** with client trading on IB. This is NOT a new execution phase — it's **operational tooling** for existing trading workflow.

**Compliance Status:** ⚠️ **SOFT FLAG — CCO NOTIFICATION REQUIRED**

**Why Soft Flag (Not Hard Refuse):**
1. ✅ You're already trading live (execution phase is active, not future)
2. ✅ IB is on approved vendor list
3. ✅ Best execution procedures presumably already in place (you're using IB manually now)
4. ⚠️ **But:** Automating order submission via API = new operational risk → CCO should review

**Action Required:**
- [ ] **CCO (Ashley):** Review this spec, confirm:
  - Best execution policy covers automated API orders (vs. manual TWS orders)
  - Trade error log process can handle API failures (network errors, order rejections)
  - Client disclosure: do clients know orders may be submitted via automated system?
- [ ] **Nick:** Confirm you have documented procedures for current manual trading workflow
- [ ] **Trading Hat (me):** Build `IBModelTrader` class with robust error handling + logging

**Timeline:** Can proceed with read-only API testing now. Order submission waits for CCO sign-off (1 week).

---

## 12. Next Steps

**Immediate (This Week):**
1. ✅ **Nick answers open questions** (Section 10 above)
2. ✅ **CCO reviews this spec** (confirm automation is acceptable)
3. ✅ **I build `IBModelTrader` class** (read-only methods first)

**Next Week (Pending CCO):**
4. ✅ Add order submission methods (dry-run mode first)
5. ✅ Test on IB paper account
6. ✅ Document execution procedures for CCO review

**Week 3+ (Deployment):**
7. ✅ Deploy to live (Equities model first)
8. ✅ Monitor vs. manual execution for 2 weeks
9. ✅ Roll out to Rates and Macro models

---

**Document Prepared By:** Trading & Risk Hat  
**For Questions:** Nick Diaz (Managing Member) or Ashley Schott (CCO)  
**Next Review:** After Nick answers open questions (target: May 22)
