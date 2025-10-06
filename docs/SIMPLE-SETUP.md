# Simple Setup Guide - Internal Service Only

This guide is for running the funding rate service **internally** (VPS + local machine) without Docker or external access.

---

## Part 1: VPS Setup (One-Time)

Your VPS will run PostgreSQL and Redis. The service will connect to these from both VPS and your local machine.

### Step 1: Install PostgreSQL + TimescaleDB

```bash
# SSH into your VPS
ssh your-vps

# Update system
sudo apt update && sudo apt upgrade -y

# Install PostgreSQL 15
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt update
sudo apt install -y postgresql-15 postgresql-contrib-15

# Install TimescaleDB
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -
sudo apt update
sudo apt install -y timescaledb-2-postgresql-15

# Tune PostgreSQL for TimescaleDB
sudo timescaledb-tune --quiet --yes

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Step 2: Create Database and User

```bash
# Switch to postgres user
sudo -i -u postgres

# Create database
psql << 'EOF'
-- Create database
CREATE DATABASE funding_rates;

-- Create user (change password!)
CREATE USER funding_user WITH PASSWORD 'your_secure_password_here';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE funding_rates TO funding_user;

-- Connect and enable TimescaleDB
\c funding_rates
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Exit
\q
EOF

# Exit postgres user
exit
```

**Important**: Replace `your_secure_password_here` with a strong password and save it!

### Step 3: Configure PostgreSQL for Internal Access

```bash
# Edit postgresql.conf
sudo nano /etc/postgresql/15/main/postgresql.conf

# Find and change (if you want to access from local machine):
# listen_addresses = 'localhost'
# TO:
# listen_addresses = '*'
# (Or keep 'localhost' if only running on VPS)

# Edit pg_hba.conf (only if accessing from local machine)
sudo nano /etc/postgresql/15/main/pg_hba.conf

# Add this line at the end (replace YOUR_LOCAL_IP):
# host    funding_rates    funding_user    YOUR_LOCAL_IP/32    scram-sha-256

# Restart PostgreSQL
sudo systemctl restart postgresql
```

**Option 1**: Keep `localhost` only if running service **only on VPS**
**Option 2**: Use `*` and add your local IP if you want to **develop locally** and connect to VPS database

### Step 4: Install Redis

```bash
# Install Redis
sudo apt install -y redis-server

# Edit config for production use
sudo nano /etc/redis/redis.conf

# Find and set:
# maxmemory 256mb
# maxmemory-policy allkeys-lru

# Restart Redis
sudo systemctl restart redis-server

# Verify
redis-cli ping
# Should return: PONG
```

### Step 5: Install Python 3.11

```bash
# Add PPA
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Install pip
curl -sS https://bootstrap.pypa.io/get-pip.py | sudo python3.11

# Verify
python3.11 --version
```

### Step 6: Create Service Directory

```bash
# Create directory
sudo mkdir -p /opt/funding-rate-service
sudo chown $USER:$USER /opt/funding-rate-service

# Create logs directory
sudo mkdir -p /var/log/funding-rate-service
sudo chown $USER:$USER /var/log/funding-rate-service
```

---

## Part 2: Local Development Setup

### Step 1: Set Up Project

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Create virtual environment
python3.11 -m venv venv

# Activate
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 2: Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env
nano .env
```

**For Local Development (connecting to VPS database):**
```bash
# Database (connecting to VPS)
DATABASE_URL=postgresql://funding_user:your_password@YOUR_VPS_IP:5432/funding_rates

# Redis (connecting to VPS)
REDIS_URL=redis://YOUR_VPS_IP:6379/0
USE_REDIS=true

# Service (local)
SERVICE_PORT=8000
SERVICE_HOST=127.0.0.1
LOG_LEVEL=INFO
ENVIRONMENT=development

# DEX APIs - will fill these in later
LIGHTER_API_URL=https://api.lighter.xyz
EDGEX_API_URL=https://api.edgex.exchange
PARADEX_API_URL=https://api.paradex.trade
GRVT_API_URL=https://api.grvt.io
HYPERLIQUID_API_URL=https://api.hyperliquid.xyz
```

**OR for VPS-only deployment:**
```bash
# Database (localhost on VPS)
DATABASE_URL=postgresql://funding_user:your_password@localhost:5432/funding_rates

# Redis (localhost on VPS)
REDIS_URL=redis://localhost:6379/0
USE_REDIS=true

# Service (VPS)
SERVICE_PORT=8000
SERVICE_HOST=127.0.0.1
LOG_LEVEL=INFO
ENVIRONMENT=production
```

### Step 3: Test Basic Connection

```bash
# Activate venv
source venv/bin/activate

# Test connection
python -c "
import asyncio
from databases import Database

async def test():
    db = Database('postgresql://funding_user:your_password@YOUR_VPS_IP:5432/funding_rates')
    await db.connect()
    result = await db.fetch_one('SELECT version()')
    print('Database connected:', result[0])
    await db.disconnect()

asyncio.run(test())
"
```

---

## Part 3: Initialize Database Schema

We need to create the database schema. First, let's create the schema file:

### Step 1: Create Schema File (on local machine)

The schema file is already in the design doc. I'll create it for you in the next step.

### Step 2: Run Schema

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Create scripts directory
mkdir -p scripts

# Run the init script (will create next)
python scripts/init_db.py
```

---

## Part 4: Development Workflow

### Working Locally, Deploying to VPS

#### Local Development:
```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Activate venv
source venv/bin/activate

# Run service
python main.py

# Access at http://localhost:8000
```

#### Deploy to VPS:
```bash
# On local machine: commit and push
git add .
git commit -m "Update funding rate service"
git push origin main

# On VPS: pull and restart
ssh your-vps
cd /opt/funding-rate-service
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
# Kill old process and restart (we'll set up systemd later)
```

---

## Part 5: VPS Deployment (Production)

### Step 1: Clone Repository on VPS

```bash
# SSH into VPS
ssh your-vps

cd /opt/funding-rate-service

# Clone (or pull if already cloned)
git clone YOUR_REPO_URL .
# OR
git pull origin main

# Create venv
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Environment on VPS

```bash
cd /opt/funding-rate-service

# Create .env
nano .env
```

Paste:
```bash
DATABASE_URL=postgresql://funding_user:your_password@localhost:5432/funding_rates
REDIS_URL=redis://localhost:6379/0
USE_REDIS=true
SERVICE_PORT=8000
SERVICE_HOST=127.0.0.1
LOG_LEVEL=INFO
ENVIRONMENT=production

# DEX APIs
LIGHTER_API_URL=https://api.lighter.xyz
EDGEX_API_URL=https://api.edgex.exchange
PARADEX_API_URL=https://api.paradex.trade
GRVT_API_URL=https://api.grvt.io
HYPERLIQUID_API_URL=https://api.hyperliquid.xyz

COLLECTION_INTERVAL_SECONDS=60
MAX_CONCURRENT_COLLECTIONS=10
COLLECTION_TIMEOUT_SECONDS=30
CACHE_TTL_SECONDS=60
CACHE_MAX_SIZE_MB=100
```

### Step 3: Initialize Database

```bash
cd /opt/funding-rate-service
source venv/bin/activate
python scripts/init_db.py
```

### Step 4: Run Service Manually (for testing)

```bash
source venv/bin/activate
python main.py

# In another SSH session, test:
curl http://localhost:8000/
curl http://localhost:8000/health
```

### Step 5: Set Up Systemd Service (Run on Boot)

```bash
# Create service file
sudo nano /etc/systemd/system/funding-rate-service.service
```

Paste:
```ini
[Unit]
Description=Funding Rate Service
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/opt/funding-rate-service
Environment="PATH=/opt/funding-rate-service/venv/bin"
EnvironmentFile=/opt/funding-rate-service/.env
ExecStart=/opt/funding-rate-service/venv/bin/python main.py

Restart=always
RestartSec=10

StandardOutput=append:/var/log/funding-rate-service/service.log
StandardError=append:/var/log/funding-rate-service/error.log

[Install]
WantedBy=multi-user.target
```

**Replace `YOUR_USERNAME`** with your actual username!

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable funding-rate-service

# Start service
sudo systemctl start funding-rate-service

# Check status
sudo systemctl status funding-rate-service

# View logs
sudo journalctl -u funding-rate-service -f
```

---

## Quick Reference

### VPS Commands

```bash
# Service management
sudo systemctl start funding-rate-service
sudo systemctl stop funding-rate-service
sudo systemctl restart funding-rate-service
sudo systemctl status funding-rate-service

# View logs
sudo journalctl -u funding-rate-service -f
tail -f /var/log/funding-rate-service/service.log

# Database
psql -U funding_user -d funding_rates

# Redis
redis-cli
redis-cli FLUSHALL  # Clear cache
```

### Local Development

```bash
# Activate venv
source venv/bin/activate

# Run service
python main.py

# Run tests
pytest

# Format code
black .
```

### Git Workflow

```bash
# Local
git add .
git commit -m "message"
git push

# VPS
cd /opt/funding-rate-service
git pull
source venv/bin/activate
pip install -r requirements.txt  # if requirements changed
sudo systemctl restart funding-rate-service
```

---

## Next Steps

1. ✅ VPS configured (PostgreSQL, Redis, Python)
2. ⏭️ Create database schema
3. ⏭️ Implement data models
4. ⏭️ Implement DEX adapters
5. ⏭️ Implement API endpoints
6. ⏭️ Deploy to VPS

See next section for implementation steps.

