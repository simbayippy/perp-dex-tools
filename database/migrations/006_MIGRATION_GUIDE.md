# Migration 006: Multi-Account Support - Guide

## 📋 Overview

This migration adds support for multiple trading accounts with isolated credentials, positions, and optional account sharing.

**Created Tables:**
- `accounts` - Core account/wallet entity
- `account_exchange_credentials` - Encrypted exchange credentials per account
- `account_exchange_sharing` - Cross-account credential sharing

**Modified Tables:**
- `strategy_positions` - Added `account_id` column

---

## 🚀 Quick Start

### 1. Run the Migration

```bash
# Option A: Run just this migration
cd database/migrations
./006_run_migration.sh

# Option B: Run all migrations including this one
./RUN_ALL_MIGRATIONS.sh

# Option C: Run directly
cd ../..
python database/scripts/run_migration.py database/migrations/006_add_multi_account_support.sql
```

### 2. Create Your First Account

```sql
INSERT INTO accounts (account_name, description, is_active, metadata)
VALUES (
    'main_bot',
    'Primary funding arbitrage account',
    TRUE,
    '{"max_positions": 10, "max_exposure_usd": 100000}'::jsonb
);
```

### 3. Verify the Migration

```sql
-- Check accounts table exists
SELECT * FROM accounts;

-- Check credential table exists
SELECT table_name FROM information_schema.tables 
WHERE table_name LIKE 'account%';

-- Verify account_id column was added to positions
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'strategy_positions' AND column_name = 'account_id';
```

---

## 🔄 Rollback (if needed)

```bash
python database/scripts/run_migration.py database/migrations/006_add_multi_account_support_rollback.sql
```

⚠️ **WARNING**: Rollback will:
- Delete all accounts and credentials
- Remove account_id from positions
- Unlink all positions from accounts

---

## 📊 Database Schema

### accounts Table
```sql
CREATE TABLE accounts (
    id UUID PRIMARY KEY,
    account_name VARCHAR(255) UNIQUE NOT NULL,
    wallet_address VARCHAR(255),
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    metadata JSONB  -- For position limits, settings, etc.
);
```

### account_exchange_credentials Table
```sql
CREATE TABLE account_exchange_credentials (
    id UUID PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    exchange_id INTEGER REFERENCES dexes(id),
    
    -- Encrypted credentials
    api_key_encrypted TEXT,
    secret_key_encrypted TEXT,
    additional_credentials_encrypted JSONB,
    
    -- Exchange identifiers
    exchange_account_id VARCHAR(255),
    subaccount_index INTEGER,
    
    is_active BOOLEAN DEFAULT TRUE,
    last_used TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    
    UNIQUE(account_id, exchange_id, subaccount_index)
);
```

### account_exchange_sharing Table
```sql
CREATE TABLE account_exchange_sharing (
    id UUID PRIMARY KEY,
    primary_account_id UUID REFERENCES accounts(id),
    shared_account_id UUID REFERENCES accounts(id),
    exchange_id INTEGER REFERENCES dexes(id),
    sharing_type VARCHAR(50) DEFAULT 'full',  -- 'full', 'read_only', 'positions_only'
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    
    CHECK (primary_account_id != shared_account_id),
    UNIQUE(primary_account_id, exchange_id)
);
```

---

## 🔒 Credential Management (Phase 2)

**TODO**: The migration creates tables for encrypted credentials, but you'll need to:

1. **Implement encryption utilities** (see Phase 2 of architecture doc)
2. **Set up master encryption key** in environment:
   ```bash
   export CREDENTIAL_ENCRYPTION_KEY="your-fernet-key-here"
   ```
3. **Create credential loader** that:
   - Decrypts credentials from database
   - Falls back to environment variables for migration period
   - Supports credential sharing logic

---

## 📝 Usage Examples

### Example 1: Create Account with Metadata
```sql
INSERT INTO accounts (account_name, description, metadata)
VALUES (
    'test_bot',
    'Testing account with limited exposure',
    '{
        "max_positions": 5,
        "max_exposure_usd": 10000,
        "risk_level": "low",
        "allowed_exchanges": ["lighter", "aster"]
    }'::jsonb
);
```

### Example 2: Add Exchange Credentials (Placeholder)
```sql
-- NOTE: In production, use encrypted values via Python encryption utilities
INSERT INTO account_exchange_credentials (
    account_id,
    exchange_id,
    api_key_encrypted,
    secret_key_encrypted,
    subaccount_index
)
VALUES (
    (SELECT id FROM accounts WHERE account_name = 'main_bot'),
    (SELECT id FROM dexes WHERE name = 'lighter'),
    'ENCRYPTED_API_KEY_HERE',
    'ENCRYPTED_SECRET_KEY_HERE',
    0
);
```

### Example 3: Set Up Account Sharing (Backpack KYC Scenario)
```sql
-- Create shared Backpack account
INSERT INTO accounts (account_name, description)
VALUES ('shared_backpack', 'Shared Backpack account for KYC compliance');

-- Add Backpack credentials to shared account
INSERT INTO account_exchange_credentials (account_id, exchange_id, api_key_encrypted, secret_key_encrypted)
VALUES (
    (SELECT id FROM accounts WHERE account_name = 'shared_backpack'),
    (SELECT id FROM dexes WHERE name = 'backpack'),
    'ENCRYPTED_BACKPACK_API_KEY',
    'ENCRYPTED_BACKPACK_SECRET'
);

-- Allow main_bot to use shared Backpack account
INSERT INTO account_exchange_sharing (primary_account_id, shared_account_id, exchange_id, sharing_type)
VALUES (
    (SELECT id FROM accounts WHERE account_name = 'main_bot'),
    (SELECT id FROM accounts WHERE account_name = 'shared_backpack'),
    (SELECT id FROM dexes WHERE name = 'backpack'),
    'full'
);
```

### Example 4: Link Existing Positions to Account
```sql
-- Migrate legacy positions to default account
UPDATE strategy_positions 
SET account_id = (SELECT id FROM accounts WHERE account_name = 'default')
WHERE account_id IS NULL;
```

### Example 5: Query Positions by Account
```sql
-- Get all open positions for a specific account
SELECT 
    p.id,
    s.symbol,
    d1.name as long_exchange,
    d2.name as short_exchange,
    p.size_usd,
    p.entry_divergence,
    p.cumulative_funding_usd,
    p.status
FROM strategy_positions p
JOIN accounts a ON p.account_id = a.id
JOIN symbols s ON p.symbol_id = s.id
JOIN dexes d1 ON p.long_dex_id = d1.id
JOIN dexes d2 ON p.short_dex_id = d2.id
WHERE a.account_name = 'main_bot'
  AND p.status = 'open';
```

---

## 🧪 Testing Checklist

- [ ] Migration runs without errors
- [ ] All 3 new tables created successfully
- [ ] `strategy_positions.account_id` column exists
- [ ] All indexes created properly
- [ ] Triggers are working (updated_at auto-updates)
- [ ] Can create test account
- [ ] Foreign key constraints work (try deleting account → cascades properly)
- [ ] UNIQUE constraints work (try duplicate account_name → fails)
- [ ] CHECK constraint works (try sharing account with itself → fails)
- [ ] Rollback migration works (destructive - test in dev only!)

---

## 🎯 Next Steps (Phases 2-4)

### Phase 2: Credential Management
- [ ] Implement Fernet encryption utilities (`database/encryption.py`)
- [ ] Create credential loader (`database/credential_loader.py`)
- [ ] Add CLI tool to encrypt and store credentials
- [ ] Update exchange client factory to use database credentials

### Phase 3: Multi-Account Logic
- [ ] Update `FundingArbPositionManager.__init__()` to accept `account_id`
- [ ] Modify all CRUD operations to filter by `account_id`
- [ ] Update `runbot.py` to accept `--account` CLI parameter
- [ ] Update dashboard/TUI to show account name
- [ ] Add account filtering to monitoring views

### Phase 4: Account Sharing
- [ ] Implement credential sharing resolver
- [ ] Test multiple Lighter accounts → one Backpack account
- [ ] Add validation for sharing permissions
- [ ] Create CLI tools for managing account sharing

---

## 🐛 Troubleshooting

### Migration Fails with "relation already exists"
The migration uses `IF NOT EXISTS` clauses, so it's safe to re-run. Check if tables were partially created:
```sql
SELECT table_name FROM information_schema.tables 
WHERE table_name IN ('accounts', 'account_exchange_credentials', 'account_exchange_sharing');
```

### Foreign Key Violation on Rollback
Make sure to run the rollback migration, not drop tables manually. The rollback handles cascading deletes properly.

### Can't Insert Credentials
Remember: credentials should be encrypted first using Phase 2 utilities. Don't store plaintext credentials in the database.

---

## 📚 References

- Architecture doc: `/docs/MULTI_ACCOUNT_DB_ARCHITECTURE.md`
- Original migration: `006_add_multi_account_support.sql`
- Rollback migration: `006_add_multi_account_support_rollback.sql`

