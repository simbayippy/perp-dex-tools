-- ============================================================================
-- Migration 016: Add Trade Fills Table
-- ============================================================================
-- Adds table to store aggregated trade history (entry and exit trades)
-- for queryable trade history and analytics.
--
-- Key Features:
-- - trade_fills: Stores aggregated trade data (one row per order)
-- - Links to positions, accounts, DEXes, and symbols
-- - Supports both entry and exit trades
-- - Enables Telegram bot trade history queries
-- ============================================================================

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Table: trade_fills
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Stores aggregated trade/fill data for positions (one row per order)

CREATE TABLE IF NOT EXISTS trade_fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Position and account context
    position_id UUID NOT NULL REFERENCES strategy_positions(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    trade_type VARCHAR(10) NOT NULL CHECK (trade_type IN ('entry', 'exit')),
    
    -- Exchange and symbol
    dex_id INTEGER NOT NULL REFERENCES dexes(id) ON DELETE CASCADE,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    
    -- Order identification
    order_id VARCHAR(255) NOT NULL,  -- Order that generated this fill
    trade_id VARCHAR(255),  -- Exchange-specific trade ID (for uniqueness check)
    
    -- Aggregated trade details (from all fills for this order)
    timestamp TIMESTAMP NOT NULL,  -- First fill timestamp
    side VARCHAR(10) NOT NULL CHECK (side IN ('buy', 'sell')),
    total_quantity DECIMAL(20, 8) NOT NULL,  -- Sum of all fills
    weighted_avg_price DECIMAL(20, 8) NOT NULL,  -- Weighted average price
    total_fee DECIMAL(20, 8) NOT NULL,  -- Sum of all fees
    fee_currency VARCHAR(10) NOT NULL,
    
    -- PnL (if available from exchange - Paradex provides this)
    realized_pnl DECIMAL(20, 8),
    realized_funding DECIMAL(20, 8),
    
    -- Metadata
    fill_count INTEGER DEFAULT 1,  -- Number of fills aggregated
    
    -- Audit
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- One row per order_id per position
    UNIQUE(position_id, order_id)
);

-- Indexes for trade_fills
CREATE INDEX IF NOT EXISTS idx_trade_fills_position ON trade_fills(position_id, trade_type);
CREATE INDEX IF NOT EXISTS idx_trade_fills_account ON trade_fills(account_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trade_fills_order ON trade_fills(order_id);
CREATE INDEX IF NOT EXISTS idx_trade_fills_timestamp ON trade_fills(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trade_fills_dex_symbol_time ON trade_fills(dex_id, symbol_id, timestamp DESC);

-- Comments for documentation
COMMENT ON TABLE trade_fills IS 'Stores aggregated trade/fill data for positions (one row per order)';
COMMENT ON COLUMN trade_fills.trade_type IS 'Type of trade: entry (opening) or exit (closing)';
COMMENT ON COLUMN trade_fills.order_id IS 'Order ID that generated this fill (may have multiple fills)';
COMMENT ON COLUMN trade_fills.total_quantity IS 'Sum of all quantities from fills for this order';
COMMENT ON COLUMN trade_fills.weighted_avg_price IS 'Weighted average price: sum(price * quantity) / sum(quantity)';
COMMENT ON COLUMN trade_fills.total_fee IS 'Sum of all fees from fills for this order';
COMMENT ON COLUMN trade_fills.fill_count IS 'Number of fills aggregated into this row';
COMMENT ON COLUMN trade_fills.realized_pnl IS 'Realized PnL if exchange provides it (e.g., Paradex)';
COMMENT ON COLUMN trade_fills.realized_funding IS 'Realized funding if exchange provides it (e.g., Paradex)';

