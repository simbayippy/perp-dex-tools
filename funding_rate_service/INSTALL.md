# Installation Guide

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ (with TimescaleDB extension)
- Redis (optional, for caching)

## Step 1: Install Lighter SDK

The Lighter adapter requires the Lighter Python SDK:

```bash
# From the project root
cd ../lighter-python
pip install -e .

# Or if you're in funding_rate_service directory
cd ../../lighter-python
pip install -e .
```

## Step 2: Install Service Dependencies

```bash
cd funding_rate_service
pip install -r requirements.txt
```

## Step 3: Setup Database

```bash
# Initialize database
python scripts/init_db.py

# Seed DEX data
python scripts/seed_dexes.py
```

## Step 4: Configure Environment

Create a `.env` file in the `funding_rate_service` directory:

```bash
# Database
DATABASE_URL=postgresql://funding_user:your_password@localhost:5432/funding_rates

# Service
SERVICE_PORT=8000
SERVICE_HOST=0.0.0.0
LOG_LEVEL=INFO
ENVIRONMENT=development

# Redis (optional)
REDIS_URL=redis://localhost:6379/0
USE_REDIS=false  # Set to true if using Redis

# Collection
COLLECTION_INTERVAL_SECONDS=60
```

## Step 5: Test the Setup

Test the Lighter adapter:

```bash
python scripts/test_lighter_adapter.py
```

Expected output:
```
============================================================
Testing Lighter Adapter
============================================================

📡 Fetching funding rates from Lighter...

✅ Success!
   Latency: 250ms
   Fetched: 20 funding rates

------------------------------------------------------------
Symbol     Funding Rate     Annualized APY 
------------------------------------------------------------
BTC        0.00010000             10.95%
ETH        0.00008000              8.76%
...
```

## Step 6: Run the Service

```bash
# Development mode (with hot reload)
python main.py

# Or using uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Visit http://localhost:8000/docs for the API documentation.

## Troubleshooting

### Lighter SDK Import Error

If you get `ImportError: No module named 'lighter'`:

```bash
# Make sure you installed the Lighter SDK
cd ../lighter-python
pip install -e .
```

### Database Connection Error

Make sure PostgreSQL is running:

```bash
# Check if PostgreSQL is running
pg_isready

# If not, start it
sudo systemctl start postgresql  # Linux
brew services start postgresql   # macOS
```

### Python Version Issues

Make sure you're using Python 3.11+:

```bash
python --version  # Should be 3.11 or higher
```

## Next Steps

Once installed and tested:

1. **Phase 2.3**: Implement other DEX adapters (EdgeX, Paradex, GRVT)
2. **Phase 2.4**: Create collection orchestrator
3. **Phase 3**: Implement business logic (fee calculator, opportunity finder)
4. **Phase 4**: Create API endpoints
5. **Phase 5**: Add background tasks

See `PROGRESS.md` for detailed roadmap.

