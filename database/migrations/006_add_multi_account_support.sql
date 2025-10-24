-- ============================================================================
-- Migration 006: Add Multi-Account Support
-- ============================================================================
-- Adds tables and columns to support multiple trading accounts with
-- isolated credentials, positions, and optional account sharing.
--
-- Key Features:
-- - accounts: Core account/wallet entity
-- - account_exchange_credentials: Encrypted exchange credentials per account
-- - account_exchange_sharing: Cross-account credential sharing
-- - strategy_positions.account_id: Links positions to accounts
-- ============================================================================


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: accounts
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Main account/wallet entity representing a trading bot or user account

CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_name VARCHAR(255) UNIQUE NOT NULL,  -- e.g., "main_bot", "test_account"
    wallet_address VARCHAR(255),  -- On-chain wallet address if applicable
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb  -- Flexible storage for account-specific settings
);

-- Indexes for accounts
CREATE INDEX IF NOT EXISTS idx_accounts_name ON accounts(account_name);
CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(is_active) WHERE is_active = TRUE;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: account_exchange_credentials
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Exchange credentials per account (encrypted)

CREATE TABLE IF NOT EXISTS account_exchange_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    exchange_id INTEGER NOT NULL REFERENCES dexes(id) ON DELETE CASCADE,
    
    -- Encrypted credentials (use Fernet or similar)
    api_key_encrypted TEXT,
    secret_key_encrypted TEXT,
    additional_credentials_encrypted JSONB,  -- For exchange-specific extras (passphrase, private key, etc.)
    
    -- Exchange-specific identifiers
    exchange_account_id VARCHAR(255),  -- Internal account ID on the exchange
    subaccount_index INTEGER,  -- For exchanges with subaccounts
    
    is_active BOOLEAN DEFAULT TRUE,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(account_id, exchange_id, subaccount_index)
);

-- Indexes for account_exchange_credentials
CREATE INDEX IF NOT EXISTS idx_account_creds_account ON account_exchange_credentials(account_id);
CREATE INDEX IF NOT EXISTS idx_account_creds_exchange ON account_exchange_credentials(exchange_id);
CREATE INDEX IF NOT EXISTS idx_account_creds_active ON account_exchange_credentials(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_account_creds_composite ON account_exchange_credentials(account_id, exchange_id);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: account_exchange_sharing
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- For shared exchange accounts (e.g., multiple accounts sharing one Backpack KYC)

CREATE TABLE IF NOT EXISTS account_exchange_sharing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    primary_account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    shared_account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    exchange_id INTEGER NOT NULL REFERENCES dexes(id) ON DELETE CASCADE,
    sharing_type VARCHAR(50) NOT NULL DEFAULT 'full',  -- 'full', 'read_only', 'positions_only'
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Prevent sharing an account with itself
    CHECK (primary_account_id != shared_account_id),
    
    -- One primary account can only share one exchange from one shared account
    UNIQUE(primary_account_id, exchange_id)
);

-- Indexes for account_exchange_sharing
CREATE INDEX IF NOT EXISTS idx_account_sharing_primary ON account_exchange_sharing(primary_account_id);
CREATE INDEX IF NOT EXISTS idx_account_sharing_shared ON account_exchange_sharing(shared_account_id);
CREATE INDEX IF NOT EXISTS idx_account_sharing_exchange ON account_exchange_sharing(exchange_id);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Alter strategy_positions: Add account_id column
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Add account_id column to strategy_positions (nullable for backward compatibility)
ALTER TABLE strategy_positions 
ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id) ON DELETE SET NULL;

-- Index for filtering positions by account
CREATE INDEX IF NOT EXISTS idx_positions_account ON strategy_positions(account_id);
CREATE INDEX IF NOT EXISTS idx_positions_account_status ON strategy_positions(account_id, status);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Trigger: Auto-update updated_at timestamp
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Reuse existing update_updated_at_column function from migration 004

-- Apply trigger to accounts
DROP TRIGGER IF EXISTS update_accounts_updated_at ON accounts;
CREATE TRIGGER update_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to account_exchange_credentials
DROP TRIGGER IF EXISTS update_account_exchange_credentials_updated_at ON account_exchange_credentials;
CREATE TRIGGER update_account_exchange_credentials_updated_at
    BEFORE UPDATE ON account_exchange_credentials
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to account_exchange_sharing
DROP TRIGGER IF EXISTS update_account_exchange_sharing_updated_at ON account_exchange_sharing;
CREATE TRIGGER update_account_exchange_sharing_updated_at
    BEFORE UPDATE ON account_exchange_sharing
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE accounts IS 'Trading accounts/bots with isolated credentials and positions';
COMMENT ON TABLE account_exchange_credentials IS 'Encrypted exchange credentials per account (uses Fernet encryption)';
COMMENT ON TABLE account_exchange_sharing IS 'Cross-account credential sharing (e.g., shared Backpack KYC)';

COMMENT ON COLUMN accounts.account_name IS 'Unique identifier for the account (e.g., "main_bot", "test_account")';
COMMENT ON COLUMN accounts.wallet_address IS 'On-chain wallet address if applicable';
COMMENT ON COLUMN accounts.metadata IS 'Flexible JSONB storage for position limits, settings, etc.';

COMMENT ON COLUMN account_exchange_credentials.api_key_encrypted IS 'Fernet-encrypted API key';
COMMENT ON COLUMN account_exchange_credentials.secret_key_encrypted IS 'Fernet-encrypted secret key';
COMMENT ON COLUMN account_exchange_credentials.additional_credentials_encrypted IS 'Encrypted JSONB for exchange-specific extras (passphrase, private_key, etc.)';
COMMENT ON COLUMN account_exchange_credentials.subaccount_index IS 'Subaccount index for exchanges that support subaccounts';

COMMENT ON COLUMN account_exchange_sharing.sharing_type IS 'full: all operations, read_only: queries only, positions_only: position viewing';

COMMENT ON COLUMN strategy_positions.account_id IS 'Links position to owning account (NULL for legacy positions)';


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Success notification
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DO $$
BEGIN
    RAISE NOTICE '✅ Migration 006: Multi-account support tables created successfully!';
    RAISE NOTICE '📋 Next steps:';
    RAISE NOTICE '   1. Create a default account: INSERT INTO accounts (account_name, description) VALUES (''default'', ''Default account'');';
    RAISE NOTICE '   2. Implement credential encryption utilities (Phase 2)';
    RAISE NOTICE '   3. Update FundingArbPositionManager to be account-aware (Phase 3)';
END $$;

