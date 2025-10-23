#!/bin/bash
# Run all migrations in order

echo "Running all database migrations..."
echo "=================================="

cd "$(dirname "$0")/../.."

python scripts/run_migration.py database/migrations/001_add_dex_symbols_updated_at.sql
python scripts/run_migration.py database/migrations/002_add_missing_opportunity_columns.sql
python scripts/run_migration.py database/migrations/003_rename_opportunity_dex_fields.sql

echo ""
echo "=================================="
echo "All migrations completed!"
