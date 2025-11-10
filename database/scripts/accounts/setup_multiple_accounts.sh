#!/bin/bash
# Setup multiple trading accounts from separate .env files
#
# Usage:
#   ./database/scripts/setup_multiple_accounts.sh

set -e  # Exit on error

echo "🚀 Multi-Account Setup Script"
echo "======================================"
echo ""

# Check if .env files exist
ENV_FILES=(.env.*)

if [ ${#ENV_FILES[@]} -eq 0 ]; then
    echo "❌ No .env.* files found"
    echo ""
    echo "Create .env files for each account:"
    echo "  .env.acc1  (account 1 credentials)"
    echo "  .env.acc2  (account 2 credentials)"
    echo "  .env.acc3  (account 3 credentials)"
    exit 1
fi

# Backup original .env
if [ -f .env ]; then
    echo "📦 Backing up current .env to .env.backup"
    cp .env .env.backup
fi

echo "Found ${#ENV_FILES[@]} account configuration(s):"
for env_file in "${ENV_FILES[@]}"; do
    echo "  - $env_file"
done
echo ""

# Process each .env file
for env_file in "${ENV_FILES[@]}"; do
    # Extract account name from filename (e.g., .env.acc1 -> acc1)
    account_name=$(basename "$env_file" | sed 's/^\.env\.//')
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔐 Setting up account: $account_name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # Copy env file to .env
    cp "$env_file" .env
    
    proxy_file="proxies.${account_name}.txt"
    proxy_args=()
    if [ -f "$proxy_file" ]; then
        echo "🌐 Found proxy list: $proxy_file"
        proxy_args=(--proxy-file "$proxy_file" --proxy-label-prefix "${account_name}_proxy")
    fi

    # Add account to database
    python database/scripts/accounts/add_account.py --from-env --account-name "$account_name" --env-file "$env_file" "${proxy_args[@]}"
    
    echo ""
done

# Restore original .env if backup exists
if [ -f .env.backup ]; then
    echo "📦 Restoring original .env"
    mv .env.backup .env
fi

echo ""
echo "======================================"
echo "✅ All accounts setup complete!"
echo "======================================"
echo ""
echo "📋 Verify accounts:"
echo "   python database/scripts/accounts/list_accounts.py"
echo ""
echo "🚀 Run bots:"
echo "   python runbot.py --config config.yml --account acc1"
echo "   python runbot.py --config config.yml --account acc2"
echo "   python runbot.py --config config.yml --account acc3"
