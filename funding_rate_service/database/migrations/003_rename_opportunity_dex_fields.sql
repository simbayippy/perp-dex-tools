-- Migration: Rename opportunity fields for better clarity
-- Date: 2025-10-07
-- Description: Rename volume/OI/spread fields to clarify they refer to DEXs
--              e.g., long_volume_24h -> long_dex_volume_24h

-- Rename volume fields
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'long_volume_24h'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN long_volume_24h TO long_dex_volume_24h;
        RAISE NOTICE 'Renamed long_volume_24h to long_dex_volume_24h';
    END IF;
END $$;

DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'short_volume_24h'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN short_volume_24h TO short_dex_volume_24h;
        RAISE NOTICE 'Renamed short_volume_24h to short_dex_volume_24h';
    END IF;
END $$;

-- Rename OI fields
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'long_oi_usd'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN long_oi_usd TO long_dex_oi_usd;
        RAISE NOTICE 'Renamed long_oi_usd to long_dex_oi_usd';
    END IF;
END $$;

DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'short_oi_usd'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN short_oi_usd TO short_dex_oi_usd;
        RAISE NOTICE 'Renamed short_oi_usd to short_dex_oi_usd';
    END IF;
END $$;

-- Rename spread fields
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'long_spread_bps'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN long_spread_bps TO long_dex_spread_bps;
        RAISE NOTICE 'Renamed long_spread_bps to long_dex_spread_bps';
    END IF;
END $$;

DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'opportunities' AND column_name = 'short_spread_bps'
    ) THEN
        ALTER TABLE opportunities RENAME COLUMN short_spread_bps TO short_dex_spread_bps;
        RAISE NOTICE 'Renamed short_spread_bps to short_dex_spread_bps';
    END IF;
END $$;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed successfully! All DEX-related fields now have _dex_ prefix for clarity.';
END $$;
