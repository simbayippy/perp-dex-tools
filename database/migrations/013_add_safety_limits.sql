-- ============================================================================
-- Migration 013: Add Safety Limits Table
-- ============================================================================
-- Adds table to enforce user safety constraints and rate limiting.
--
-- Key Features:
-- - safety_limits: Per-user safety limits
-- - Daily start limits, cooldown periods, error rate thresholds
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: safety_limits
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Enforces user safety constraints and rate limiting

CREATE TABLE IF NOT EXISTS safety_limits (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    daily_start_limit INTEGER NOT NULL DEFAULT 10,
    max_error_rate FLOAT NOT NULL DEFAULT 0.5 CHECK (max_error_rate >= 0 AND max_error_rate <= 1),
    cooldown_minutes INTEGER NOT NULL DEFAULT 5 CHECK (cooldown_minutes >= 0),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Triggers: Automatically maintain updated_at
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DROP TRIGGER IF EXISTS trg_safety_limits_updated_at ON safety_limits;
CREATE TRIGGER trg_safety_limits_updated_at
    BEFORE UPDATE ON safety_limits
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE safety_limits IS 'Per-user safety constraints and rate limiting';
COMMENT ON COLUMN safety_limits.daily_start_limit IS 'Maximum number of strategy starts per day';
COMMENT ON COLUMN safety_limits.max_error_rate IS 'Maximum error rate (0.0 to 1.0). If user''s error rate exceeds this, new starts are blocked';
COMMENT ON COLUMN safety_limits.cooldown_minutes IS 'Minimum minutes to wait between strategy starts';

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 013 completed successfully!';
END $$;

