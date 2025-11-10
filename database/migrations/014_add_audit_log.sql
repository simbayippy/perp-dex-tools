-- ============================================================================
-- Migration 014: Add Audit Log Table
-- ============================================================================
-- Adds table for compliance and debugging audit logging.
--
-- Key Features:
-- - audit_log: Tracks important user actions
-- - Action types: start_strategy, stop_strategy, create_account, etc.
-- - JSONB details field for flexible action-specific data
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: audit_log
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Audit log for compliance and debugging

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for audit_log
CREATE INDEX IF NOT EXISTS idx_audit_log_user_created ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id) WHERE user_id IS NOT NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE audit_log IS 'Audit log for compliance and debugging';
COMMENT ON COLUMN audit_log.user_id IS 'User who performed the action. NULL for system actions';
COMMENT ON COLUMN audit_log.action IS 'Action type (e.g., start_strategy, stop_strategy, create_account)';
COMMENT ON COLUMN audit_log.details IS 'Action-specific details stored as JSONB';

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 014 completed successfully!';
END $$;

