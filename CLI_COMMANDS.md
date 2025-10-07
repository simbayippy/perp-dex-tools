# Trading Bot CLI Commands

Quick reference for running the trading bot with various configurations.

---

## 🚀 Quick Start

### **Your Standard Grid Strategy (LONG)**
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 50 \
  --wait-time 20 \
  --grid-step 0.06
```

---

## 📋 Core Parameters

| Parameter | Required | Description | Example |
|-----------|----------|-------------|---------|
| `--exchange` | Yes | Exchange to trade on | `lighter`, `grvt`, `edgex`, `backpack` |
| `--ticker` | Yes | Trading pair symbol | `BTC`, `ETH`, `SOL` |
| `--quantity` | Yes | Order size per grid level | `0.00273` |
| `--strategy` | No | Trading strategy (default: `grid`) | `grid`, `funding_arbitrage` |
| `--env-file` | No | Path to .env file (default: `.env`) | `.env`, `.env.prod` |

---

## 🎯 Grid Strategy Parameters

### **Required**
- `--take-profit` - Profit target per order (e.g., `0.008` = 0.8%)
- `--direction` - Trading direction: `buy` (LONG) or `sell` (SHORT)
- `--max-orders` - Maximum active orders (e.g., `50`)
- `--wait-time` - Seconds between orders (e.g., `20`)
- `--grid-step` - Price distance between orders (e.g., `0.06` = 6%)

### **Optional Safety Controls**
- `--stop-price` - Emergency stop price
- `--pause-price` - Temporary pause price

### **Optional Enhancements**
- `--random-timing` - Add randomness to order timing
- `--dynamic-profit` - Vary profit targets dynamically

---

## 📖 Understanding `--direction`

The direction parameter determines your market position:

### **`--direction buy` (LONG Position)**
- **Strategy:** Profit from price increases
- **How it works:**
  - Places **BUY orders** when price dips
  - Closes with **SELL orders** at profit target
- **When to use:** Bullish on the asset
- **Stop price:** Triggers if price falls **below** stop-price

**Example:**
```bash
--direction buy --stop-price 102000  # Stop if BTC < 102,000
```

### **`--direction sell` (SHORT Position)**
- **Strategy:** Profit from price decreases
- **How it works:**
  - Places **SELL orders** when price rises
  - Closes with **BUY orders** at profit target
- **When to use:** Bearish on the asset
- **Stop price:** Triggers if price rises **above** stop-price

**Example:**
```bash
--direction sell --stop-price 108000  # Stop if BTC > 108,000
```

---

## ✅ Example Commands

### 1. **Conservative LONG (Buy) Strategy**
Small position, tight profit targets:
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.001 \
  --take-profit 0.005 \
  --direction buy \
  --max-orders 25 \
  --wait-time 30 \
  --grid-step 0.05 \
  --stop-price 100000
```

### 2. **Aggressive LONG (Buy) Strategy**
Larger position, wider grid:
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.005 \
  --take-profit 0.01 \
  --direction buy \
  --max-orders 100 \
  --wait-time 15 \
  --grid-step 0.08 \
  --stop-price 102000
```

### 3. **SHORT (Sell) Strategy**
Profit from price decline:
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction sell \
  --max-orders 50 \
  --wait-time 20 \
  --grid-step 0.06 \
  --stop-price 110000
```

### 4. **With Safety Controls**
Stop and pause prices:
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 50 \
  --wait-time 20 \
  --grid-step 0.06 \
  --stop-price 100000 \
  --pause-price 108000
```

### 5. **Enhanced with Random Timing**
Less predictable patterns:
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 50 \
  --wait-time 20 \
  --grid-step 0.06 \
  --random-timing \
  --dynamic-profit
```

### 6. **Different Exchange (GRVT)**
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange grvt \
  --ticker ETH \
  --quantity 0.05 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 30 \
  --wait-time 20 \
  --grid-step 0.05
```

### 7. **Different Asset (SOL)**
```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker SOL \
  --quantity 0.5 \
  --take-profit 0.01 \
  --direction buy \
  --max-orders 40 \
  --wait-time 15 \
  --grid-step 0.06
```

---

## 🎛️ Advanced: Custom Parameters

For custom parameters not covered by convenience flags:

```bash
python runbot.py \
  --env-file .env \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 50 \
  --wait-time 20 \
  --grid-step 0.06 \
  --strategy-params boost_mode=true profit_range=0.3
```

---

## 🛡️ Safety Price Logic

### **Stop Price Behavior**

| Direction | Trigger Condition | Action |
|-----------|------------------|--------|
| `buy` (LONG) | Price < stop_price | Cancel all orders + STOP |
| `sell` (SHORT) | Price > stop_price | Cancel all orders + STOP |

**Example:**
- LONG strategy with `--stop-price 102000`
- If BTC drops to 101,999 → **Bot stops immediately**

### **Pause Price Behavior**

| Direction | Trigger Condition | Action |
|-----------|------------------|--------|
| `buy` (LONG) | Price > pause_price | Pause new orders (keep existing) |
| `sell` (SHORT) | Price < pause_price | Pause new orders (keep existing) |

**Example:**
- LONG strategy with `--pause-price 110000`
- If BTC rises to 110,001 → **Bot pauses** (resumes when price drops)

---

## 📊 Parameter Recommendations

### **Conservative Trader**
```
--take-profit 0.005      # 0.5% profit
--max-orders 25          # Limit exposure
--grid-step 0.03         # Tight grid (3%)
--wait-time 30           # Slower execution
```

### **Moderate Trader**
```
--take-profit 0.008      # 0.8% profit
--max-orders 50          # Medium exposure
--grid-step 0.06         # Medium grid (6%)
--wait-time 20           # Standard timing
```

### **Aggressive Trader**
```
--take-profit 0.01       # 1% profit
--max-orders 100         # High exposure
--grid-step 0.1          # Wide grid (10%)
--wait-time 10           # Fast execution
```

---

## 🔍 Available Exchanges

| Exchange | Command | Notes |
|----------|---------|-------|
| Lighter | `--exchange lighter` | ✅ Fully supported |
| GRVT | `--exchange grvt` | ✅ Fully supported |
| EdgeX | `--exchange edgex` | ✅ Fully supported |
| Backpack | `--exchange backpack` | ✅ Fully supported |
| Paradex | `--exchange paradex` | ⚠️ Dependency conflicts |
| Aster | `--exchange aster` | ✅ Fully supported |

---

## 🆘 Common Issues

### **"Grid strategy requires --direction parameter"**
**Solution:** Add `--direction buy` or `--direction sell`

### **"Grid strategy requires --take-profit parameter"**
**Solution:** Add `--take-profit 0.008` (or your desired %)

### **"No module named 'exchange_clients'"**
**Solution:** Install exchange clients:
```bash
pip install -e './exchange_clients[all]'
```

---

## 📚 More Help

```bash
# See all available options
python runbot.py --help

# View available exchanges
python -c "from exchange_clients.factory import ExchangeFactory; print(ExchangeFactory.get_supported_exchanges())"
```

---

**Last Updated:** 2025-10-07  
**Version:** 2.0 (Shared Exchange Library)

