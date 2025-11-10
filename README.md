# 🚀 Perp DEX Tools

**Advanced algorithmic trading system for perpetual futures across multiple decentralized exchanges.**

Cross-DEX funding rate arbitrage • Grid trading • Multi-exchange support • Real-time funding rate tracking

---

## ✨ Features

### **Trading Strategies**
- 📈 **Grid Trading** - Automated buy/sell grids with dynamic profit targets
- 💱 **Funding Rate Arbitrage** - Delta-neutral arbitrage across DEXs with intelligent risk management
- 🔄 **Multi-Exchange Support** - Trade simultaneously across 6+ DEXs (Lighter, GRVT, EdgeX, Backpack, Aster, Paradex)

### **Funding Rate Service**
- 📊 **Real-time Data Collection** - Continuous funding rate monitoring across all DEXs
- 🗄️ **PostgreSQL Storage** - Historical data for analysis and backtesting
- 🔍 **Opportunity Finder** - Automated detection of profitable arbitrage opportunities
- 🌐 **REST API** - FastAPI service with comprehensive endpoints for external service
- 🔧 **Internal Module** - Can be imported and called directly from Python code


### **Developer Experience**
- 🎨 **Interactive Config Builder** - Beautiful CLI wizard for strategy configuration
- 📝 **YAML Configs** - Reproducible, version-controlled configurations
- 🧪 **Comprehensive Tests** - Unit and integration tests for all strategies
- 📚 **Rich Documentation** - Detailed guides for every component

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Trading Client                          │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Grid      │  │  Funding     │  │  Future          │   │
│  │  Strategy  │  │  Arbitrage   │  │  Strategies...   │   │
│  └────────────┘  └──────────────┘  └──────────────────┘   │
│         │                │                   │              │
│         └────────────────┴───────────────────┘              │
│                          │                                  │
│              ┌───────────▼───────────┐                      │
│              │  Exchange Clients     │                      │
│              │  (6 DEX Connectors)   │                      │
│              └───────────┬───────────┘                      │
└────────────────────────────┼────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
    ┌───▼───┐          ┌─────▼─────┐       ┌────▼────┐
    │Lighter│          │   GRVT    │  ...  │ Backpack│
    └───────┘          └───────────┘       └─────────┘

┌─────────────────────────────────────────────────────────────┐
│              Funding Rate Service (FastAPI)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Collection  │─▶│  PostgreSQL  │─▶│   REST API   │     │
│  │   Adapters   │  │   Database   │  │   Endpoints  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Key Design Principles:**
- **Modular Strategy System** - 3-level hierarchy (Base → Categories → Implementations)
- **Shared Exchange Library** - Single source of truth for each DEX
- **Database-Backed Persistence** - PostgreSQL for positions, funding payments, and state
- **Hummingbot-Inspired Patterns** - Event-driven lifecycle, atomic execution, risk management

---

## 🚀 Quick Start

### **1. Installation**

```bash
# Clone the repository
git clone https://github.com/yourusername/perp-dex-tools.git
cd perp-dex-tools

# Install dependencies
pip install -r requirements.txt
pip install -e './exchange_clients[all]'
```

> 📖 **Detailed setup:** See [`INSTALLATION.md`](INSTALLATION.md)

### **2. Configure Environment**

```bash
# Copy environment template
cp env_example.txt .env

# Edit .env with your API keys and settings
nano .env
```

### **3. Run a Strategy**

**Option A: Interactive Config Builder** (Recommended)
```bash
python -m trading_config.config_builder
python runbot.py --config configs/your_config.yml
```

**Option B: Direct CLI**
```bash
python runbot.py \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.001 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 10
```

> 📖 **All commands:** See [`QUICK_START.md`](QUICK_START.md) and [`CLI_COMMANDS.md`](CLI_COMMANDS.md)

---

## 📊 Funding Rate Service

### **Start the Service**

```bash
cd funding_rate_service
uvicorn main:app --reload
```

### **API Examples**

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Get current funding rates
curl http://localhost:8000/api/v1/funding-rates/current

# Find arbitrage opportunities
curl "http://localhost:8000/api/v1/opportunities?min_profit=0.0001"

# Compare two DEXes
curl "http://localhost:8000/api/v1/funding-rates/compare?dex1=lighter&dex2=backpack&symbol=BTC"
```

> 📖 **Full API reference:** Visit `http://localhost:8000/docs` or see [`funding_rate_service/docs/API_ENDPOINTS.md`](funding_rate_service/docs/API_ENDPOINTS.md)

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [`QUICK_START.md`](QUICK_START.md) | Essential commands to get started |
| [`INSTALLATION.md`](INSTALLATION.md) | Detailed installation guide |
| [`CLI_COMMANDS.md`](CLI_COMMANDS.md) | Complete CLI reference with examples |
| [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) | Complete project architecture and structure |
| [`funding_rate_service/docs/API_ENDPOINTS.md`](funding_rate_service/docs/API_ENDPOINTS.md) | REST API documentation |
| [`docs/ADDING_EXCHANGES.md`](docs/ADDING_EXCHANGES.md) | Guide for adding new DEX connectors |

---

## 🎯 Supported Strategies

### **Grid Trading**
Automated grid-based trading with configurable profit targets, direction (long/short), and safety controls.

**Key Features:**
- Dynamic profit targets
- Stop/pause price safety controls
- Random timing (anti-pattern detection)
- Supports all exchanges

**Example:**
```bash
python runbot.py \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.00273 \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 50
```

### **Funding Rate Arbitrage** 🔥 NEW
Delta-neutral arbitrage capturing funding rate divergence across DEXes.

**Key Features:**
- Atomic multi-order execution (ensures delta neutrality)
- Real-time opportunity scanning
- Intelligent risk management (profit erosion, divergence flip)
- Database-backed position tracking
- Multiple rebalancing strategies

**Example:**
```bash
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --target-exposure 1000 \
  --min-profit-rate 0.001 \
  --exchanges lighter,backpack,edgex
```

---

## 🔧 Supported Exchanges

| Exchange | Trading | Funding Data | Status |
|----------|---------|--------------|--------|
| **Lighter** | ✅ | ✅ | Fully supported |
| **GRVT** | ✅ | ✅ | Fully supported |
| **EdgeX** | ✅ | ✅ | Fully supported |
| **Backpack** | ✅ | ✅ | Fully supported |
| **Aster** | ✅ | ✅ | Fully supported |
| **Paradex** | ⚠️ | ✅ | Dependency conflicts |

---

## 🗄️ Database Setup

The funding rate service and trading strategies use PostgreSQL for data persistence.

```bash
cd funding_rate_service

# Option 1: Docker (recommended)
docker-compose up -d

# Option 2: Local PostgreSQL
# Ensure PostgreSQL is running on your system

# Initialize database
python database/scripts/setup/init_db.py
python database/scripts/setup/seed_dexes.py

# Run migrations
cd database/migrations
./RUN_ALL_MIGRATIONS.sh
```

**Database Tables:**
- `dexes` - DEX metadata and health status
- `symbols` - Trading pairs and market data
- `funding_rates` - Historical funding rate data
- `strategy_positions` - Open position tracking
- `funding_payments` - Funding payment history
- `strategy_state` - Strategy execution state

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test suites
pytest tests/strategies/funding_arbitrage/
pytest tests/strategies/funding_arbitrage/test_funding_analyzer_isolated.py
pytest tests/strategies/funding_arbitrage/test_risk_management_isolated.py

# Run funding rate service tests
cd funding_rate_service
pytest tests/
```

---

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Submit a pull request

See [`docs/ADDING_EXCHANGES.md`](docs/ADDING_EXCHANGES.md) for adding new DEX connectors.

---

## ⚠️ Disclaimer

This software is for educational and research purposes only. Trading cryptocurrencies and perpetual futures carries significant risk. Always:
- Test strategies in paper trading mode first
- Never invest more than you can afford to lose
- Understand the risks of leverage and funding rates
- Comply with all applicable laws and regulations

---

## 📄 License

[Your License Here]

---

## 🔗 Links

- **Documentation:** [`docs/`](docs/)
- **API Docs:** `http://localhost:8000/docs` (when service is running)
- **GitHub Issues:** [Report bugs or request features](https://github.com/yourusername/perp-dex-tools/issues)

---

**Last Updated:** 2025-10-09  
**Version:** 2.5 (Interactive Config + Multi-Exchange Strategies)  
**Status:** Production Ready

---

Built with ❤️ for algorithmic traders

