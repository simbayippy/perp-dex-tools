-- ============================================================================
-- Dashboard tables for terminal UI snapshots and events
-- ============================================================================

CREATE TABLE IF NOT EXISTS dashboard_sessions (
    session_id UUID PRIMARY KEY,
    strategy VARCHAR(64) NOT NULL,
    config_path TEXT,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    health VARCHAR(16) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dashboard_snapshots (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES dashboard_sessions(session_id) ON DELETE CASCADE,
    generated_at TIMESTAMP NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dashboard_snapshots_session_time
    ON dashboard_snapshots (session_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_events (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES dashboard_sessions(session_id) ON DELETE CASCADE,
    ts TIMESTAMP NOT NULL,
    category VARCHAR(16) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_session_time
    ON dashboard_events (session_id, ts DESC);
