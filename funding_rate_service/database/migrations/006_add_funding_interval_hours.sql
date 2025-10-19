-- Migration: Add funding_interval_hours to dexes and dex_symbols tables
-- Date: 2025-10-19
-- Description: Add funding_interval_hours columns to normalize funding rates across exchanges
--
--              HYBRID APPROACH:
--              1. Exchange-level interval (dexes.funding_interval_hours): Default interval for the exchange
--                 - Lighter: 1 hour
--                 - Most others: 8 hours
--
--              2. Symbol-level interval (dex_symbols.funding_interval_hours): Overrides exchange default
--                 - Used by exchanges like Aster where different symbols have different intervals
--                 - Example: Aster's INJUSDT has 8h, but ZORAUSDT has 4h
--
--              3. Normalization priority: symbol-level > exchange-level > 8h default

-- ============================================================================
-- PART 1: Add exchange-level funding intervals (dexes table)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'dexes'
        AND column_name = 'funding_interval_hours'
    ) THEN
        ALTER TABLE dexes ADD COLUMN funding_interval_hours INTEGER NOT NULL DEFAULT 8;
        RAISE NOTICE 'Added funding_interval_hours column to dexes table';
    END IF;
END $$;

-- Update existing DEXes with their correct funding intervals
UPDATE dexes SET funding_interval_hours = 1 WHERE name = 'lighter';
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'grvt';
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'edgex';
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'paradex';
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'backpack';
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'aster';  -- Default, but symbols may vary
UPDATE dexes SET funding_interval_hours = 8 WHERE name = 'hyperliquid';

-- Add comment to the column
COMMENT ON COLUMN dexes.funding_interval_hours IS 'Default funding payment interval in hours for this exchange (e.g., 1 for Lighter, 8 for most others). Can be overridden per symbol in dex_symbols table.';

-- Add index for queries that filter by interval
CREATE INDEX IF NOT EXISTS idx_dexes_funding_interval ON dexes(funding_interval_hours);

-- ============================================================================
-- PART 2: Add symbol-level funding intervals (dex_symbols table)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'dex_symbols'
        AND column_name = 'funding_interval_hours'
    ) THEN
        ALTER TABLE dex_symbols ADD COLUMN funding_interval_hours INTEGER NULL;
        RAISE NOTICE 'Added funding_interval_hours column to dex_symbols table';
    END IF;
END $$;

-- Add comment to the column
COMMENT ON COLUMN dex_symbols.funding_interval_hours IS 'Symbol-specific funding interval in hours. If NULL, uses exchange default from dexes table. Used by exchanges like Aster where intervals vary per symbol (e.g., INJUSDT=8h, ZORAUSDT=4h).';

-- Add index for queries that need symbol-level intervals
CREATE INDEX IF NOT EXISTS idx_dex_symbols_funding_interval ON dex_symbols(funding_interval_hours) WHERE funding_interval_hours IS NOT NULL;

-- ============================================================================
-- PART 3: Create a view for easy interval lookup
-- ============================================================================

CREATE OR REPLACE VIEW v_symbol_funding_intervals AS
SELECT
    ds.id as dex_symbol_id,
    d.name as dex_name,
    s.symbol,
    COALESCE(ds.funding_interval_hours, d.funding_interval_hours, 8) as effective_interval_hours,
    ds.funding_interval_hours as symbol_specific_interval,
    d.funding_interval_hours as exchange_default_interval
FROM dex_symbols ds
JOIN dexes d ON ds.dex_id = d.id
JOIN symbols s ON ds.symbol_id = s.id
WHERE d.is_active = TRUE AND ds.is_active = TRUE;

COMMENT ON VIEW v_symbol_funding_intervals IS 'Provides effective funding interval for each symbol-DEX pair. Uses symbol-specific interval if available, otherwise falls back to exchange default, then 8h.';

-- ============================================================================
-- Success messages
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 006 completed successfully!';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';
    RAISE NOTICE 'Added funding interval support:';
    RAISE NOTICE '  1. Exchange-level: dexes.funding_interval_hours';
    RAISE NOTICE '     - Lighter: 1h';
    RAISE NOTICE '     - Others: 8h (default)';
    RAISE NOTICE '';
    RAISE NOTICE '  2. Symbol-level: dex_symbols.funding_interval_hours';
    RAISE NOTICE '     - NULL = use exchange default';
    RAISE NOTICE '     - Non-NULL = override for specific symbols';
    RAISE NOTICE '';
    RAISE NOTICE '  3. View: v_symbol_funding_intervals';
    RAISE NOTICE '     - Easy lookup of effective interval per symbol';
    RAISE NOTICE '';
    RAISE NOTICE 'IMPORTANT: Adapters should normalize rates to 8h standard!';
    RAISE NOTICE '========================================';
END $$;
