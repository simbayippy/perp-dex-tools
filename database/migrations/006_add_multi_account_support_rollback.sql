-- ============================================================================
-- Migration 006 ROLLBACK: Remove Multi-Account Support
-- ============================================================================
-- Safely removes multi-account tables and columns added in migration 006
-- WARNING: This will delete all account data and unlink positions from accounts
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Remove triggers
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DROP TRIGGER IF EXISTS update_accounts_updated_at ON accounts;
DROP TRIGGER IF EXISTS update_account_exchange_credentials_updated_at ON account_exchange_credentials;
DROP TRIGGER IF EXISTS update_account_exchange_sharing_updated_at ON account_exchange_sharing;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Remove indexes
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- strategy_positions indexes
DROP INDEX IF EXISTS idx_positions_account_status;
DROP INDEX IF EXISTS idx_positions_account;

-- account_exchange_sharing indexes
DROP INDEX IF EXISTS idx_account_sharing_exchange;
DROP INDEX IF EXISTS idx_account_sharing_shared;
DROP INDEX IF EXISTS idx_account_sharing_primary;

-- account_exchange_credentials indexes
DROP INDEX IF EXISTS idx_account_creds_composite;
DROP INDEX IF EXISTS idx_account_creds_active;
DROP INDEX IF EXISTS idx_account_creds_exchange;
DROP INDEX IF EXISTS idx_account_creds_account;

-- accounts indexes
DROP INDEX IF EXISTS idx_accounts_active;
DROP INDEX IF EXISTS idx_accounts_name;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Remove column from strategy_positions
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALTER TABLE strategy_positions DROP COLUMN IF EXISTS account_id;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Drop tables (order matters due to foreign key constraints)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DROP TABLE IF EXISTS account_exchange_sharing CASCADE;
DROP TABLE IF EXISTS account_exchange_credentials CASCADE;
DROP TABLE IF EXISTS accounts CASCADE;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Success notification
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DO $$
BEGIN
    RAISE NOTICE '✅ Migration 006 rolled back successfully!';
    RAISE NOTICE '⚠️  All account data has been removed';
    RAISE NOTICE '⚠️  Positions are no longer linked to accounts';
END $$;

