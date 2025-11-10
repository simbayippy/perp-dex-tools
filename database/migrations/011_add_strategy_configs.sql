-- ============================================================================
-- Migration 011: Add Strategy Configs Table
-- ============================================================================
-- Adds table to store user-created strategy configurations and public templates.
--
-- Key Features:
-- - strategy_configs: User configs and public templates
-- - Supports both user-specific configs and public templates (is_template flag)
-- - Resource priority for future resource management
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: strategy_configs
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Stores user-created strategy configurations and public templates

CREATE TABLE IF NOT EXISTS strategy_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    config_name VARCHAR(255) NOT NULL,
    strategy_type VARCHAR(50) NOT NULL CHECK (strategy_type IN ('funding_arbitrage', 'grid')),
    config_data JSONB NOT NULL,
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    resource_priority VARCHAR(10) DEFAULT 'normal' CHECK (resource_priority IN ('low', 'normal', 'high')),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for strategy_configs
CREATE INDEX IF NOT EXISTS idx_strategy_configs_user ON strategy_configs(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_configs_user_active ON strategy_configs(user_id, is_active) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_strategy_configs_template ON strategy_configs(is_template) WHERE is_template = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_configs_type ON strategy_configs(strategy_type);
CREATE INDEX IF NOT EXISTS idx_strategy_configs_user_name ON strategy_configs(user_id, config_name) WHERE user_id IS NOT NULL;

-- Partial unique index: per-user uniqueness for config names (non-templates)
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_configs_user_name_unique 
ON strategy_configs(user_id, config_name) 
WHERE user_id IS NOT NULL AND is_template = FALSE;

-- Partial unique index: global uniqueness for template names
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_configs_template_name_unique 
ON strategy_configs(config_name) 
WHERE is_template = TRUE;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Triggers: Automatically maintain updated_at
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DROP TRIGGER IF EXISTS trg_strategy_configs_updated_at ON strategy_configs;
CREATE TRIGGER trg_strategy_configs_updated_at
    BEFORE UPDATE ON strategy_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE strategy_configs IS 'User-created strategy configurations and public templates';
COMMENT ON COLUMN strategy_configs.user_id IS 'User who owns this config. NULL for public templates (is_template=TRUE)';
COMMENT ON COLUMN strategy_configs.config_name IS 'User-friendly name for the configuration';
COMMENT ON COLUMN strategy_configs.strategy_type IS 'Type of strategy: funding_arbitrage or grid';
COMMENT ON COLUMN strategy_configs.config_data IS 'Full config YAML stored as JSONB';
COMMENT ON COLUMN strategy_configs.is_template IS 'If TRUE, this is a public template available to all users';
COMMENT ON COLUMN strategy_configs.resource_priority IS 'Resource priority: low, normal, or high (for future resource management)';

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 011 completed successfully!';
END $$;

