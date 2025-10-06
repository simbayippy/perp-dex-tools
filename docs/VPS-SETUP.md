# VPS Setup Guide - Funding Rate Service

## Prerequisites
- Ubuntu 22.04 or 24.04 VPS
- Root or sudo access
- At least 4GB RAM, 2 CPU cores, 50GB storage

---

## Step 1: Update System

```bash
# Update package lists
sudo apt update && sudo apt upgrade -y

# Install basic tools
sudo apt install -y curl wget git build-essential software-properties-common
```

---

## Step 2: Install PostgreSQL 15

```bash
# Add PostgreSQL repository
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -

# Update and install PostgreSQL 15
sudo apt update
sudo apt install -y postgresql-15 postgresql-contrib-15

# Check PostgreSQL is running
sudo systemctl status postgresql
```

---

## Step 3: Install TimescaleDB Extension

```bash
# Add TimescaleDB repository
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -

# Install TimescaleDB
sudo apt update
sudo apt install -y timescaledb-2-postgresql-15

# Configure TimescaleDB (this will tune PostgreSQL config)
sudo timescaledb-tune --quiet --yes

# Restart PostgreSQL
sudo systemctl restart postgresql
```

---

## Step 4: Configure PostgreSQL Database

```bash
# Switch to postgres user
sudo -i -u postgres

# Create database and user
psql << EOF
-- Create database
CREATE DATABASE funding_rates;

-- Create user with password
CREATE USER funding_user WITH PASSWORD 'CHANGE_THIS_PASSWORD';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE funding_rates TO funding_user;

-- Connect to the database
\c funding_rates

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Exit
\q
EOF

# Exit postgres user
exit
```

**Important**: Replace `CHANGE_THIS_PASSWORD` with a strong password!

### Configure PostgreSQL for Remote Access (if needed)

```bash
# Edit postgresql.conf to listen on all interfaces
sudo nano /etc/postgresql/15/main/postgresql.conf

# Find and change:
# listen_addresses = 'localhost'
# TO:
# listen_addresses = '*'

# Edit pg_hba.conf to allow connections
sudo nano /etc/postgresql/15/main/pg_hba.conf

# Add this line (replace YOUR_IP with your local IP):
# host    funding_rates    funding_user    YOUR_IP/32    scram-sha-256
# For local VPS access only, skip this step

# Restart PostgreSQL
sudo systemctl restart postgresql
```

---

## Step 5: Install Redis

```bash
# Install Redis
sudo apt install -y redis-server

# Edit Redis config for better performance
sudo nano /etc/redis/redis.conf

# Find and set these values:
# maxmemory 256mb
# maxmemory-policy allkeys-lru
# save ""  # Disable persistence for cache-only use

# Restart Redis
sudo systemctl restart redis-server

# Check Redis is running
sudo systemctl status redis-server

# Test Redis
redis-cli ping
# Should return: PONG
```

---

## Step 6: Install Python 3.11+

```bash
# Add deadsnakes PPA for Python 3.11
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Install pip
curl -sS https://bootstrap.pypa.io/get-pip.py | sudo python3.11

# Verify installation
python3.11 --version
pip3.11 --version
```

---

## Step 7: Install Nginx (Optional - for SSL/domain)

```bash
# Install Nginx
sudo apt install -y nginx

# Start and enable Nginx
sudo systemctl start nginx
sudo systemctl enable nginx

# Check status
sudo systemctl status nginx
```

---

## Step 8: Configure Firewall

```bash
# Install UFW if not installed
sudo apt install -y ufw

# Allow SSH (IMPORTANT: do this first!)
sudo ufw allow 22/tcp

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Allow PostgreSQL (only if accessing remotely)
# sudo ufw allow 5432/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status
```

---

## Step 9: Create Application Directory

```bash
# Create app directory
sudo mkdir -p /opt/funding-rate-service
sudo chown $USER:$USER /opt/funding-rate-service

# Create logs directory
sudo mkdir -p /var/log/funding-rate-service
sudo chown $USER:$USER /var/log/funding-rate-service

# Navigate to directory
cd /opt/funding-rate-service
```

---

## Step 10: Set Up Git Deployment

```bash
# Initialize git (if not cloning)
cd /opt/funding-rate-service
git init

# Or clone your repository
git clone YOUR_REPO_URL /opt/funding-rate-service

# Create environment file
nano .env
```

**Example `.env` file:**
```bash
# Database
DATABASE_URL=postgresql://funding_user:YOUR_PASSWORD@localhost:5432/funding_rates
DATABASE_POOL_MIN_SIZE=5
DATABASE_POOL_MAX_SIZE=20

# Redis
REDIS_URL=redis://localhost:6379/0
USE_REDIS=true

# Service
SERVICE_PORT=8000
SERVICE_HOST=0.0.0.0
LOG_LEVEL=INFO
ENVIRONMENT=production

# DEX APIs (add your API URLs)
LIGHTER_API_URL=https://api.lighter.xyz
EDGEX_API_URL=https://api.edgex.exchange
PARADEX_API_URL=https://api.paradex.trade
GRVT_API_URL=https://api.grvt.io
HYPERLIQUID_API_URL=https://api.hyperliquid.xyz

# Collection settings
COLLECTION_INTERVAL_SECONDS=60
MAX_CONCURRENT_COLLECTIONS=10
COLLECTION_TIMEOUT_SECONDS=30

# Cache settings
CACHE_TTL_SECONDS=60
CACHE_MAX_SIZE_MB=100
```

---

## Step 11: Set Up Python Virtual Environment

```bash
cd /opt/funding-rate-service

# Create virtual environment
python3.11 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies (will create requirements.txt later)
# pip install -r requirements.txt
```

---

## Step 12: Create Systemd Service

```bash
# Create systemd service file
sudo nano /etc/systemd/system/funding-rate-service.service
```

**Service file content:**
```ini
[Unit]
Description=Funding Rate Service
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/opt/funding-rate-service
Environment="PATH=/opt/funding-rate-service/venv/bin"
EnvironmentFile=/opt/funding-rate-service/.env
ExecStart=/opt/funding-rate-service/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2

# Restart policy
Restart=always
RestartSec=10

# Logging
StandardOutput=append:/var/log/funding-rate-service/service.log
StandardError=append:/var/log/funding-rate-service/error.log

[Install]
WantedBy=multi-user.target
```

**Replace `YOUR_USERNAME` with your actual username!**

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable funding-rate-service

# Don't start yet - we need to create the application first
# sudo systemctl start funding-rate-service
```

---

## Step 13: Configure Nginx Reverse Proxy (Optional)

```bash
# Create Nginx config
sudo nano /etc/nginx/sites-available/funding-rate-api
```

**Nginx config:**
```nginx
server {
    listen 80;
    server_name your-domain.com;  # Change this

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/funding-rate-api /etc/nginx/sites-enabled/

# Test Nginx config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

### Optional: SSL with Certbot

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal is set up automatically
```

---

## Step 14: Database Initialization Script

Create a script to initialize the database schema:

```bash
cd /opt/funding-rate-service
nano scripts/init_db.sh
```

**Script content:**
```bash
#!/bin/bash

# Load environment variables
source .env

# Run database initialization SQL
psql $DATABASE_URL -f funding_rate_service/database/schema.sql

echo "Database initialized successfully!"
```

```bash
# Make executable
chmod +x scripts/init_db.sh
```

---

## Quick Reference Commands

### PostgreSQL
```bash
# Connect to database
psql -U funding_user -d funding_rates

# Check connections
sudo -u postgres psql -c "SELECT * FROM pg_stat_activity;"

# Restart
sudo systemctl restart postgresql
```

### Redis
```bash
# Connect to Redis CLI
redis-cli

# Check memory usage
redis-cli INFO memory

# Flush all cache
redis-cli FLUSHALL

# Restart
sudo systemctl restart redis-server
```

### Application Service
```bash
# Start service
sudo systemctl start funding-rate-service

# Stop service
sudo systemctl stop funding-rate-service

# Restart service
sudo systemctl restart funding-rate-service

# Check status
sudo systemctl status funding-rate-service

# View logs
sudo journalctl -u funding-rate-service -f

# View application logs
tail -f /var/log/funding-rate-service/service.log
```

### Git Deployment
```bash
cd /opt/funding-rate-service

# Pull latest changes
git pull origin main

# Activate venv
source venv/bin/activate

# Install/update dependencies
pip install -r requirements.txt

# Restart service
sudo systemctl restart funding-rate-service
```

---

## Monitoring Commands

```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check CPU usage
top

# Check PostgreSQL size
sudo -u postgres psql -c "SELECT pg_database.datname, pg_size_pretty(pg_database_size(pg_database.datname)) AS size FROM pg_database;"

# Check Redis memory
redis-cli INFO memory | grep used_memory_human
```

---

## Troubleshooting

### PostgreSQL not starting
```bash
# Check logs
sudo journalctl -u postgresql -n 50

# Check disk space
df -h
```

### Redis not starting
```bash
# Check logs
sudo journalctl -u redis-server -n 50

# Check config
redis-server --test-config
```

### Application not starting
```bash
# Check service logs
sudo journalctl -u funding-rate-service -n 100

# Check application logs
tail -n 100 /var/log/funding-rate-service/error.log

# Test manually
cd /opt/funding-rate-service
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Security Checklist

- [ ] Changed default PostgreSQL password
- [ ] Configured firewall (UFW)
- [ ] Disabled root login over SSH
- [ ] Set up SSH keys (disable password auth)
- [ ] Configured fail2ban for SSH
- [ ] Regular system updates scheduled
- [ ] PostgreSQL not exposed to internet (unless needed)
- [ ] Redis not exposed to internet
- [ ] SSL certificate installed (if using domain)
- [ ] Environment variables secured (.env not in git)
- [ ] Regular database backups configured

---

## Backup Strategy

### PostgreSQL Backup
```bash
# Create backup script
nano /opt/funding-rate-service/scripts/backup_db.sh
```

```bash
#!/bin/bash
BACKUP_DIR="/opt/funding-rate-service/backups"
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)
pg_dump -U funding_user funding_rates | gzip > $BACKUP_DIR/funding_rates_$DATE.sql.gz

# Keep only last 7 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete
```

```bash
chmod +x /opt/funding-rate-service/scripts/backup_db.sh

# Add to crontab for daily backups
crontab -e
# Add: 0 2 * * * /opt/funding-rate-service/scripts/backup_db.sh
```

---

## Next Steps

1. ✅ VPS is now configured with PostgreSQL, TimescaleDB, and Redis
2. ⏭️ Implement the funding rate service locally
3. ⏭️ Test locally
4. ⏭️ Push to Git
5. ⏭️ Pull on VPS and deploy
6. ⏭️ Initialize database schema
7. ⏭️ Start the service

