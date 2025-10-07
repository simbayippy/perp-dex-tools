# Funding Rate Service

A microservice for collecting, analyzing, and providing funding rate data across multiple DEXs for arbitrage opportunities.

## Quick Start

### 1. Start Local Development Environment

```bash
# Start PostgreSQL and Redis
docker-compose up -d

# Create and activate virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Initialize database (once services are ready)
python scripts/init_db.py
```

### 2. Run the Service

```bash
# Development mode (auto-reload)
python main.py

# Or using uvicorn directly
uvicorn main:app --reload --port 8000
```

### 3. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Project Structure

```
funding_rate_service/
├── api/                    # API layer (FastAPI routes)
├── core/                   # Business logic
├── collection/             # Data collection from DEXs
├── database/               # Database access layer
├── cache/                  # Caching layer
├── models/                 # Pydantic models
├── tasks/                  # Background tasks
├── utils/                  # Utilities
├── tests/                  # Tests
├── scripts/                # Utility scripts
├── main.py                 # Application entry point
├── config.py               # Configuration
└── requirements.txt        # Python dependencies
```

## Development Workflow

### Running Tests

```bash
pytest
pytest --cov=. --cov-report=html
```

### Code Quality

```bash
# Format code
black .

# Check code style
ruff check .

# Type checking
mypy .
```

### Database Operations

```bash
# Connect to database
docker exec -it funding-rate-postgres psql -U funding_user -d funding_rates

# Run migrations
python scripts/migrate.py

# Seed initial data
python scripts/seed_dexes.py
```

## Deployment

See [VPS-SETUP.md](../docs/VPS-SETUP.md) for production deployment instructions.

## Documentation

- [System Design](../docs/tasks/funding-rate-service-design.md)
- [VPS Setup](../docs/VPS-SETUP.md)
- [Local Development](../docs/LOCAL-DEVELOPMENT.md)

## License

See parent repository license.

