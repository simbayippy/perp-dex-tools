# Multi-Account Setup Guide

Quick guide to set up multi-account support with Lighter, Aster, and Backpack.

## 🚀 Quick Start (5 minutes)

### Step 1: Run the Migration

```bash
# Make script executable
chmod +x database/migrations/006_run_migration.sh

# Run migration
./database/migrations/006_run_migration.sh
```

**Expected output:**
```
✅ Migration 006 applied successfully!
```

---

### Step 2: Generate Encryption Key

```bash
# Generate a new encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Copy the output** (looks like: `jXK7vZ9xT...`) and add to your `.env` file:

```bash
# Add this line to your .env file
CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here
```

⚠️ **IMPORTANT**: Keep this key safe! Without it, you can't decrypt your credentials.

---

### Step 3: Add Your Account

Your `.env` already has credentials for Lighter, Aster, and Backpack. Run:

```bash
python database/scripts/add_account.py --from-env --account-name main_bot
```

**What it does:**
- Creates an account called `main_bot`
- Reads credentials from your `.env` file
- Encrypts and stores them in the database
- Configures all 3 exchanges (Lighter, Aster, Backpack)

**Expected output:**
```
✅ Created account 'main_bot' (id: ...)
✅ Added credentials for lighter (account_id: ...)
✅ Added credentials for aster (account_id: ...)
✅ Added credentials for backpack (account_id: ...)
```

---

### Step 4: Verify Setup

```bash
python database/scripts/list_accounts.py
```

**You should see:**
```
📋 Trading Accounts
==================================================
1. main_bot (✅ Active)
   ID: [uuid]
   Description: Account from .env: main_bot
   
   🔐 Configured Exchanges (3):
      ✅ Lighter Network (lighter)
      ✅ Aster (aster)
      ✅ Backpack (backpack)
```

---

## 🎯 Your Current .env Credentials

Based on your `.env` file, here's what will be stored:

### Lighter
```
✅ API_KEY_PRIVATE_KEY (encrypted)
✅ LIGHTER_ACCOUNT_INDEX: 213803
✅ LIGHTER_API_KEY_INDEX: 2
```

### Aster
```
✅ ASTER_API_KEY (encrypted)
✅ ASTER_SECRET_KEY (encrypted)
```

### Backpack
```
✅ BACKPACK_PUBLIC_KEY (encrypted)
✅ BACKPACK_SECRET_KEY (encrypted)
```

---

## 📋 What Got Created in the Database

### `accounts` table
| Field | Value |
|-------|-------|
| account_name | `main_bot` |
| description | Account from .env: main_bot |
| is_active | `true` |

### `account_exchange_credentials` table (3 rows)
| account | exchange | credentials |
|---------|----------|-------------|
| main_bot | lighter | [encrypted] |
| main_bot | aster | [encrypted] |
| main_bot | backpack | [encrypted] |

---

## 🔐 Security Features

1. **Encryption at Rest**: All credentials encrypted with Fernet (symmetric encryption)
2. **Master Key**: Single `CREDENTIAL_ENCRYPTION_KEY` in `.env`
3. **No Plaintext**: Original credentials only in `.env`, database has encrypted versions
4. **Key Rotation**: Can re-encrypt with new key if needed

---

## 🛠️ Common Tasks

### Add Another Account

```bash
# Edit .env with new credentials for account2
# Then run:
python database/scripts/add_account.py --from-env --account-name account2
```

### Update Existing Credentials

```bash
# Update .env with new credentials
# Then run same command (it will update):
python database/scripts/add_account.py --from-env --account-name main_bot
```

### View Credentials (Decrypted)

```bash
# ⚠️  Use carefully! This shows decrypted credentials
python database/scripts/list_accounts.py --show-credentials
```

---

## 🧪 Test Your Setup

```sql
-- Connect to your database
psql $DATABASE_URL

-- Check accounts
SELECT account_name, is_active, created_at FROM accounts;

-- Check credentials (count)
SELECT 
    a.account_name,
    d.name as exchange,
    aec.is_active
FROM account_exchange_credentials aec
JOIN accounts a ON aec.account_id = a.id
JOIN dexes d ON aec.exchange_id = d.id
ORDER BY a.account_name, d.name;
```

---

## ❓ Troubleshooting

### "Module 'cryptography' not found"
```bash
pip install cryptography
```

### "CREDENTIAL_ENCRYPTION_KEY not found"
Make sure you added it to `.env`:
```bash
echo "CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here" >> .env
```

### "Exchange 'lighter' not found in database"
Run the seed script:
```bash
python database/scripts/seed_dexes.py
```

### "Account already exists"
This is normal - the script updates existing accounts. Use `--show-credentials` to verify.

---

## 🎉 Next Steps

Now that accounts are set up, you can:

1. **Phase 3**: Update `FundingArbPositionManager` to use accounts
2. **Phase 3**: Add `--account` parameter to `runbot.py`
3. **Phase 4**: Set up account sharing (if needed)

See `/docs/MULTI_ACCOUNT_DB_ARCHITECTURE.md` for the full roadmap.

---

## 📚 Related Files

- **Architecture**: `/docs/MULTI_ACCOUNT_DB_ARCHITECTURE.md`
- **Migration**: `/database/migrations/006_add_multi_account_support.sql`
- **Scripts**: `/database/scripts/README.md`

