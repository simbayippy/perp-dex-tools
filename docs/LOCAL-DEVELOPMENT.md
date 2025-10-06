# Local Development Setup - Funding Rate Service

## Development Environment

You'll develop the funding rate service locally on your machine, then deploy to VPS via Git.

---

## Local Prerequisites

### Option 1: Docker (Recommended for Local Dev)
- Docker Desktop installed
- Docker Compose installed

### Option 2: Native Installation
- PostgreSQL 15+
- Redis 7+
- Python 3.11+

---

## Local Setup with Docker (Recommended)

### Step 1: Create Docker Compose for Local Dev

The `docker-compose.yml` file will be in `/perp-dex-tools/funding_rate_service/`

```yaml
version: '3.8'

services:
  postgres:
    image: timescale/timescaledb:latest-pg15
    container_name: funding-rate-postgres
    environment:
      POSTGRES_DB: funding_rates
      POSTGRES_USER: funding_user
      POSTGRES_PASSWORD: dev_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./database/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U funding_user -d funding_rates"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: funding-rate-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

volumes:
  postgres_data:
```

### Step 2: Start Local Services

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Start services
docker-compose up -d

# Check services are running
docker-compose ps

# View logs
docker-compose logs -f
```

### Step 3: Set Up Python Environment

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Create virtual environment
python3.11 -m venv venv

# Activate
source venv/bin/activate  # On macOS/Linux
# or
.\venv\Scripts\activate  # On Windows

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Project Structure

We'll create the funding rate service as a subfolder:

```
perp-dex-tools/
├── docs/
├── exchanges/
├── strategies/
├── helpers/
├── funding_rate_service/          # NEW SERVICE
│   ├── __init__.py
│   ├── main.py                    # FastAPI entry point
│   ├── config.py                  # Configuration management
│   ├── requirements.txt           # Python dependencies
│   ├── docker-compose.yml         # Local dev environment
│   ├── .env.example              # Example environment variables
│   ├── .env                       # Local environment (gitignored)
│   │
│   ├── api/                       # API Layer
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── funding_rates.py
│   │   │   ├── opportunities.py
│   │   │   ├── dexes.py
│   │   │   ├── history.py
│   │   │   └── health.py
│   │   └── dependencies.py        # FastAPI dependencies
│   │
│   ├── core/                      # Business Logic Layer
│   │   ├── __init__.py
│   │   ├── opportunity_finder.py
│   │   ├── fee_calculator.py
│   │   ├── data_aggregator.py
│   │   └── mappers.py             # DEX/Symbol ID<->Name mappers
│   │
│   ├── collection/                # Data Collection Layer
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Collection orchestrator
│   │   ├── base_adapter.py        # Base adapter interface
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   ├── lighter.py
│   │   │   ├── edgex.py
│   │   │   ├── paradex.py
│   │   │   ├── grvt.py
│   │   │   └── hyperliquid.py
│   │   └── circuit_breaker.py
│   │
│   ├── database/                  # Data Access Layer
│   │   ├── __init__.py
│   │   ├── connection.py          # Database connection
│   │   ├── schema.sql             # Database schema
│   │   ├── migrations/            # Database migrations
│   │   ├── repositories/
│   │   │   ├── __init__.py
│   │   │   ├── dex_repository.py
│   │   │   ├── symbol_repository.py
│   │   │   ├── funding_rate_repository.py
│   │   │   └── opportunity_repository.py
│   │   └── models.py              # SQLAlchemy models (optional)
│   │
│   ├── cache/                     # Caching Layer
│   │   ├── __init__.py
│   │   ├── cache_manager.py
│   │   └── redis_client.py
│   │
│   ├── models/                    # Pydantic Models
│   │   ├── __init__.py
│   │   ├── dex.py
│   │   ├── symbol.py
│   │   ├── funding_rate.py
│   │   ├── opportunity.py
│   │   └── filters.py
│   │
│   ├── tasks/                     # Background Tasks
│   │   ├── __init__.py
│   │   ├── collection_task.py
│   │   ├── opportunity_task.py
│   │   └── cleanup_task.py
│   │
│   ├── utils/                     # Utilities
│   │   ├── __init__.py
│   │   ├── logger.py
│   │   └── helpers.py
│   │
│   ├── tests/                     # Tests
│   │   ├── __init__.py
│   │   ├── test_api/
│   │   ├── test_core/
│   │   ├── test_collection/
│   │   └── conftest.py
│   │
│   └── scripts/                   # Utility scripts
│       ├── init_db.sh
│       ├── seed_dexes.py
│       └── test_adapters.py
│
├── trading_bot.py
├── runbot.py
└── README.md
```

---

## Development Workflow

### 1. Work Locally
```bash
# Start local services (PostgreSQL + Redis)
cd funding_rate_service
docker-compose up -d

# Activate Python environment
source venv/bin/activate

# Run the service
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# In another terminal: run tests
pytest

# Access API docs
# http://localhost:8000/docs
```

### 2. Commit and Push
```bash
git add .
git commit -m "Implement funding rate collection"
git push origin main
```

### 3. Deploy to VPS
```bash
# SSH into VPS
ssh your-vps

# Navigate to service directory
cd /opt/funding-rate-service

# Pull latest changes
git pull origin main

# Activate venv
source venv/bin/activate

# Install any new dependencies
pip install -r requirements.txt

# Run database migrations if needed
python scripts/migrate.py

# Restart service
sudo systemctl restart funding-rate-service

# Check status
sudo systemctl status funding-rate-service
```

---

## Environment Files

### `.env.example` (committed to Git)
```bash
# Database
DATABASE_URL=postgresql://funding_user:password@localhost:5432/funding_rates
DATABASE_POOL_MIN_SIZE=5
DATABASE_POOL_MAX_SIZE=20

# Redis
REDIS_URL=redis://localhost:6379/0
USE_REDIS=true

# Service
SERVICE_PORT=8000
SERVICE_HOST=0.0.0.0
LOG_LEVEL=INFO
ENVIRONMENT=development

# DEX APIs
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

### `.env` (local, NOT committed)
```bash
# Copy from .env.example and modify for local dev
cp .env.example .env

# Edit with your local settings
# For local Docker: DATABASE_URL=postgresql://funding_user:dev_password@localhost:5432/funding_rates
```

---

## Git Ignore

Add to `.gitignore`:
```
# Funding rate service
funding_rate_service/.env
funding_rate_service/venv/
funding_rate_service/__pycache__/
funding_rate_service/*.pyc
funding_rate_service/.pytest_cache/
funding_rate_service/.coverage
funding_rate_service/htmlcov/
```

---

## Quick Commands Cheat Sheet

### Local Development
```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# View logs
docker-compose logs -f postgres
docker-compose logs -f redis

# Run app in dev mode (auto-reload)
uvicorn main:app --reload --port 8000

# Run tests
pytest -v

# Check code style
ruff check .
black --check .

# Format code
black .
```

### Database Operations
```bash
# Connect to local PostgreSQL
docker exec -it funding-rate-postgres psql -U funding_user -d funding_rates

# Run migrations
python scripts/migrate.py

# Seed initial data
python scripts/seed_dexes.py

# Backup local database
docker exec funding-rate-postgres pg_dump -U funding_user funding_rates > backup.sql
```

### Testing Individual Components
```bash
# Test a specific adapter
python scripts/test_adapters.py lighter

# Test API endpoints
pytest tests/test_api/test_funding_rates.py -v

# Test with coverage
pytest --cov=funding_rate_service --cov-report=html
```

---

## VS Code / Cursor Configuration

Create `.vscode/settings.json` in `funding_rate_service/`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": false,
  "python.linting.flake8Enabled": true,
  "python.formatting.provider": "black",
  "editor.formatOnSave": true,
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "files.exclude": {
    "**/__pycache__": true,
    "**/*.pyc": true,
    ".pytest_cache": true
  }
}
```

---

## Next Steps

1. ✅ Review this guide
2. ⏭️ Create project structure
3. ⏭️ Implement core components step-by-step
4. ⏭️ Test locally
5. ⏭️ Deploy to VPS

