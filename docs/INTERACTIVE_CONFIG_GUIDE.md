# Interactive Configuration System - User Guide

## 📖 Overview

The trading bot now supports **three launch modes** for maximum flexibility:

1. **Interactive Mode** - Step-by-step wizard (recommended for first-time users)
2. **Config File Mode** - Load saved configurations
3. **CLI Args Mode** - Traditional command-line arguments (backward compatible)

## 🚀 Quick Start

### Installation

First, install the additional dependencies for interactive configuration:

```bash
pip install -r requirements_interactive.txt
```

This will install:
- `questionary` - Beautiful interactive prompts
- `pyyaml` - YAML configuration file support

### Mode 1: Interactive Configuration (Recommended)

Launch the interactive wizard:

```bash
python runbot.py --interactive
```

or

```bash
python runbot.py -i
```

The wizard will guide you through:
1. Strategy selection (Grid or Funding Arbitrage)
2. Parameter configuration (step-by-step with help text)
3. Configuration summary and confirmation
4. Optional: Save configuration to file
5. Optional: Start bot immediately

**Benefits:**
- ✅ No need to remember parameter names
- ✅ Built-in validation and help text
- ✅ See all options with descriptions
- ✅ Save configurations for reuse
- ✅ Great for learning the system

### Mode 2: Config File

First, create example config files:

```bash
python config_yaml.py
```

This creates:
- `configs/example_funding_arbitrage.yml`
- `configs/example_grid.yml`

Edit these files with your desired parameters, then run:

```bash
python runbot.py --config configs/my_strategy.yml
```

or

```bash
python runbot.py -c configs/my_strategy.yml
```

**Benefits:**
- ✅ Version control your configurations
- ✅ Share configurations with team
- ✅ Quick switching between strategies
- ✅ Reproducible setups

### Mode 3: CLI Arguments (Legacy)

Traditional command-line launch:

**Grid Strategy:**
```bash
python runbot.py \
  --strategy grid \
  --exchange lighter \
  --ticker BTC \
  --quantity 100 \
  --take-profit 0.008 \
  --direction buy
```

**Funding Arbitrage:**
```bash
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --quantity 1 \
  --target-exposure 100 \
  --exchanges lighter,grvt,backpack \
  --strategy-params dry_run=true
```

**Benefits:**
- ✅ Scriptable and automatable
- ✅ Quick one-off runs
- ✅ Backward compatible with existing scripts

## 📝 Configuration File Format

### Funding Arbitrage Example

```yaml
strategy: funding_arbitrage
created_at: '2025-10-09T15:30:45.123456'
version: '1.0'
config:
  # Exchanges
  primary_exchange: lighter
  scan_exchanges:
  - lighter
  - grvt
  - backpack
  - edgex
  
  # Position Sizing
  target_exposure: 100  # USD per side
  max_positions: 5
  max_total_exposure_usd: 1000
  
  # Profitability
  min_profit_rate: 0.0001  # 0.01%
  max_oi_usd: 10000000     # $10M
  
  # Risk Management
  risk_strategy: combined
  profit_erosion_threshold: 0.5  # 50%
  max_position_age_hours: 168    # 1 week
  
  # Execution
  max_new_positions_per_cycle: 2
  check_interval_seconds: 60
  dry_run: true
```

### Grid Strategy Example

```yaml
strategy: grid
created_at: '2025-10-09T15:30:45.123456'
version: '1.0'
config:
  # Exchange
  exchange: lighter
  ticker: BTC
  
  # Grid Setup
  direction: buy
  quantity: 100
  take_profit: 0.008  # 0.8%
  
  # Grid Spacing
  grid_step: 0.002    # 0.2%
  max_orders: 25
  
  # Timing
  wait_time: 10
  random_timing: false
  
  # Advanced
  dynamic_profit: false
  stop_price: null
  pause_price: null
```

## 🎯 Strategy Parameters

### Funding Arbitrage

#### Exchange Configuration
- **primary_exchange**: Main exchange for risk management
  - Choices: `lighter`, `grvt`, `backpack`, `edgex`, `aster`, `paradex`
- **scan_exchanges**: Exchanges to scan for opportunities (list)

#### Position Sizing
- **target_exposure**: USD value per side (e.g., `100` = $100 long + $100 short)
  - Min: 10, Max: 100,000
- **max_positions**: Maximum concurrent positions
  - Min: 1, Max: 50, Default: 5
- **max_total_exposure_usd**: Total notional value limit
  - Min: 100, Max: 1,000,000, Default: 1,000

#### Profitability
- **min_profit_rate**: Minimum net profit after fees
  - Min: 0.00001 (0.001%), Max: 0.1 (10%), Default: 0.0001 (0.01%)
- **max_oi_usd**: Maximum open interest filter
  - For point farming: Use low values (e.g., 50,000)
  - For pure profit: Use high values (e.g., 10M+)
  - Default: 10,000,000

#### Risk Management
- **risk_strategy**: Risk management approach
  - `combined` (recommended): All risk checks
  - `profit_erosion`: Close when profit drops
  - `divergence_flip`: Close when rates flip
  - `time_based`: Close after fixed duration
- **profit_erosion_threshold**: Close when profit drops to X% of entry
  - Min: 0.1 (10%), Max: 0.9 (90%), Default: 0.5 (50%)
- **max_position_age_hours**: Force close after this many hours
  - Min: 1, Max: 720 (30 days), Default: 168 (1 week)

#### Execution
- **max_new_positions_per_cycle**: Rate limit for new positions
  - Min: 1, Max: 10, Default: 2
- **check_interval_seconds**: How often to check positions
  - Min: 10, Max: 300, Default: 60
- **dry_run**: Test mode (no real trades)
  - Default: true

### Grid Strategy

#### Exchange
- **exchange**: Exchange to trade on
- **ticker**: Trading pair (e.g., `BTC`, `ETH`)

#### Grid Setup
- **direction**: `buy` or `sell`
- **quantity**: Order size per order
- **take_profit**: Profit percentage per trade
  - Min: 0.001 (0.1%), Max: 0.1 (10%)

#### Grid Spacing
- **grid_step**: Minimum distance between orders
  - Min: 0.0001 (0.01%), Max: 0.1 (10%), Default: 0.002 (0.2%)
- **max_orders**: Maximum active orders
  - Min: 1, Max: 100, Default: 25

#### Timing
- **wait_time**: Seconds between orders
  - Min: 1, Max: 300, Default: 10
- **random_timing**: Add randomness to timing
  - Default: false

#### Advanced
- **dynamic_profit**: Adjust profit based on volatility
  - Default: false
- **stop_price**: Emergency exit price
  - Optional
- **pause_price**: Pause new orders at this price
  - Optional

## 🔧 Advanced Usage

### Config File Override with CLI Args

You can load a config file and override specific parameters:

```bash
python runbot.py \
  --config configs/my_funding_arb.yml \
  --strategy-params dry_run=false target_exposure=200
```

### Standalone Interactive Config Builder

Run the config builder without starting the bot:

```bash
python config_builder.py
```

This will:
1. Guide you through configuration
2. Save the config to a file
3. Exit without starting the bot

Then you can use the saved config later:

```bash
python runbot.py --config configs/funding_arb_20251009_153045.yml
```

### Generate Example Configs

```bash
python config_yaml.py
```

Creates example configs in `./configs/` directory.

### Validate a Config File

```python
from config_yaml import validate_config_file

is_valid, error = validate_config_file("configs/my_strategy.yml")
if not is_valid:
    print(f"Error: {error}")
```

## 🐛 Troubleshooting

### Issue: "questionary not found"

**Solution:**
```bash
pip install -r requirements_interactive.txt
```

### Issue: "Invalid config file"

**Solution:** Validate your YAML syntax and ensure all required fields are present. Use example configs as templates.

### Issue: "Strategy validation failed"

**Solution:** Check parameter ranges and types. The interactive mode provides helpful error messages.

### Issue: Interactive mode not displaying properly

**Solution:** Ensure your terminal supports ANSI color codes. On Windows, use Windows Terminal or enable ANSI support.

## 📚 Tips & Best Practices

1. **Start with Interactive Mode** - Get familiar with all parameters
2. **Save Successful Configs** - Build a library of working configurations
3. **Use Dry Run First** - Always test with `dry_run: true` before live trading
4. **Version Control Configs** - Store configs in git for reproducibility
5. **Document Custom Configs** - Add comments in YAML files to explain your choices
6. **Test Config File Loading** - Validate configs before production use

## 🎨 Interactive Mode Preview

```
══════════════════════════════════════════════════════════════════
  Trading Bot - Interactive Configuration Wizard
══════════════════════════════════════════════════════════════════

? Which strategy would you like to run?
  ❯ Funding Rate Arbitrage - Delta-neutral funding rate arbitrage...
    Grid Trading - Place multiple orders at different price levels...

══════════════════════════════════════════════════════════════════
  Funding Rate Arbitrage - Configuration
══════════════════════════════════════════════════════════════════

[1/14] Which exchange should be your PRIMARY exchange?
  ❓ This exchange will handle the main connection and risk management
  ❯ lighter
    grvt
    backpack
    edgex
    aster
    paradex

✓ primary_exchange: lighter

[2/14] Which exchanges should we scan for opportunities? (comma-separated)
  ❓ We'll look for funding rate divergences across these exchanges
  [lighter, grvt, backpack]

✓ scan_exchanges: lighter, grvt, backpack

...

══════════════════════════════════════════════════════════════════
  Configuration Summary
══════════════════════════════════════════════════════════════════

Strategy: Funding Rate Arbitrage

Exchanges:
  • primary_exchange: lighter
  • scan_exchanges: lighter, grvt, backpack

Position Sizing:
  • target_exposure: 100
  • max_positions: 5
  • max_total_exposure_usd: 1000

...

? Does this configuration look correct? (Y/n)

💾 Would you like to save this configuration? (Y/n)

📁 Configuration filename: funding_arb_20251009_153045.yml

✓ Configuration saved to: configs/funding_arb_20251009_153045.yml

🚀 Start trading bot now? (Y/n)
```

## 📞 Support

For issues or questions:
1. Check this guide first
2. Review example configs in `./configs/`
3. Run with `--help` for CLI options
4. Check logs in `./logs/` directory

## 🔄 Migration from Old CLI System

If you have existing scripts using the old CLI format, they will continue to work! The CLI args mode is fully backward compatible.

To migrate to config files:
1. Run `python runbot.py --interactive` with your current parameters
2. Save the configuration
3. Update your scripts to use `--config` mode

## 📦 Files Created by Interactive System

- `configs/` - Configuration files (YAML)
- `strategies/base_schema.py` - Parameter schema system
- `strategies/implementations/*/schema.py` - Strategy-specific schemas
- `config_builder.py` - Interactive wizard
- `config_yaml.py` - YAML file handling
- `requirements_interactive.txt` - Additional dependencies

---

**Happy Trading! 🚀**

