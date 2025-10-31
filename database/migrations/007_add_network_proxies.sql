-- ============================================================================
-- Migration 007: Networking Proxy Tables
-- ============================================================================
-- Introduces proxy management so each trading account can route traffic through
-- dedicated static IPs. Two tables are added:
--   - network_proxies: Catalog of proxy endpoints and encrypted credentials
--   - account_proxy_assignments: Mapping of accounts to proxies with rotation
-- ============================================================================


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: network_proxies
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE TABLE IF NOT EXISTS network_proxies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label VARCHAR(255) NOT NULL UNIQUE,
    endpoint_url TEXT NOT NULL,                     -- e.g., http://1.2.3.4:8080
    auth_type VARCHAR(32) NOT NULL DEFAULT 'none',  -- none | basic | token | custom
    credentials_encrypted JSONB,                    -- Fernet encrypted payload
    metadata JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_network_proxies_active
    ON network_proxies(is_active) WHERE is_active = TRUE;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: account_proxy_assignments
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE TABLE IF NOT EXISTS account_proxy_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    proxy_id UUID NOT NULL REFERENCES network_proxies(id) ON DELETE CASCADE,
    priority INTEGER DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'active',   -- active | standby | burned
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(account_id, proxy_id)
);

CREATE INDEX IF NOT EXISTS idx_account_proxy_assignments_account
    ON account_proxy_assignments(account_id);
CREATE INDEX IF NOT EXISTS idx_account_proxy_assignments_proxy
    ON account_proxy_assignments(proxy_id);
CREATE INDEX IF NOT EXISTS idx_account_proxy_assignments_status
    ON account_proxy_assignments(status) WHERE status = 'active';


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Triggers: Automatically maintain updated_at
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DROP TRIGGER IF EXISTS trg_network_proxies_updated_at ON network_proxies;
CREATE TRIGGER trg_network_proxies_updated_at
    BEFORE UPDATE ON network_proxies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_account_proxy_assignments_updated_at ON account_proxy_assignments;
CREATE TRIGGER trg_account_proxy_assignments_updated_at
    BEFORE UPDATE ON account_proxy_assignments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE network_proxies IS 'Catalog of network proxy endpoints with encrypted credentials';
COMMENT ON TABLE account_proxy_assignments IS 'Mapping of trading accounts to proxy endpoints for traffic egress';

COMMENT ON COLUMN network_proxies.endpoint_url IS 'Proxy endpoint in URI form (http://ip:port, socks5://host:port, etc.)';
COMMENT ON COLUMN network_proxies.credentials_encrypted IS 'Fernet-encrypted JSON (e.g., {"username": "...", "password": "..."})';
COMMENT ON COLUMN account_proxy_assignments.priority IS 'Lower priority value is preferred; used for rotation ordering';
COMMENT ON COLUMN account_proxy_assignments.status IS 'active proxies are eligible, burned proxies are skipped until restored';
