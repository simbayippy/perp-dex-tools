#!/bin/bash
# Run all migrations in order

echo "Running all database migrations..."
echo "=================================="

cd "$(dirname "$0")/../.."

python scripts/run_migration.py database/migrations/001_add_dex_symbols_updated_at.sql
python scripts/run_migration.py database/migrations/002_add_missing_opportunity_columns.sql
python scripts/run_migration.py database/migrations/003_rename_opportunity_dex_fields.sql
python scripts/run_migration.py database/migrations/004_add_strategy_tables.sql
python scripts/run_migration.py database/migrations/005_create_dashboard_tables.sql
python scripts/run_migration.py database/migrations/006_add_multi_account_support.sql

echo ""
echo "=================================="
echo "All migrations completed!"
