# 🚀 Quick Start Guide

Essential commands to get started with the trading bot after installation.

```bash
1. python -m trading_config.config_builder 
2. python -m funding_rate_service.main
3. python runbot.py --config configs/funding_arb_test.yml
```

---

## 📋 Available Commands

### **1. Interactive Configuration Builder** 🎨

Create a configuration file with a guided wizard:

```bash
python -m trading_config.config_builder
```

This will:
- Let you choose a strategy (Grid or Funding Arbitrage)
- Prompt for all required parameters
- Validate your inputs
- Save a YAML config file

Then run the bot:
```bash
python runbot.py --config configs/your_config.yml
```

---

### **2. Generate Example Configs**

Create example YAML files to edit:

```bash
python -m trading_config.config_yaml
```

This creates:
- `configs/example_funding_arbitrage.yml`
- `configs/example_grid.yml`

Edit them and run:
```bash
python runbot.py --config configs/example_funding_arbitrage.yml
```

---

### **3. Direct CLI Mode** (Quick Testing)

Run strategies directly with command-line arguments:

#### **Grid Strategy:**
```bash
python runbot.py \
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

#### **Funding Arbitrage:**
```bash
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --quantity 1 \
  --target-exposure 1000 \
  --min-profit-rate 0.001 \
  --exchanges lighter,backpack,edgex
```

> 📖 **See full CLI options:** `CLI_COMMANDS.md`

---

### **4. Funding Rate Service** 📊

Start the FastAPI service to access funding rate data:

```bash
cd funding_rate_service
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

#### **Test the API:**

```bash
# Health check
curl http://localhost:8000/api/v1/health

# List all DEXes
curl http://localhost:8000/api/v1/dexes

# Get current funding rates
curl http://localhost:8000/api/v1/funding-rates/current

# Find arbitrage opportunities
curl "http://localhost:8000/api/v1/opportunities?dex=lighter&min_profit=0.0001"

# Compare rates between two DEXes
curl "http://localhost:8000/api/v1/funding-rates/compare?dex1=lighter&dex2=backpack&symbol=BTC"
```

> 📖 **Full API docs:** Visit `http://localhost:8000/docs` (Swagger UI)

---

### **5. Database Setup** (First Time Only)

If you haven't set up the database yet:

```bash
cd funding_rate_service

# Start PostgreSQL (if using Docker)
docker-compose up -d

# OR use local PostgreSQL (already running on your VPS)
# Just make sure it's running: sudo systemctl status postgresql

# Initialize database
python scripts/init_db.py

# Seed DEX data
python scripts/seed_dexes.py

# Run migrations
cd database/migrations
./RUN_ALL_MIGRATIONS.sh
```

---

## 🎯 Recommended Workflow

### **For First-Time Users:**
1. **Create a config** with the interactive builder:
   ```bash
   python -m trading_config.config_builder
   ```

2. **Run the bot** with your config:
   ```bash
   python runbot.py --config configs/your_config.yml
   ```

### **For Quick Testing:**
1. **Use CLI args** directly:
   ```bash
   python runbot.py --strategy grid --exchange lighter --ticker BTC --quantity 0.001 --take-profit 0.008 --direction buy --max-orders 10
   ```

### **For Funding Arbitrage:**
1. **Start the funding service** (in separate terminal):
   ```bash
   cd funding_rate_service && uvicorn main:app --reload
   ```

2. **Check for opportunities**:
   ```bash
   curl "http://localhost:8000/api/v1/opportunities?min_profit=0.0001"
   ```

3. **Run funding arb strategy**:
   ```bash
   python runbot.py --config configs/funding_arb_config.yml
   ```

---

## 📂 Config Files Location

All config files are saved in:
```
/configs/
  ├── example_funding_arbitrage.yml
  ├── example_grid.yml
  └── your_custom_configs.yml
```

---

## 🆘 Quick Help

```bash
# See all runbot options
python runbot.py --help

# List supported exchanges
python -c "from exchange_clients.factory import ExchangeFactory; print(ExchangeFactory.get_supported_exchanges())"

# Check funding service status
curl http://localhost:8000/api/v1/health

# View funding service API docs
open http://localhost:8000/docs
```

---

## 📚 More Documentation

- **Detailed CLI commands:** `CLI_COMMANDS.md`
- **Full project structure:** `docs/PROJECT_STRUCTURE.md`
- **Funding service API:** `funding_rate_service/docs/API_ENDPOINTS.md`
- **Adding exchanges:** `docs/ADDING_EXCHANGES.md`

---

**Last Updated:** 2025-10-09  
**Version:** 2.5

