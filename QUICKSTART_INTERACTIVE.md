# 🚀 Quick Start - Interactive Configuration

## Installation

```bash
pip install -r requirements_interactive.txt
```

## Three Ways to Run

### 1️⃣ Interactive Mode (Recommended)

```bash
python runbot.py --interactive
```

**Best for:**
- First-time users
- Learning the system
- Exploring parameters
- Creating new configurations

### 2️⃣ Config File Mode

```bash
# First, create example configs
python config_yaml.py

# Then run with a config
python runbot.py --config configs/example_funding_arbitrage.yml
```

**Best for:**
- Production use
- Reproducible setups
- Version control
- Team collaboration

### 3️⃣ CLI Args Mode

```bash
python runbot.py \
  --strategy funding_arbitrage \
  --exchange lighter \
  --ticker BTC \
  --quantity 1 \
  --target-exposure 100 \
  --exchanges lighter,grvt \
  --strategy-params dry_run=true
```

**Best for:**
- Quick tests
- Automation scripts
- Legacy compatibility

## 📖 Full Documentation

See `docs/INTERACTIVE_CONFIG_GUIDE.md` for complete guide.

## 🎯 Quick Examples

### Example 1: Configure Funding Arbitrage Interactively

```bash
python runbot.py --interactive
```

Follow the prompts:
1. Select "Funding Rate Arbitrage"
2. Choose primary exchange: `lighter`
3. Select scan exchanges: `lighter,grvt,backpack`
4. Set position size: `100` (USD per side)
5. Set min profit: `0.0001` (0.01%)
6. Enable dry run: `Yes`
7. Save config: `Yes`
8. Start bot: `Yes`

### Example 2: Use Saved Config

```bash
python runbot.py --config configs/my_funding_arb.yml
```

### Example 3: Override Config Parameters

```bash
python runbot.py \
  --config configs/my_funding_arb.yml \
  --strategy-params dry_run=false target_exposure=200
```

## ❓ Help

```bash
# See all options
python runbot.py --help

# Interactive mode help
python runbot.py --interactive

# Generate example configs
python config_yaml.py
```

## 📚 Learn More

- **User Guide:** `docs/INTERACTIVE_CONFIG_GUIDE.md`
- **Architecture:** `docs/MULTI_EXCHANGE_IMPLEMENTATION.md`
- **Project Structure:** `docs/PROJECT_STRUCTURE.md`

---

**Happy Trading! 🎉**

