-- ============================================================================
-- Migration 018: Add Liquidation Risk Notification Type
-- ============================================================================
-- Adds 'liquidation_risk' to the allowed notification types in 
-- strategy_notifications table.
--
-- This allows the system to send notifications when there's liquidation risk
-- detected before opening a position (pre-flight check).
-- ============================================================================

-- Drop the existing check constraint and recreate it with the new value
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    -- Find the existing constraint on notification_type
    -- PostgreSQL stores IN (...) as = ANY ((ARRAY[...])::text[]), so we search for both patterns
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'strategy_notifications'::regclass
    AND contype = 'c'
    AND (
        pg_get_constraintdef(oid) LIKE '%notification_type%'
        AND (
            pg_get_constraintdef(oid) LIKE '%position_opened%'
            OR pg_get_constraintdef(oid) LIKE '%position_closed%'
            OR pg_get_constraintdef(oid) LIKE '%insufficient_margin%'
        )
    )
    LIMIT 1;
    
    -- Drop the existing constraint if it exists
    IF constraint_name IS NOT NULL THEN
        EXECUTE 'ALTER TABLE strategy_notifications DROP CONSTRAINT ' || quote_ident(constraint_name);
        RAISE NOTICE 'Dropped existing notification_type check constraint: %', constraint_name;
    END IF;
    
    -- Add the new constraint with all four notification types
    -- Check if constraint already exists to avoid errors
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'strategy_notifications'::regclass
        AND conname = 'strategy_notifications_notification_type_check'
    ) THEN
        ALTER TABLE strategy_notifications 
        ADD CONSTRAINT strategy_notifications_notification_type_check 
        CHECK (notification_type IN ('position_opened', 'position_closed', 'insufficient_margin', 'liquidation_risk'));
        
        RAISE NOTICE 'Added new notification_type check constraint with liquidation_risk';
    ELSE
        RAISE NOTICE 'Constraint strategy_notifications_notification_type_check already exists';
    END IF;
END $$;

-- Update the comment to reflect the new notification type
COMMENT ON COLUMN strategy_notifications.notification_type IS 'Type of notification: position_opened, position_closed, insufficient_margin, liquidation_risk';

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 018 completed successfully!';
END $$;

