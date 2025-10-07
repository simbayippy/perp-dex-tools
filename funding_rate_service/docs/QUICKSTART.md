# Quick Start - Your Next Steps

You're ready to get the funding rate service running! Here's exactly what to do.

---

## ✅ What's Already Done

1. Project structure created
2. Core files set up (main.py, config.py, etc.)
3. Database schema created
4. Initialization scripts ready
5. Documentation complete

---

## 🚀 Next Steps

### Step 1: Set Up VPS (5-10 minutes)

SSH into your VPS and run these commands:

```bash
# Install PostgreSQL + TimescaleDB
sudo apt update && sudo apt upgrade -y

# PostgreSQL 15
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt update
sudo apt install -y postgresql-15 postgresql-contrib-15

# TimescaleDB
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -
sudo apt update
sudo apt install -y timescaledb-2-postgresql-15
sudo timescaledb-tune --quiet --yes
sudo systemctl restart postgresql

# Redis
sudo apt install -y redis-server
sudo systemctl restart redis-server

# Python 3.11
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
curl -sS https://bootstrap.pypa.io/get-pip.py | sudo python3.11
```

### Step 2: Create Database (2 minutes)

```bash
# On VPS
sudo -i -u postgres psql << 'EOF'
CREATE DATABASE funding_rates;
CREATE USER funding_user WITH PASSWORD 'CHANGE_THIS_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE funding_rates TO funding_user;
\c funding_rates
CREATE EXTENSION IF NOT EXISTS timescaledb;
\q
EOF
exit
```

**⚠️ IMPORTANT**: Replace `CHANGE_THIS_PASSWORD` with a strong password and save it!

### Step 3: Local Development Setup (3 minutes)

On your local machine:

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
cp .env.example .env
nano .env
```

Edit `.env` and set:
```bash
# If connecting to VPS database from local:
DATABASE_URL=postgresql://funding_user:YOUR_PASSWORD@YOUR_VPS_IP:5432/funding_rates

# Or if you have PostgreSQL locally:
DATABASE_URL=postgresql://funding_user:YOUR_PASSWORD@localhost:5432/funding_rates
```

### Step 4: Initialize Database (1 minute)

```bash
# Make sure venv is activated
source venv/bin/activate

# Run init script
python scripts/init_db.py

# Seed DEX data
python scripts/seed_dexes.py
```

You should see:
```
✓ Database schema initialized successfully!
✓ Added DEX: Lighter Network
✓ Added DEX: EdgeX
...
```

### Step 5: Test Run (1 minute)

```bash
# Start the service
python main.py
```

You should see:
```
INFO:     Starting Funding Rate Service...
INFO:     Database connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Step 6: Test API

Open browser or use curl:
```bash
# Basic health check
curl http://localhost:8000/

# Health endpoint
curl http://localhost:8000/health

# API docs (open in browser)
open http://localhost:8000/docs
```
