# Database Scripts

This directory contains database management and utility scripts.

## 📋 Available Scripts

### Core Scripts

#### `run_migration.py`
Run a single database migration file.

**Usage:**
```bash
python database/scripts/run_migration.py database/migrations/006_add_multi_account_support.sql
```

**Features:**
- Connects to database via `database.connection`
- Executes raw SQL with support for multiple statements
- Handles PostgreSQL-specific features (NOTICE, etc.)

---

#### `run_all_migrations.py`
Run all pending migrations using the migration manager.

**Usage:**
```bash
python database/scripts/run_all_migrations.py
```

**Features:**
- Uses `database.migration_manager` to track which migrations have run
- Automatically runs migrations in order
- Skips already-applied migrations

---

#### `init_db.py`
Initialize the database with the base schema.

**Usage:**
```bash
python database/scripts/init_db.py
```

**Features:**
- Creates all tables from `database/schema.sql`
- Idempotent - safe to run multiple times (uses `IF NOT EXISTS`)
- Lists all created tables after completion

---

#### `seed_dexes.py`
Seed the `dexes` table with initial exchange data.

**Usage:**
```bash
python database/scripts/seed_dexes.py
```

**Exchanges Included:**
- Lighter Network (0% fees)
- EdgeX
- Paradex
- GRVT (maker rebates!)
- Hyperliquid
- Backpack
- Aster

**Features:**
- Checks for existing DEXes before inserting (won't duplicate)
- Displays all registered DEXes with fee information
- Safe to run multiple times

---

### Account Management Scripts

#### `add_account.py`
Add a new trading account with encrypted credentials to the database.

**Usage:**
```bash
# Interactive mode (recommended)
python database/scripts/add_account.py --interactive

# From .env file
python database/scripts/add_account.py --from-env --account-name main_bot
```

**Features:**
- Reads credentials from `.env` file (Lighter, Aster, Backpack)
- Encrypts credentials using Fernet encryption
- Stores encrypted credentials in database
- Supports multiple exchanges per account
- Idempotent - safe to run multiple times (updates existing credentials)

**First Time Setup:**
```bash
# 1. Generate encryption key (only needed once)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Add to .env file
echo "CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here" >> .env

# 3. Add your account
python database/scripts/add_account.py --from-env --account-name main_bot
```

---

#### `list_accounts.py`
List all trading accounts and their configured exchanges.

**Usage:**
```bash
# List all accounts (credentials hidden)
python database/scripts/list_accounts.py

# Show decrypted credentials (use carefully!)
python database/scripts/list_accounts.py --show-credentials
```

**Features:**
- Shows account details, metadata, and configured exchanges
- Can decrypt and display credentials (with proper key)
- Shows credential sharing relationships
- Lists last usage timestamps

---

## 🚀 Typical Setup Workflow

### First Time Setup

```bash
# 1. Install required dependencies
pip install cryptography  # For credential encryption

# 2. Create .env file with DATABASE_URL
cp env_example.txt .env
# Edit .env with your database credentials and exchange API keys

# 3. Initialize database schema
python database/scripts/init_db.py

# 4. Seed initial DEX data
python database/scripts/seed_dexes.py

# 5. Run all migrations
python database/scripts/run_all_migrations.py

# 6. Generate encryption key and add to .env
python -c "from cryptography.fernet import Fernet; print('CREDENTIAL_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
# Copy the output and add to your .env file

# 7. Add your trading account
python database/scripts/add_account.py --from-env --account-name main_bot

# 8. Verify account was created
python database/scripts/list_accounts.py
```

### Adding New Migrations

```bash
# 1. Create migration file in database/migrations/
# Example: 007_add_new_feature.sql

# 2. Run specific migration
python database/scripts/run_migration.py database/migrations/007_add_new_feature.sql

# Or run all pending migrations
python database/scripts/run_all_migrations.py
```

---

## 🔧 Configuration

All scripts use the database connection from `database/connection.py`, which reads from:

**Environment Variables:**
```bash
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
```

Or from `funding_rate_service.config.settings` if available.

---

## 📂 Related Directories

- `/database/migrations/` - SQL migration files
- `/database/repositories/` - Data access layer
- `/database/schema.sql` - Base database schema
- `/database/connection.py` - Database connection manager

---

## 🐛 Troubleshooting

### "Module not found" errors
Make sure you're running scripts from the project root:
```bash
cd /path/to/perp-dex-tools
python database/scripts/init_db.py
```

### "Database connection failed"
Check your `.env` file has the correct `DATABASE_URL`:
```bash
DATABASE_URL=postgresql://user:password@host:port/database
```

### "Permission denied" on scripts
Make scripts executable (optional):
```bash
chmod +x database/scripts/*.py
```

Then you can run directly:
```bash
./database/scripts/seed_dexes.py
```

