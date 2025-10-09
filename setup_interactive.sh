#!/bin/bash
# Setup script for interactive configuration system

echo "═══════════════════════════════════════════════════════════════"
echo "  Trading Bot - Interactive Configuration Setup"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements_interactive.txt

if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    exit 1
fi

echo "✓ Dependencies installed"
echo ""

# Create configs directory
echo "📁 Creating configs directory..."
mkdir -p configs

echo "✓ Configs directory ready"
echo ""

# Generate example configs
echo "📝 Generating example configuration files..."
python config_yaml.py

if [ $? -ne 0 ]; then
    echo "❌ Failed to generate example configs"
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Setup Complete! ✅"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Try the interactive wizard:"
echo "   python runbot.py --interactive"
echo ""
echo "2. Or use an example config:"
echo "   python runbot.py --config configs/example_funding_arbitrage.yml"
echo ""
echo "3. Or use CLI args (backward compatible):"
echo "   python runbot.py --strategy funding_arbitrage --exchange lighter ..."
echo ""
echo "📖 For full documentation, see:"
echo "   docs/INTERACTIVE_CONFIG_GUIDE.md"
echo ""
echo "Happy trading! 🚀"
echo ""

