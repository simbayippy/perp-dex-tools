-- ============================================================================
-- Migration 008: Add Users and API Keys
-- ============================================================================
-- Adds user management and API key authentication for REST API access.
--
-- Key Features:
-- - users: User accounts for API access (one user can have multiple trading accounts)
-- - api_keys: API keys linked to users (not accounts) for authentication
-- - Admin support: Users can be marked as admins (access all accounts)
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: users
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- User accounts for API access. One user can own multiple trading accounts.

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255),
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for users
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_admin ON users(is_admin) WHERE is_admin = TRUE;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: api_keys
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- API keys for REST API authentication. Linked to users (not accounts).

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL,  -- bcrypt/argon2 hashed key
    key_prefix VARCHAR(16) NOT NULL,  -- First 8-16 chars for display (e.g., "perp_a1b2")
    name VARCHAR(255),  -- Optional descriptive name
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP,  -- Optional expiration
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for api_keys
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_api_keys_user_active ON api_keys(user_id, is_active);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Update triggers
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE users IS 'User accounts for REST API access. One user can own multiple trading accounts.';
COMMENT ON TABLE api_keys IS 'API keys for REST API authentication. Linked to users (not accounts).';

COMMENT ON COLUMN users.username IS 'Unique username identifier';
COMMENT ON COLUMN users.is_admin IS 'If true, user can access all accounts (including legacy accounts with user_id = NULL)';
COMMENT ON COLUMN users.metadata IS 'Flexible JSONB storage for user-specific settings';

COMMENT ON COLUMN api_keys.key_hash IS 'Hashed API key (bcrypt/argon2) - never store plaintext';
COMMENT ON COLUMN api_keys.key_prefix IS 'First 8-16 characters of key for display/identification (e.g., "perp_a1b2")';
COMMENT ON COLUMN api_keys.name IS 'Optional descriptive name for the key (e.g., "Telegram Bot Key")';
COMMENT ON COLUMN api_keys.last_used_at IS 'Timestamp of last successful API key usage';
COMMENT ON COLUMN api_keys.expires_at IS 'Optional expiration timestamp (NULL = never expires)';

