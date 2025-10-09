# Interactive Configuration System - Implementation Plan

## 🎯 Goal
Transform the CLI-only `runbot.py` into an interactive, user-friendly configuration system inspired by Hummingbot, while fixing the multi-exchange architecture.

## 📊 Current Issues

### 1. **Architecture Issues**
- `factory.py`: Only accepts 1 exchange client
- `trading_bot.py`: Single exchange design
- `runbot.py`: CLI args only, poor UX for complex strategies
- Funding arb needs multiple exchange clients for long/short sides

### 2. **UX Issues**
- No validation of parameters
- Hard to remember all CLI flags
- No guidance on valid values
- No config file persistence
- Error-prone for complex strategies

## 🏗️ Proposed Architecture

### Phase 1: Multi-Exchange Support

#### 1.1 Update `ExchangeFactory`
```python
# Before (single client)
exchange_client = ExchangeFactory.create_exchange('lighter', config)

# After (multi-client support)
exchange_clients = ExchangeFactory.create_multiple_exchanges(
    exchanges=['lighter', 'grvt', 'backpack'],
    config=config
)
# Returns: {'lighter': LighterClient, 'grvt': GrvtClient, 'backpack': BackpackClient}
```

#### 1.2 Update `TradingBot`
```python
class TradingBot:
    def __init__(self, config: TradingConfig):
        # Support both single and multiple exchanges
        if config.strategy in ['funding_arbitrage']:
            # Multi-exchange strategies
            self.exchange_clients = ExchangeFactory.create_multiple_exchanges(...)
            self.primary_exchange_client = self.exchange_clients[config.exchange]
        else:
            # Single-exchange strategies (grid, etc.)
            self.exchange_client = ExchangeFactory.create_exchange(...)
            self.exchange_clients = None
```

#### 1.3 Update `StrategyFactory`
```python
# Before
strategy = StrategyFactory.create_strategy(name, config, exchange_client)

# After
strategy = StrategyFactory.create_strategy(
    name, 
    config, 
    exchange_client=exchange_client,  # For single-exchange strategies
    exchange_clients=exchange_clients  # For multi-exchange strategies
)
```

### Phase 2: Interactive Configuration Builder

#### 2.1 Create `config_builder.py`
```python
class InteractiveConfigBuilder:
    """
    Hummingbot-style interactive configuration builder.
    
    Features:
    - Step-by-step prompts
    - Input validation
    - Default values
    - Help text
    - Config persistence (YAML/JSON)
    """
    
    def build_config_for_strategy(self, strategy_name: str) -> TradingConfig:
        """Build config interactively based on strategy requirements."""
        pass
```

#### 2.2 Strategy Parameter Schema
```python
# strategies/implementations/funding_arbitrage/schema.py
FUNDING_ARB_SCHEMA = {
    "name": "funding_arbitrage",
    "display_name": "Funding Rate Arbitrage",
    "description": "Delta-neutral funding rate arbitrage across multiple DEXs",
    
    "parameters": [
        {
            "key": "primary_exchange",
            "prompt": "Which exchange should be your PRIMARY exchange?",
            "type": "choice",
            "choices": ["lighter", "grvt", "backpack", "edgex", "aster"],
            "required": True,
            "help": "This exchange will be used for the main connection"
        },
        {
            "key": "scan_exchanges",
            "prompt": "Which exchanges should we scan for opportunities? (comma-separated)",
            "type": "multi_choice",
            "choices": ["lighter", "grvt", "backpack", "edgex", "aster", "paradex"],
            "default": "lighter,grvt,backpack",
            "required": True,
            "help": "We'll look for funding rate divergences across these exchanges"
        },
        {
            "key": "target_exposure",
            "prompt": "What is your target position size per side (USD)?",
            "type": "decimal",
            "default": "100",
            "min": 10,
            "max": 100000,
            "required": True,
            "help": "This is the USD value of each long/short position"
        },
        {
            "key": "min_profit_rate",
            "prompt": "Minimum profit rate to enter position (e.g., 0.0001 = 0.01%)?",
            "type": "decimal",
            "default": "0.0001",
            "min": 0.00001,
            "max": 0.1,
            "required": True,
            "help": "Only enter positions with profit above this threshold"
        },
        {
            "key": "max_oi_usd",
            "prompt": "Maximum open interest filter (USD)?",
            "type": "decimal",
            "default": "10000000",
            "min": 1000,
            "required": False,
            "help": "For point farming, use lower values (e.g., 50000). For pure profit, use high values."
        },
        {
            "key": "risk_strategy",
            "prompt": "Which risk management strategy?",
            "type": "choice",
            "choices": ["combined", "profit_erosion", "divergence_flip"],
            "default": "combined",
            "required": True,
            "help": "combined = all strategies, profit_erosion = close when profit drops, divergence_flip = close when rates flip"
        },
        {
            "key": "max_positions",
            "prompt": "Maximum number of concurrent positions?",
            "type": "integer",
            "default": "5",
            "min": 1,
            "max": 50,
            "required": False,
            "help": "Limit the number of open funding arb positions"
        },
        {
            "key": "dry_run",
            "prompt": "Run in dry-run mode (no real trades)?",
            "type": "boolean",
            "default": "true",
            "required": False,
            "help": "Test the strategy without placing real orders"
        }
    ]
}
```

#### 2.3 Interactive Flow Example
```
╔════════════════════════════════════════════════════════════════╗
║        Funding Rate Arbitrage - Configuration Wizard          ║
╚════════════════════════════════════════════════════════════════╝

Strategy: Funding Rate Arbitrage
Description: Delta-neutral funding rate arbitrage across multiple DEXs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 1/8: Primary Exchange
─────────────────────────────────────────────────────────────────
Which exchange should be your PRIMARY exchange?

Available: lighter, grvt, backpack, edgex, aster

  [?] This exchange will be used for the main connection

Enter your choice >>> lighter

✓ Primary exchange set to: lighter

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 2/8: Scan Exchanges
─────────────────────────────────────────────────────────────────
Which exchanges should we scan for opportunities? (comma-separated)

Available: lighter, grvt, backpack, edgex, aster, paradex
Default: lighter,grvt,backpack

  [?] We'll look for funding rate divergences across these exchanges

Enter exchanges (or press Enter for default) >>> lighter,grvt,backpack,edgex

✓ Will scan: lighter, grvt, backpack, edgex

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 3/8: Position Size
─────────────────────────────────────────────────────────────────
What is your target position size per side (USD)?

Default: 100
Range: 10 - 100000

  [?] This is the USD value of each long/short position

Enter amount >>> 50

✓ Position size: $50 per side

... (continue for all parameters)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Configuration Summary:
─────────────────────────────────────────────────────────────────
Strategy:          Funding Rate Arbitrage
Primary Exchange:  lighter
Scan Exchanges:    lighter, grvt, backpack, edgex
Position Size:     $50 per side
Min Profit:        0.01%
Max OI:            $10,000,000
Risk Strategy:     combined
Max Positions:     5
Dry Run:           Yes

Save this configuration? [Y/n] >>> Y
Configuration saved to: configs/funding_arb_2025_10_09.yml

Start trading bot now? [Y/n] >>> Y

Starting bot...
```

### Phase 3: Config File Support

#### 3.1 YAML Config Format
```yaml
# configs/funding_arb_example.yml
strategy: funding_arbitrage

exchanges:
  primary: lighter
  scan: [lighter, grvt, backpack, edgex]

position:
  target_exposure_usd: 50
  max_positions: 5
  max_total_exposure_usd: 500

profitability:
  min_profit_rate: 0.0001  # 0.01%
  min_divergence: 0.0001
  max_oi_usd: 10000000

risk_management:
  strategy: combined
  profit_erosion_threshold: 0.5
  max_position_age_hours: 168

execution:
  dry_run: true
  check_interval_seconds: 60
```

#### 3.2 Launch Options
```bash
# Option 1: Interactive mode (new)
python runbot.py --interactive

# Option 2: From config file (new)
python runbot.py --config configs/funding_arb_example.yml

# Option 3: CLI args (existing, still supported)
python runbot.py --strategy funding_arbitrage --exchange lighter ...

# Option 4: Hybrid (config + overrides)
python runbot.py --config funding_arb.yml --dry-run false --target-exposure 100
```

## 📝 Implementation Steps

### Step 1: Multi-Exchange Architecture (2-3 hours)
- [ ] Add `create_multiple_exchanges()` to `ExchangeFactory`
- [ ] Update `TradingBot.__init__()` to handle multi-exchange
- [ ] Update `StrategyFactory.create_strategy()` signature
- [ ] Update funding arb strategy to receive correct clients
- [ ] Test with multiple exchange clients

### Step 2: Parameter Schema System (1-2 hours)
- [ ] Create `strategies/base_schema.py` with parameter types
- [ ] Create schema for funding arbitrage
- [ ] Create schema for grid strategy
- [ ] Add schema validation utilities

### Step 3: Interactive Config Builder (3-4 hours)
- [ ] Create `config_builder.py` with interactive prompts
- [ ] Add input validation and help text
- [ ] Add parameter defaults and ranges
- [ ] Create config summary display
- [ ] Add config persistence (YAML)

### Step 4: Update `runbot.py` (1 hour)
- [ ] Add `--interactive` mode
- [ ] Add `--config` file support
- [ ] Keep backward compatibility with CLI args
- [ ] Add config override support

### Step 5: Testing & Documentation (1 hour)
- [ ] Test all launch modes
- [ ] Test multi-exchange initialization
- [ ] Update README with new UX flow
- [ ] Create example config files

**Total Estimated Time: 8-11 hours**

## 🎨 Libraries to Consider

### For Interactive Prompts
- **`questionary`** (recommended): Beautiful interactive prompts
- **`inquirer`**: Alternative prompt library
- **`click`**: CLI framework with prompt support

### For Config Files
- **`pyyaml`**: YAML parsing (already used?)
- **`pydantic`**: Config validation (already used!)

### For Terminal UI (Phase 1 - Future)
- **`rich`**: Beautiful terminal output
- **`textual`**: Full TUI framework (like Hummingbot)

## 🚀 Quick Start Implementation

Want me to start implementing? I can begin with:

1. **Phase 1**: Multi-exchange architecture fixes
2. **Phase 2**: Interactive config builder with `questionary`
3. **Phase 3**: YAML config support

This will give you a production-ready, user-friendly configuration system!

