-- Migration 004: Add Strategy Management Tables
-- Adds tables for funding arbitrage strategy position tracking and state management

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: strategy_positions
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Stores open and closed trading positions for funding arbitrage strategies

CREATE TABLE IF NOT EXISTS strategy_positions (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    
    -- Position details
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    long_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    short_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    size_usd DECIMAL(20, 8) NOT NULL,
    
    -- Entry data
    entry_long_rate DECIMAL(20, 8) NOT NULL,
    entry_short_rate DECIMAL(20, 8) NOT NULL,
    entry_divergence DECIMAL(20, 8) NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    
    -- Current state
    current_divergence DECIMAL(20, 8),
    last_check TIMESTAMP,
    
    -- Status tracking
    status VARCHAR(20) NOT NULL DEFAULT 'open', -- 'open', 'pending_close', 'closed'
    rebalance_pending BOOLEAN DEFAULT FALSE,
    rebalance_reason VARCHAR(50),
    
    -- Exit data
    exit_reason VARCHAR(50),
    closed_at TIMESTAMP,
    pnl_usd DECIMAL(20, 8),
    
    -- Cumulative funding tracking
    cumulative_funding_usd DECIMAL(20, 8) DEFAULT 0,
    funding_payments_count INTEGER DEFAULT 0,
    
    -- Metadata
    metadata JSONB,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for strategy_positions
CREATE INDEX IF NOT EXISTS idx_positions_strategy_status ON strategy_positions(strategy_name, status);
CREATE INDEX IF NOT EXISTS idx_positions_opened_at ON strategy_positions(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON strategy_positions(symbol_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON strategy_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_rebalance ON strategy_positions(rebalance_pending) WHERE rebalance_pending = TRUE;


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: funding_payments
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Tracks individual funding payments for each position

CREATE TABLE IF NOT EXISTS funding_payments (
    id SERIAL PRIMARY KEY,
    position_id UUID NOT NULL REFERENCES strategy_positions(id) ON DELETE CASCADE,
    
    -- Payment details
    payment_time TIMESTAMP NOT NULL,
    long_payment DECIMAL(20, 8) NOT NULL,  -- Amount paid/received on long side (negative = paid)
    short_payment DECIMAL(20, 8) NOT NULL, -- Amount paid/received on short side (positive = received)
    net_payment DECIMAL(20, 8) NOT NULL,   -- Net profit from this payment
    
    -- Rates at time of payment
    long_rate DECIMAL(20, 8),
    short_rate DECIMAL(20, 8),
    divergence DECIMAL(20, 8),
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for funding_payments
CREATE INDEX IF NOT EXISTS idx_funding_payments_position ON funding_payments(position_id, payment_time DESC);
CREATE INDEX IF NOT EXISTS idx_funding_payments_time ON funding_payments(payment_time DESC);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: fund_transfers
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Tracks cross-DEX fund transfer operations

CREATE TABLE IF NOT EXISTS fund_transfers (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES strategy_positions(id),
    
    -- Transfer details
    from_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    to_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    amount_usd DECIMAL(20, 8) NOT NULL,
    reason VARCHAR(50) NOT NULL,
    
    -- Status tracking
    status VARCHAR(20) NOT NULL, -- 'pending', 'withdrawing', 'bridging', 'depositing', 'completed', 'failed'
    withdrawal_tx VARCHAR(100),
    bridge_tx VARCHAR(100),
    deposit_tx VARCHAR(100),
    
    -- Error handling
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    
    -- Metadata
    metadata JSONB
);

-- Indexes for fund_transfers
CREATE INDEX IF NOT EXISTS idx_transfers_status ON fund_transfers(status);
CREATE INDEX IF NOT EXISTS idx_transfers_position ON fund_transfers(position_id);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON fund_transfers(created_at DESC);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: strategy_state
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Stores arbitrary strategy state for persistence and recovery

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy_name VARCHAR(50) PRIMARY KEY,
    state_data JSONB NOT NULL,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- Index for strategy_state
CREATE INDEX IF NOT EXISTS idx_strategy_state_updated ON strategy_state(last_updated DESC);


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Trigger: Auto-update updated_at timestamp
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to strategy_positions
DROP TRIGGER IF EXISTS update_strategy_positions_updated_at ON strategy_positions;
CREATE TRIGGER update_strategy_positions_updated_at
    BEFORE UPDATE ON strategy_positions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to strategy_state
DROP TRIGGER IF EXISTS update_strategy_state_updated_at ON strategy_state;
CREATE TRIGGER update_strategy_state_updated_at
    BEFORE UPDATE ON strategy_state
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Comments for documentation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMENT ON TABLE strategy_positions IS 'Tracks funding arbitrage positions (open and closed)';
COMMENT ON TABLE funding_payments IS 'Records individual funding payments for each position';
COMMENT ON TABLE fund_transfers IS 'Tracks cross-DEX fund transfer operations';
COMMENT ON TABLE strategy_state IS 'Stores strategy state for persistence and recovery';

COMMENT ON COLUMN strategy_positions.cumulative_funding_usd IS 'Sum of all net funding payments received';
COMMENT ON COLUMN strategy_positions.rebalance_pending IS 'Flag for positions pending rebalancing';
COMMENT ON COLUMN funding_payments.net_payment IS 'short_payment - long_payment (profit for this interval)';
COMMENT ON COLUMN fund_transfers.status IS 'pending, withdrawing, bridging, depositing, completed, failed';

