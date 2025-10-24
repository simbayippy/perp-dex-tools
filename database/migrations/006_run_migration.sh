#!/bin/bash
# Quick script to run migration 006 (Multi-Account Support)

echo "Running Migration 006: Multi-Account Support"
echo "=============================================="

cd "$(dirname "$0")/../.."

echo "Applying migration..."
python scripts/run_migration.py database/migrations/006_add_multi_account_support.sql

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Migration 006 applied successfully!"
    echo ""
    echo "📋 Next steps:"
    echo "   1. Create a default account:"
    echo "      INSERT INTO accounts (account_name, description) VALUES ('default', 'Default account');"
    echo ""
    echo "   2. (Optional) Test rollback:"
    echo "      python scripts/run_migration.py database/migrations/006_add_multi_account_support_rollback.sql"
else
    echo ""
    echo "❌ Migration failed! Check error messages above."
    exit 1
fi

