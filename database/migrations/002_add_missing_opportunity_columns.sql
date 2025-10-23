-- Migration: Add missing columns to opportunities table
-- Date: 2025-10-07
-- Description: Add oi_imbalance and avg_spread_bps columns that were missing

-- Add oi_imbalance if it doesn't exist
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'opportunities' 
        AND column_name = 'oi_imbalance'
    ) THEN
        ALTER TABLE opportunities ADD COLUMN oi_imbalance VARCHAR(20);
        RAISE NOTICE 'Added oi_imbalance column to opportunities';
    END IF;
END $$;

-- Add avg_spread_bps if it doesn't exist
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'opportunities' 
        AND column_name = 'avg_spread_bps'
    ) THEN
        ALTER TABLE opportunities ADD COLUMN avg_spread_bps INTEGER;
        RAISE NOTICE 'Added avg_spread_bps column to opportunities';
    END IF;
END $$;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed successfully!';
END $$;
