#!/bin/bash
# Run all migrations in order

echo "Running all database migrations..."
echo "=================================="

cd "$(dirname "$0")/../.."

python database/scripts/migrations/run_migration.py database/migrations/001_add_dex_symbols_updated_at.sql
python database/scripts/migrations/run_migration.py database/migrations/002_add_missing_opportunity_columns.sql
python database/scripts/migrations/run_migration.py database/migrations/003_rename_opportunity_dex_fields.sql
python database/scripts/migrations/run_migration.py database/migrations/004_add_strategy_tables.sql
python database/scripts/migrations/run_migration.py database/migrations/005_create_dashboard_tables.sql
python database/scripts/migrations/run_migration.py database/migrations/006_add_multi_account_support.sql
python database/scripts/migrations/run_migration.py database/migrations/006_add_multi_account_support_rollup.sql
python database/scripts/migrations/run_migration.py database/migrations/007_add_network_proxies.sql
python database/scripts/migrations/run_migration.py database/migrations/008_add_users_and_api_keys.sql
python database/scripts/migrations/run_migration.py database/migrations/009_add_user_id_to_accounts.sql
python database/scripts/migrations/run_migration.py database/migrations/010_add_telegram_user_mapping.sql
python database/scripts/migrations/run_migration.py database/migrations/011_add_strategy_configs.sql
python database/scripts/migrations/run_migration.py database/migrations/012_add_strategy_runs.sql
python database/scripts/migrations/run_migration.py database/migrations/013_add_safety_limits.sql
python database/scripts/migrations/run_migration.py database/migrations/014_add_audit_log.sql
python database/scripts/migrations/run_migration.py database/migrations/015_add_strategy_notifications.sql
python database/scripts/migrations/run_migration.py database/migrations/016_add_trade_fills_table.sql
python database/scripts/migrations/run_migration.py database/migrations/017_add_insufficient_margin_notification_type.sql

echo ""
echo "=================================="
echo "All migrations completed!"
