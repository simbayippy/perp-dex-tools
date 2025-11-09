-- ============================================================================
-- Migration 010: Add Telegram User ID Mapping
-- ============================================================================
-- Adds telegram_user_id column to users table for linking Telegram users
-- to database users.
--
-- Key Features:
-- - telegram_user_id: Links Telegram user ID (integer) to database user
-- - Unique constraint: One Telegram user can only link to one database user
-- - Nullable: Users don't require Telegram linking
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Add telegram_user_id column to users table
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Add telegram_user_id column (nullable, unique)
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT UNIQUE;

-- Index for telegram_user_id lookups
CREATE INDEX IF NOT EXISTS idx_users_telegram_user_id ON users(telegram_user_id) WHERE telegram_user_id IS NOT NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON COLUMN users.telegram_user_id IS 'Telegram user ID (integer) for linking Telegram users to database users. Unique constraint ensures one Telegram user maps to one database user.';

