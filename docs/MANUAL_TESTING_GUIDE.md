# 🧪 Manual Testing Guide

**Last Updated:** 2025-10-08  
**Status:** Ready for Testing

---

## 📋 Prerequisites

### 1. **Database Setup**
```bash
# Start PostgreSQL (if not running)
cd funding_rate_service
docker-compose up -d

# Run the strategy tables migration
python scripts/run_migration.py 004

# Verify tables created
psql -h localhost -U postgres -d funding_rates -c "\dt"
# Should show: strategy_positions, funding_payments, fund_transfers, strategy_state
```

### 2. **Database Connection** (Required for Funding Arbitrage)
```bash
# Start PostgreSQL (funding arbitrage uses direct DB access)
cd funding_rate_service
docker-compose up -d

# Verify database is accessible
psql -h localhost -U postgres -d funding_rates -c "SELECT 1;"
# Should return: 1
```

**Note:** The funding arbitrage strategy uses **direct internal calls** to `funding_rate_service` components, not HTTP API calls. The FastAPI service doesn't need to be running.

### 3. **Environment Setup**
```bash
# Ensure .env file exists with exchange credentials
cp env_example.txt .env
# Edit .env with your API keys
```

---

## 🎯 Testing Scenarios

### **Scenario 1: Grid Strategy (Migrated Architecture)**

**Purpose:** Verify the migrated grid strategy works with new architecture

```bash
# Test 1: Basic grid strategy
python runbot.py \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.01 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 3

# Expected behavior:
# - Strategy loads successfully
# - Places initial buy orders
# - Monitors for fills and profit-taking
# - Logs show grid state updates
```

**What to Monitor:**
- [ ] Strategy initializes without errors
- [ ] Orders are placed on the exchange
- [ ] Grid state is tracked correctly
- [ ] Profit-taking logic works
- [ ] Logs are clear and informative

---

### **🔥 Scenario 2: Funding Arbitrage Strategy (NEW)**

**Purpose:** Test the new funding arbitrage strategy end-to-end

#### **Test 2A: Opportunity Detection**
```bash
# Test opportunity detection (dry run mode)
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --target-exposure 100 \
  --min-profit-rate 0.0005 \
  --exchanges lighter,backpack,edgex \
  --strategy-params dry_run=true
```

**Expected Behavior:**
- [ ] Connects to funding rate service
- [ ] Fetches current funding rates
- [ ] Calculates profitability for all DEX pairs
- [ ] Identifies profitable opportunities (if any)
- [ ] Logs opportunity analysis without placing trades

#### **Test 2B: Small Position Test** ⚠️ **REAL MONEY**
```bash
# CAUTION: This will place real trades with small amounts
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --target-exposure 50 \
  --min-profit-rate 0.001 \
  --exchanges lighter,backpack \
  --strategy-params max_positions=1
```

**Expected Behavior:**
- [ ] Finds profitable opportunity
- [ ] Performs pre-flight liquidity checks
- [ ] Opens long position on one DEX
- [ ] Opens short position on another DEX (atomic execution)
- [ ] Tracks positions in database
- [ ] Monitors funding payments
- [ ] Triggers rebalance when conditions met

**What to Monitor:**
- [ ] **Atomic Execution:** Both sides fill or neither (no one-sided exposure)
- [ ] **Database Persistence:** Check `strategy_positions` table
- [ ] **Funding Tracking:** Check `funding_payments` table
- [ ] **Risk Management:** Rebalance triggers work correctly
- [ ] **Execution Quality:** Slippage and timing metrics

---

## 📊 Monitoring & Verification

### **Database Queries**
```sql
-- Check active positions
SELECT * FROM strategy_positions WHERE status = 'ACTIVE';

-- Check funding payments
SELECT * FROM funding_payments ORDER BY timestamp DESC LIMIT 10;

-- Check strategy state
SELECT * FROM strategy_state ORDER BY updated_at DESC;
```

### **Direct Database Access**
The funding arbitrage strategy accesses data directly from the database, not via API:
```sql
-- Check recent funding rates
SELECT * FROM funding_rates ORDER BY timestamp DESC LIMIT 10;

-- Check DEX availability
SELECT * FROM dexes WHERE is_active = true;
```

### **Log Analysis**
Look for these key log messages:

**Grid Strategy:**
```
[INFO] GridStrategy: Initialized with config: ...
[INFO] GridStrategy: Placed order: ...
[INFO] GridStrategy: Order filled: ...
[INFO] GridStrategy: Profit taken: ...
```

**Funding Arbitrage:**
```
[INFO] FundingArbitrageStrategy: Opportunity found: ...
[INFO] AtomicMultiOrderExecutor: Executing atomic trade: ...
[INFO] FundingArbPositionManager: Position opened: ...
[INFO] FundingRateAnalyzer: Current divergence: ...
[INFO] CombinedRebalanceStrategy: Rebalance triggered: ...
```

---

## 🚨 Safety Checks

### **Before Testing:**
- [ ] **Small Amounts:** Use minimal position sizes ($10-50)
- [ ] **Testnet First:** If available, test on testnet
- [ ] **Backup Funds:** Only use funds you can afford to lose
- [ ] **Monitor Closely:** Watch the first few trades carefully
- [ ] **Stop Loss:** Know how to manually close positions if needed

### **During Testing:**
- [ ] **One-Sided Exposure:** Verify both sides of arbitrage positions
- [ ] **Funding Payments:** Check that funding is being collected
- [ ] **Slippage:** Monitor execution quality
- [ ] **Database:** Verify data is being persisted correctly

### **Emergency Procedures:**
```bash
# Stop the bot (Ctrl+C)
# Manually close positions if needed via exchange interfaces
# Check database for position status
# Review logs for any errors
```

---

## 🎯 Success Criteria

### **Grid Strategy:**
- [ ] ✅ Strategy loads and runs without errors
- [ ] ✅ Orders are placed and managed correctly
- [ ] ✅ Profit-taking logic works as expected
- [ ] ✅ No memory leaks or performance issues

### **Funding Arbitrage:**
- [ ] ✅ Opportunity detection works correctly
- [ ] ✅ Atomic execution prevents one-sided fills
- [ ] ✅ Database persistence works reliably
- [ ] ✅ Risk management triggers appropriately
- [ ] ✅ Funding payments are tracked accurately
- [ ] ✅ Position closure works correctly

---

## 🐛 Common Issues & Troubleshooting

### **Import Errors:**
```bash
# If you get module import errors
export PYTHONPATH=/Users/yipsimba/perp-dex-tools:$PYTHONPATH
# Or run from project root
```

### **Database Connection:**
```bash
# If database connection fails
docker-compose down && docker-compose up -d
# Wait 30 seconds for PostgreSQL to start
```

### **Database Connection Issues:**
```bash
# If funding arbitrage can't connect to database
cd funding_rate_service
docker-compose down && docker-compose up -d
# Wait 30 seconds for PostgreSQL to start
psql -h localhost -U postgres -d funding_rates -c "SELECT 1;"
```

### **Exchange API Errors:**
- Check API keys in `.env` file
- Verify exchange is operational
- Check rate limits and permissions

---

## 📝 Test Results Template

```
## Test Results - [Date]

### Grid Strategy Test:
- [ ] ✅ / ❌ Strategy initialization
- [ ] ✅ / ❌ Order placement
- [ ] ✅ / ❌ Profit-taking
- [ ] ✅ / ❌ Error handling
- **Notes:** 

### Funding Arbitrage Test:
- [ ] ✅ / ❌ Opportunity detection
- [ ] ✅ / ❌ Atomic execution
- [ ] ✅ / ❌ Database persistence
- [ ] ✅ / ❌ Risk management
- [ ] ✅ / ❌ Funding tracking
- **Notes:** 

### Overall Assessment:
- **Ready for Production:** ✅ / ❌
- **Issues Found:** 
- **Next Steps:** 
```

---

**🎉 You're ready to test! Start with the Grid Strategy to verify the basic architecture, then move to Funding Arbitrage for the advanced features.**
