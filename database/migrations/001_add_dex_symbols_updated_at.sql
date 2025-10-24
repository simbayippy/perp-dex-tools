-- Migration: Add updated_at column to dex_symbols table
-- Date: 2025-10-07
-- Description: Rename last_updated to updated_at for consistency with other tables

-- Rename column if it exists with old name
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'dex_symbols' 
        AND column_name = 'last_updated'
    ) THEN
        ALTER TABLE dex_symbols RENAME COLUMN last_updated TO updated_at;
        RAISE NOTICE 'Renamed dex_symbols.last_updated to updated_at';
    END IF;
END $$;

-- Add updated_at if it doesn't exist at all
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'dex_symbols' 
        AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE dex_symbols ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        RAISE NOTICE 'Added updated_at column to dex_symbols';
    END IF;
END $$;

-- Add created_at if it doesn't exist
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'dex_symbols' 
        AND column_name = 'created_at'
    ) THEN
        ALTER TABLE dex_symbols ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        RAISE NOTICE 'Added created_at column to dex_symbols';
    END IF;
END $$;

-- Add index on updated_at for the queries we use
CREATE INDEX IF NOT EXISTS idx_dex_symbols_updated_at ON dex_symbols(updated_at DESC);

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed successfully!';
END $$;
