-- ============================================================================
-- Migration 009: Add user_id to Accounts with Partial Unique Indexes
-- ============================================================================
-- Links accounts to users while maintaining backward compatibility.
--
-- Key Features:
-- - Adds nullable user_id FK to accounts table
-- - Existing accounts get user_id = NULL (backward compatible)
-- - Partial unique indexes: per-user uniqueness for accounts with user_id,
--   global uniqueness for legacy accounts (user_id = NULL)
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Add user_id column to accounts
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Add user_id column (nullable for backward compatibility)
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Index for user_id lookups
CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_accounts_user_active ON accounts(user_id, is_active) WHERE user_id IS NOT NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Remove old global unique constraint and create partial unique indexes
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Drop the old unique constraint on account_name (if it exists as a constraint)
-- Note: The original migration 006 created account_name as UNIQUE in the table definition
-- We need to drop that constraint and replace with partial indexes

-- First, check if there's a unique constraint (PostgreSQL creates this automatically for UNIQUE columns)
DO $$
BEGIN
    -- Drop the unique constraint if it exists
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'accounts_account_name_key'
    ) THEN
        ALTER TABLE accounts DROP CONSTRAINT accounts_account_name_key;
    END IF;
END $$;

-- Create partial unique index for accounts WITH user_id (per-user uniqueness)
-- Multiple users can have accounts with the same name
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_name_unique 
ON accounts(user_id, account_name) 
WHERE user_id IS NOT NULL;

-- Create partial unique index for accounts WITHOUT user_id (global uniqueness for legacy accounts)
-- Only one account with a given name can exist without a user_id
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_name_unique_legacy 
ON accounts(account_name) 
WHERE user_id IS NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON COLUMN accounts.user_id IS 'Optional link to user. NULL for legacy accounts (backward compatible). When set, account_name uniqueness is per-user.';

