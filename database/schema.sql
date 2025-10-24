-- ============================================================================
-- Funding Rate Service Database Schema
-- ============================================================================

-- Table: dexes
CREATE TABLE IF NOT EXISTS dexes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    api_base_url VARCHAR(255),
    websocket_url VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    supports_websocket BOOLEAN DEFAULT FALSE,
    
    -- Fee structure
    maker_fee_percent NUMERIC(10, 6) NOT NULL,
    taker_fee_percent NUMERIC(10, 6) NOT NULL,
    has_fee_tiers BOOLEAN DEFAULT FALSE,
    fee_metadata JSONB,
    
    -- Operational metadata
    collection_interval_seconds INTEGER DEFAULT 60,
    rate_limit_per_minute INTEGER DEFAULT 60,
    last_successful_fetch TIMESTAMP,
    last_error TIMESTAMP,
    consecutive_errors INTEGER DEFAULT 0,
    
    -- Audit fields
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dexes_name ON dexes(name);
CREATE INDEX IF NOT EXISTS idx_dexes_is_active ON dexes(is_active);
CREATE INDEX IF NOT EXISTS idx_dexes_id_name ON dexes(id, name);

-- Table: symbols
CREATE TABLE IF NOT EXISTS symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    display_name VARCHAR(50),
    category VARCHAR(20),
    
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_symbols_symbol ON symbols(symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(is_active);
CREATE INDEX IF NOT EXISTS idx_symbols_id_symbol ON symbols(id, symbol);

-- Table: dex_symbols
CREATE TABLE IF NOT EXISTS dex_symbols (
    id SERIAL PRIMARY KEY,
    dex_id INTEGER NOT NULL REFERENCES dexes(id) ON DELETE CASCADE,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    
    dex_symbol_format VARCHAR(50) NOT NULL,
    
    is_active BOOLEAN DEFAULT TRUE,
    min_order_size NUMERIC(20, 8),
    max_order_size NUMERIC(20, 8),
    tick_size NUMERIC(20, 8),
    
    volume_24h NUMERIC(20, 2),
    volume_24h_base NUMERIC(20, 8),
    
    open_interest_usd NUMERIC(20, 2),
    open_interest_base NUMERIC(20, 8),
    
    best_bid NUMERIC(20, 8),
    best_ask NUMERIC(20, 8),
    spread_bps INTEGER,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(dex_id, symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_dex_symbols_dex ON dex_symbols(dex_id);
CREATE INDEX IF NOT EXISTS idx_dex_symbols_symbol ON dex_symbols(symbol_id);
CREATE INDEX IF NOT EXISTS idx_dex_symbols_active ON dex_symbols(is_active);
CREATE INDEX IF NOT EXISTS idx_dex_symbols_volume ON dex_symbols(volume_24h DESC);
CREATE INDEX IF NOT EXISTS idx_dex_symbols_oi ON dex_symbols(open_interest_usd DESC);

-- Table: funding_rates (TimescaleDB hypertable)
CREATE TABLE IF NOT EXISTS funding_rates (
    time TIMESTAMP NOT NULL,
    dex_id INTEGER NOT NULL REFERENCES dexes(id),
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    
    funding_rate NUMERIC(15, 10) NOT NULL,
    
    next_funding_time TIMESTAMP,
    predicted_rate NUMERIC(15, 10),
    index_price NUMERIC(20, 8),
    mark_price NUMERIC(20, 8),
    
    open_interest_usd NUMERIC(20, 2),
    volume_24h NUMERIC(20, 2),
    
    collection_latency_ms INTEGER,
    
    PRIMARY KEY (time, dex_id, symbol_id)
);

-- Convert to TimescaleDB hypertable (only if TimescaleDB is enabled)
SELECT create_hypertable('funding_rates', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_funding_rates_dex_symbol_time ON funding_rates(dex_id, symbol_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol_time ON funding_rates(symbol_id, time DESC);

-- Table: opportunities
CREATE TABLE IF NOT EXISTS opportunities (
    id SERIAL PRIMARY KEY,
    
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    long_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    short_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    
    long_rate NUMERIC(15, 10) NOT NULL,
    short_rate NUMERIC(15, 10) NOT NULL,
    divergence NUMERIC(15, 10) NOT NULL,
    
    estimated_fees NUMERIC(15, 10) NOT NULL,
    net_profit_percent NUMERIC(15, 10) NOT NULL,
    annualized_apy NUMERIC(10, 4),
    
    long_dex_volume_24h NUMERIC(20, 2),
    short_dex_volume_24h NUMERIC(20, 2),
    min_volume_24h NUMERIC(20, 2),
    
    long_dex_oi_usd NUMERIC(20, 2),
    short_dex_oi_usd NUMERIC(20, 2),
    min_oi_usd NUMERIC(20, 2),
    max_oi_usd NUMERIC(20, 2),
    oi_ratio NUMERIC(10, 4),
    oi_imbalance VARCHAR(20),
    
    long_dex_spread_bps INTEGER,
    short_dex_spread_bps INTEGER,
    avg_spread_bps INTEGER,
    
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP,
    
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_opportunities_symbol ON opportunities(symbol_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_profit ON opportunities(net_profit_percent DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_discovered ON opportunities(discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_long_dex ON opportunities(long_dex_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_short_dex ON opportunities(short_dex_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_min_oi ON opportunities(min_oi_usd ASC);
CREATE INDEX IF NOT EXISTS idx_opportunities_max_oi ON opportunities(max_oi_usd DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_composite ON opportunities(net_profit_percent DESC, min_oi_usd ASC);

-- Table: latest_funding_rates (for fast API responses)
CREATE TABLE IF NOT EXISTS latest_funding_rates (
    dex_id INTEGER NOT NULL REFERENCES dexes(id),
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    
    funding_rate NUMERIC(15, 10) NOT NULL,
    next_funding_time TIMESTAMP,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (dex_id, symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_latest_rates_dex ON latest_funding_rates(dex_id);
CREATE INDEX IF NOT EXISTS idx_latest_rates_symbol ON latest_funding_rates(symbol_id);

-- Table: collection_logs
CREATE TABLE IF NOT EXISTS collection_logs (
    id SERIAL PRIMARY KEY,
    dex_id INTEGER REFERENCES dexes(id),
    
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    
    symbols_fetched INTEGER DEFAULT 0,
    symbols_failed INTEGER DEFAULT 0,
    
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_collection_logs_dex_time ON collection_logs(dex_id, started_at DESC);

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Database schema created successfully!';
END $$;

