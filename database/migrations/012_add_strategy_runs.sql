-- ============================================================================
-- Migration 012: Add Strategy Runs Table
-- ============================================================================
-- Adds table to track running strategies managed by Supervisor.
--
-- Key Features:
-- - strategy_runs: Tracks all strategy executions
-- - Links to users, accounts, and configs
-- - Health monitoring fields (heartbeat, health_status, error_count)
-- - Supervisor program name tracking
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: strategy_runs
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Tracks running strategies and their health status

CREATE TABLE IF NOT EXISTS strategy_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    config_id UUID NOT NULL REFERENCES strategy_configs(id) ON DELETE RESTRICT,
    supervisor_program_name VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'starting' CHECK (status IN ('starting', 'running', 'stopped', 'error', 'paused')),
    control_api_port INTEGER NOT NULL,
    log_file_path TEXT,
    last_heartbeat TIMESTAMP,
    health_status VARCHAR(20) DEFAULT 'unknown' CHECK (health_status IN ('unknown', 'healthy', 'degraded', 'unhealthy')),
    error_count INTEGER DEFAULT 0,
    last_error TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    stopped_at TIMESTAMP,
    error_message TEXT
);

-- Indexes for strategy_runs
CREATE INDEX IF NOT EXISTS idx_strategy_runs_user_status ON strategy_runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_user ON strategy_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_account ON strategy_runs(account_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_config ON strategy_runs(config_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_status ON strategy_runs(status);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_heartbeat ON strategy_runs(last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_health ON strategy_runs(health_status);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_supervisor_name ON strategy_runs(supervisor_program_name);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_started ON strategy_runs(started_at DESC);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE strategy_runs IS 'Tracks running strategies managed by Supervisor';
COMMENT ON COLUMN strategy_runs.supervisor_program_name IS 'Supervisor program name (e.g., strategy_abc123)';
COMMENT ON COLUMN strategy_runs.status IS 'Current status: starting, running, stopped, error, paused';
COMMENT ON COLUMN strategy_runs.control_api_port IS 'Port for this strategy''s control API';
COMMENT ON COLUMN strategy_runs.log_file_path IS 'Path to Supervisor stdout log file';
COMMENT ON COLUMN strategy_runs.last_heartbeat IS 'Last health check timestamp';
COMMENT ON COLUMN strategy_runs.health_status IS 'Health status: unknown, healthy, degraded, unhealthy';
COMMENT ON COLUMN strategy_runs.error_count IS 'Number of errors encountered';
COMMENT ON COLUMN strategy_runs.last_error IS 'Most recent error message';

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 012 completed successfully!';
END $$;

