# Testing Guide - Strategy Management via Telegram Bot

This guide walks you through setting up and testing the new strategy management features.

## Prerequisites Checklist

- [x] VPS with Ubuntu/Debian
- [x] PostgreSQL database running
- [x] Python 3.10+ installed
- [x] Virtual environment activated
- [x] `.env` file configured with database credentials

---

## Step 1: Install Python Dependencies

Install the new dependency (`psutil` for health monitoring):

```bash
# Activate your virtual environment
source venv/bin/activate  # or .venv/bin/activate

# Install new dependencies
pip install psutil>=5.9.0

# Or reinstall all dependencies to be safe
pip install -r requirements.txt
```

**Verify installation:**
```bash
python -c "import psutil; print('psutil OK')"
python -c "import xmlrpc.client; print('xmlrpc OK')"
python -c "import yaml; print('yaml OK')"
```

---

## Step 2: Install and Configure Supervisor

Supervisor is required for process management. Install it on your VPS:

```bash
# Install Supervisor
sudo apt update
sudo apt install -y supervisor

# Start and enable Supervisor
sudo systemctl enable supervisor
sudo systemctl start supervisor

# Verify Supervisor is running
sudo systemctl status supervisor
```

**Check Supervisor XML-RPC API:**
```bash
# Test if Supervisor XML-RPC is accessible (should return process list)
python3 -c "import xmlrpc.client; s = xmlrpc.client.ServerProxy('http://localhost:9001/RPC2'); print(s.supervisor.getAllProcessInfo())"
```

**⚠️ If you get "Connection refused" error:**

1. **Check if Supervisor is running:**
   ```bash
   sudo systemctl status supervisor
   # Should show "active (running)"
   ```

2. **If not running, start it:**
   ```bash
   sudo systemctl start supervisor
   sudo systemctl enable supervisor  # Enable on boot
   ```

3. **Check if XML-RPC interface is enabled:**
   ```bash
   # Check Supervisor config for XML-RPC settings
   sudo grep -A 10 "\[inet_http_server\]" /etc/supervisor/supervisord.conf
   ```

4. **If XML-RPC is not configured, add it:**
   ```bash
   # Edit Supervisor config
   sudo vim /etc/supervisor/supervisord.conf
   
   # Add or uncomment these lines (usually near the top):
   [inet_http_server]
   port=127.0.0.1:9001
   
   # Save and restart Supervisor
   sudo systemctl restart supervisor
   ```

5. **Verify Supervisor is listening on port 9001:**
   ```bash
   # Check if port 9001 is listening
   sudo netstat -tlnp | grep 9001
   # Or use ss:
   sudo ss -tlnp | grep 9001
   
   # Should show something like:
   # tcp  0  0  127.0.0.1:9001  0.0.0.0:*  LISTEN  12345/supervisord
   ```

6. **Check Supervisor logs for errors:**
   ```bash
   sudo tail -50 /var/log/supervisor/supervisord.log
   ```

7. **Test again:**
   ```bash
   python3 -c "import xmlrpc.client; s = xmlrpc.client.ServerProxy('http://127.0.0.1:9001/RPC2'); print(s.supervisor.getVersion())"
   # Should print Supervisor version number
   ```

**Configure Supervisor (if needed):**
- Default config: `/etc/supervisor/supervisord.conf`
- Config directory: `/etc/supervisor/conf.d/` (where our dynamic configs go)
- XML-RPC port: `9001` (default)

**Important:** Ensure your VPS user has sudo permissions to write to `/etc/supervisor/conf.d/` (or configure passwordless sudo for this specific directory).

---

## Step 3: Run Database Migrations

Run all 4 new migrations in order:

```bash
# Make sure you're in the project root
cd /path/to/perp-dex-tools

# Activate virtual environment
source venv/bin/activate

# Run migrations one by one
python database/scripts/run_migration.py database/migrations/011_add_strategy_configs.sql
python database/scripts/run_migration.py database/migrations/012_add_strategy_runs.sql
python database/scripts/run_migration.py database/migrations/013_add_safety_limits.sql
python database/scripts/run_migration.py database/migrations/014_add_audit_log.sql
```

**Verify migrations:**
```bash
# Connect to PostgreSQL and check tables exist
psql -U your_db_user -d your_db_name -c "\dt strategy_configs"
psql -U your_db_user -d your_db_name -c "\dt strategy_runs"
psql -U your_db_user -d your_db_name -c "\dt safety_limits"
psql -U your_db_user -d your_db_name -c "\dt audit_log"
```

---

## Step 4: Configure Environment Variables

Ensure your `.env` file has these variables (if not already set):

```bash
# Database (required)
DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# Telegram Bot (required)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Supervisor (optional - defaults shown)
SUPERVISOR_RPC_URL=http://127.0.0.1:9001/RPC2
SUPERVISOR_CONFIG_DIR=/etc/supervisor/conf.d
VPS_USER=your_vps_username

# Credential Encryption (required)
CREDENTIAL_ENCRYPTION_KEY=your_encryption_key_here
```

---

## Step 5: Create Users and API Keys (Whitelisting)

**Important:** Users must be whitelisted before they can use the bot. You (the admin) create users and API keys, then provide the API keys to users.

### 5.1 Create a User

```bash
# Activate virtual environment
source venv/bin/activate

# Create user (interactive mode)
python database/scripts/create_user.py

# Or command line mode
python database/scripts/create_user.py --username alice --email alice@example.com

# Create admin user (like "simba")
python database/scripts/create_user.py --username simba --email simba@example.com --admin
```

### 5.2 Create API Key for User

```bash
# Create API key (interactive mode)
python database/scripts/create_api_key.py

# Or command line mode
python database/scripts/create_api_key.py --username alice --name "Telegram Bot Key"

# The script will output the API key - SAVE THIS! It won't be shown again.
# Example output:
# API Key: perp_8585a9b87b0ebd546c99347979101304
```

**Important Notes:**
- The API key is shown **only once** - save it immediately
- Share the API key with the user securely
- Users authenticate via `/auth <api_key>` in Telegram
- After authentication, users can create accounts, configs, and run strategies

### 5.3 Verify User Setup

```bash
# Connect to database
psql -U your_db_user -d your_db_name

# Check users
SELECT id, username, email, is_admin, is_active FROM users;

# Check API keys
SELECT ak.id, ak.key_prefix, ak.name, ak.is_active, u.username 
FROM api_keys ak 
JOIN users u ON ak.user_id = u.id;
```

---

## Step 6: Start the Telegram Bot

Start the Telegram bot service:

```bash
# Activate virtual environment
source venv/bin/activate

# Run the bot
python telegram_bot_service/main.py
```

**Or run in a screen session (recommended for VPS):**
```bash
screen -S telegram_bot
source venv/bin/activate
python telegram_bot_service/main.py
# Press Ctrl+A then D to detach
```

**Check logs:**
- The bot should log: "ProcessManager initialized" with Supervisor details
- Look for: "Process recovery completed" on startup

---

## Step 7: Test Commands

**⚠️ Authentication Required:** All commands (except `/start`, `/help`, `/auth`) require authentication. Users must run `/auth <api_key>` first.

### 7.0 Authenticate User

**In Telegram:**
```
/auth perp_8585a9b87b0ebd546c99347979101304
```

The bot will respond:
- ✅ Successfully authenticated as `username`
- ❌ Invalid or expired API key (if key is wrong)

**After authentication**, users can use all commands below.

**Create an account:**
```
/quick_start
```
Follow the wizard:
1. Enter account name
2. Add exchange credentials (lighter, aster, backpack, paradex)
3. Verify credentials (should test API access)
4. Add proxy (optional, but required for running strategies)
5. Confirm creation

**List accounts:**
```
/list_accounts
```

**Add exchange to existing account:**
```
/add_exchange
```
Select account, then follow prompts for exchange credentials.

**Add proxy to account:**
```
/add_proxy
```
Select account, enter proxy URL (e.g., `socks5://host:port`), verify.

---

### 7.2 Test Config Management

**Create a config:**
```
/create_config
```
Choose:
- Option 1: Interactive wizard (one question at a time)
- Option 2: JSON/YAML input (for experienced users)

**List configs:**
```
/list_configs
```

**Edit config:**
```
/edit_config <config_name>
```

---

### 7.3 Test Strategy Execution

**Start a strategy:**
```
/run
```
Flow:
1. Select account (must have proxy unless admin)
2. Select config
3. Bot validates config, checks safety limits, resources
4. Spawns process via Supervisor
5. Returns run_id and status

**List running strategies:**
```
/list_strategies
```
Shows:
- Status (🟢 running, 🔴 error, ⚫ stopped, 🟡 starting)
- Account, config, uptime
- Last heartbeat

**View logs:**
```
/logs <run_id>
```
Sends log file as document.

**Stop strategy:**
```
/stop_strategy <run_id>
```
Or use inline keyboard from `/list_strategies`.

**Check limits:**
```
/limits
```
Shows:
- Strategies running
- Daily start limit
- Cooldown status
- Error rate

---

## Step 8: Verify Supervisor Integration

**Check Supervisor processes:**
```bash
# List all Supervisor programs
sudo supervisorctl status

# Should see programs like: strategy_abc12345 RUNNING pid 12345, uptime 0:05:23
```

**Check Supervisor logs:**
```bash
# View Supervisor main log
sudo tail -f /var/log/supervisor/supervisord.log

# View specific strategy log
tail -f logs/strategy_<run_id>.out.log
```

**Test Supervisor XML-RPC directly:**
```bash
python3 << EOF
import xmlrpc.client
s = xmlrpc.client.ServerProxy('http://localhost:9001/RPC2')
processes = s.supervisor.getAllProcessInfo()
for p in processes:
    if p['name'].startswith('strategy_'):
        print(f"{p['name']}: {p['statename']} (PID: {p.get('pid', 'N/A')})")
EOF
```

---

## Step 9: Test Process Recovery

**Simulate bot restart:**
1. Stop the Telegram bot (Ctrl+C or kill process)
2. Start it again
3. Check logs for: "Process recovery completed"
4. Verify strategies are still running (check `/list_strategies`)

**Test orphaned process cleanup:**
1. Manually create a Supervisor program (not in DB)
2. Restart bot
3. Bot should detect and stop orphaned process

---

## Step 10: Test Admin Proxy Bypass

**As admin user (simba):**
1. Create account without proxy
2. Try to run strategy
3. Should succeed (admin bypass)
4. Check logs: "Admin user: proxy disabled, running on VPS IP"

**As regular user:**
1. Create account without proxy
2. Try to run strategy
3. Should fail: "Account must have at least one active proxy configured"

---

## Troubleshooting

### Supervisor Issues

**Supervisor not starting:**
```bash
sudo systemctl status supervisor
sudo journalctl -u supervisor -n 50
```

**Permission denied writing config:**
```bash
# Check permissions
ls -la /etc/supervisor/conf.d/

# Fix permissions (if needed)
sudo chmod 755 /etc/supervisor/conf.d/
```

**XML-RPC connection refused:**

This usually means Supervisor is not running or XML-RPC is not enabled. Follow these steps:

1. **Check if Supervisor is running:**
   ```bash
   sudo systemctl status supervisor
   ```

2. **Check if XML-RPC is enabled in config:**
   ```bash
   sudo grep -A 5 "\[inet_http_server\]" /etc/supervisor/supervisord.conf
   ```

3. **If missing, add XML-RPC configuration:**
   ```bash
   sudo vim /etc/supervisor/supervisord.conf
   # Add these lines (usually after [unix_http_server] section):
   [inet_http_server]
   port=127.0.0.1:9001
   
   # Save and restart:
   sudo systemctl restart supervisor
   ```

4. **Verify port is listening:**
   ```bash
   sudo netstat -tlnp | grep 9001
   # Should show: tcp  0  0  127.0.0.1:9001  LISTEN
   ```

5. **Check Supervisor logs:**
   ```bash
   sudo tail -50 /var/log/supervisor/supervisord.log
   ```

6. **Test connection:**
   ```bash
   python3 -c "import xmlrpc.client; s = xmlrpc.client.ServerProxy('http://127.0.0.1:9001/RPC2'); print('Supervisor version:', s.supervisor.getVersion())"
   ```

### Database Issues

**Migration fails:**
```bash
# Check database connection
python -c "from database.connection import database; import asyncio; asyncio.run(database.connect())"

# Check if tables already exist
psql -U your_db_user -d your_db_name -c "\dt"
```

### Process Issues

**Strategy not starting:**
- Check Supervisor logs: `sudo tail -f /var/log/supervisor/supervisord.log`
- Check strategy logs: `tail -f logs/strategy_<run_id>.out.log`
- Check for port conflicts: `netstat -tlnp | grep 8766`

**Strategy crashes immediately:**
- Check config file: `/tmp/strategy_<run_id>.yml`
- Verify account credentials are correct
- Check proxy connectivity

### Telegram Bot Issues

**Bot not responding:**
- Check bot token is correct in `.env`
- Verify bot is running: `ps aux | grep main.py`
- Check logs for errors

**Commands not working:**
- Ensure you're authenticated: `/auth`
- Check user exists in database
- Verify database connection

---

## Expected Behavior

### Successful Strategy Start

1. User runs `/run`
2. Selects account and config
3. Bot validates:
   - ✅ Safety limits (daily limit, cooldown, error rate)
   - ✅ Resource availability (memory, ports)
   - ✅ Config validity (credentials, proxy)
4. Bot spawns process via Supervisor
5. Supervisor starts `runbot.py` with:
   - Unique config file (`/tmp/strategy_<run_id>.yml`)
   - Unique control API port (8766-8799)
   - Log files (`logs/strategy_<run_id>.out.log`)
6. Database record created in `strategy_runs`
7. User receives confirmation with run_id

### Process Recovery on Restart

1. Bot starts up
2. Calls `recover_processes()`
3. Queries database for "running" strategies
4. Queries Supervisor for actual running processes
5. Reconciles differences:
   - DB says running but Supervisor doesn't → Mark as stopped
   - Supervisor has process but DB doesn't → Stop orphaned process
   - Supervisor process in error state → Mark as error

---

## Next Steps

After successful testing:

1. **Monitor logs** for any errors
2. **Test edge cases**:
   - Multiple strategies per user (limit: 3)
   - Daily start limit (default: 10)
   - Cooldown period (default: 5 minutes)
   - Error rate limit (default: 50%)
3. **Test admin features** (if applicable)
4. **Monitor system resources** (CPU, RAM)
5. **Test process recovery** after VPS reboot

---

## Quick Reference

**Key Commands:**
- `/quick_start` - Create account
- `/create_config` - Create strategy config
- `/run` - Start strategy
- `/list_strategies` - View running strategies
- `/stop_strategy <run_id>` - Stop strategy
- `/logs <run_id>` - View logs
- `/limits` - Check usage limits

**Key Files:**
- Supervisor configs: `/etc/supervisor/conf.d/strategy_*.conf`
- Strategy logs: `logs/strategy_<run_id>.out.log`
- Temp configs: `/tmp/strategy_<run_id>.yml`
- Bot logs: Check console output or log file

**Key Database Tables:**
- `strategy_configs` - User configs and templates
- `strategy_runs` - Running strategies
- `safety_limits` - User safety limits
- `audit_log` - Action audit trail

