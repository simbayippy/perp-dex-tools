-- ============================================================================
-- Migration 015: Add Strategy Notifications Table
-- ============================================================================
-- Adds table to queue notifications from strategies to Telegram users.
--
-- Key Features:
-- - strategy_notifications: Queue for position open/close notifications
-- - Links to strategy_runs and users
-- - Supports different notification types
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: strategy_notifications
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Queue for notifications from strategies to Telegram users

CREATE TABLE IF NOT EXISTS strategy_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notification_type VARCHAR(50) NOT NULL CHECK (notification_type IN ('position_opened', 'position_closed')),
    symbol VARCHAR(50),
    message TEXT NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    sent BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for strategy_notifications
CREATE INDEX IF NOT EXISTS idx_strategy_notifications_user_sent ON strategy_notifications(user_id, sent);
CREATE INDEX IF NOT EXISTS idx_strategy_notifications_run_id ON strategy_notifications(strategy_run_id);
CREATE INDEX IF NOT EXISTS idx_strategy_notifications_created ON strategy_notifications(created_at DESC);

-- Comments for documentation
COMMENT ON TABLE strategy_notifications IS 'Queue for notifications from strategies to Telegram users';
COMMENT ON COLUMN strategy_notifications.notification_type IS 'Type of notification: position_opened, position_closed';
COMMENT ON COLUMN strategy_notifications.symbol IS 'Trading symbol (e.g., BTC, ETH)';
COMMENT ON COLUMN strategy_notifications.message IS 'Human-readable notification message';
COMMENT ON COLUMN strategy_notifications.details IS 'Additional details (JSON): reason, pnl, size_usd, etc.';
COMMENT ON COLUMN strategy_notifications.sent IS 'Whether notification has been sent to Telegram';

