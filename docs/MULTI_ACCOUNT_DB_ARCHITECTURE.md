# Multi-Account Database Architecture Plan

Excellent question! This is a crucial architectural decision. Let me analyze your requirements and propose a sensible database design for multi-account support.

## 📊 Current Situation Analysis

**Current DB Structure:**
- `strategy_positions` table - stores positions without user association
- Acts as if there's only one global user
- No credential management

**Your Requirements:**
1. ✅ Multiple accounts running separate bots
2. ✅ Each account has DEX-specific credentials
3. ✅ Flexibility for account sharing (e.g., one Backpack account for multiple strategies)
4. ✅ Track which account opened which position
5. ✅ Secure credential management

## 🏗️ Proposed Database Architecture

### 1. **Core Tables Structure**

```sql
-- Main account/wallet entity
CREATE TABLE accounts (
    id UUID PRIMARY KEY,
    account_name VARCHAR(255) UNIQUE NOT NULL,  -- e.g., "main_bot", "test_account"
    wallet_address VARCHAR(255),  -- On-chain wallet if applicable
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB  -- Flexible storage for account-specific settings
);

-- DEX credentials per account
CREATE TABLE account_dex_credentials (
    id UUID PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    dex_id INTEGER REFERENCES dexes(id),
    
    -- Encrypted credentials
    api_key_encrypted TEXT,
    secret_key_encrypted TEXT,
    additional_credentials_encrypted JSONB,  -- For DEX-specific extras
    
    -- DEX-specific identifiers
    exchange_account_id VARCHAR(255),  -- Internal account ID on the exchange
    subaccount_index INTEGER,  -- For DEXes with subaccounts
    
    is_active BOOLEAN DEFAULT true,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(account_id, dex_id, subaccount_index)
);

-- Update strategy_positions to link to account
ALTER TABLE strategy_positions 
ADD COLUMN account_id UUID REFERENCES accounts(id);

-- For shared DEX accounts (your Backpack scenario)
CREATE TABLE account_dex_sharing (
    id UUID PRIMARY KEY,
    primary_account_id UUID REFERENCES accounts(id),
    shared_account_id UUID REFERENCES accounts(id),
    dex_id INTEGER REFERENCES dexes(id),
    sharing_type VARCHAR(50),  -- 'full', 'read_only', 'positions_only'
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 2. **Credential Management Strategy**

```python
# Option A: Environment Variables (Current)
BACKPACK_PUBLIC_KEY_ACCOUNT1=xxx
BACKPACK_SECRET_KEY_ACCOUNT1=xxx
LIGHTER_PRIVATE_KEY_ACCOUNT1=xxx

# Option B: Encrypted Database Storage (Recommended)
# Use encryption key from environment
CREDENTIAL_ENCRYPTION_KEY=xxx  # One master key

# Option C: External Secrets Manager
# AWS Secrets Manager, HashiCorp Vault, etc.
```

### 3. **Account Configuration File**

```yaml
# accounts/main_account.yaml
account_name: main_bot
description: Primary funding arbitrage account

dex_credentials:
  lighter:
    account_index: 0
    # Keys loaded from env or secrets manager
    
  backpack:
    # Shared account - references shared_bot
    use_shared_from: shared_bot
    
  aster:
    account_index: 5
    
position_limits:
  max_positions: 10
  max_exposure_usd: 100000
```

### 4. **Position Association Logic**

```python
class MultiAccountPositionManager:
    """
    Each bot instance runs with a specific account_id
    """
    
    def __init__(self, account_name: str):
        self.account = self._load_account(account_name)
        self.account_id = self.account.id
        
    async def create_position(self, position_data):
        # Automatically associates with current account
        position_data['account_id'] = self.account_id
        
    async def get_account_positions(self):
        # Only returns positions for this account
        return await db.fetch_all(
            "SELECT * FROM strategy_positions WHERE account_id = :account_id",
            {"account_id": self.account_id}
        )
```

## 🔐 Security Considerations

### **Credential Storage Options:**

**Option 1: Encrypted in Database** ✅ Recommended
- Pros: Centralized, easy to manage, supports dynamic accounts
- Cons: Need encryption key management
- Implementation: Use `cryptography` library with Fernet encryption

**Option 2: Environment Variables** 
- Pros: Simple, secure if server is secure
- Cons: Hard to manage multiple accounts, requires restart for changes

**Option 3: External Secrets Manager**
- Pros: Most secure, audit trail, rotation support
- Cons: Added complexity, external dependency

## 📋 Implementation Plan

### **Phase 1: Database Schema** (Week 1)
1. Create `accounts` table
2. Create `account_dex_credentials` table
3. Add `account_id` to `strategy_positions`
4. Create migration scripts

### **Phase 2: Credential Management** (Week 2)
1. Implement encryption/decryption utilities
2. Create credential loader that supports multiple sources
3. Update exchange client factories to use account-specific credentials

### **Phase 3: Multi-Account Logic** (Week 3)
1. Update `PositionManager` to be account-aware
2. Update `runbot.py` to accept `--account` parameter
3. Update monitoring/dashboard to filter by account

### **Phase 4: Account Sharing** (Week 4)
1. Implement Backpack account sharing logic
2. Test with multiple Lighter accounts → one Backpack account

## 🎯 Key Design Decisions

### **Q: How to handle shared DEX accounts?**
**A:** Use `account_dex_sharing` table. When account A needs Backpack access but doesn't have credentials, it can reference account B's Backpack credentials.

### **Q: How to run multiple bots?**
**A:** Each bot instance runs with:
```bash
python runbot.py --config config.yml --account main_bot
python runbot.py --config config.yml --account test_bot
```

### **Q: How to prevent position conflicts?**
**A:** Each position is tagged with `account_id`. Bots only see/manage their own positions.

## 🚀 Benefits of This Approach

1. **Scalability**: Add unlimited accounts without code changes
2. **Flexibility**: Share DEX accounts when needed (Backpack KYC issue)
3. **Security**: Centralized credential management with encryption
4. **Auditability**: Track which account did what
5. **Isolation**: Accounts can't interfere with each other

Does this architecture align with your vision? Should we prioritize certain aspects over others?

