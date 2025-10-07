# Installation Guide

This guide explains how to install dependencies for the perp-dex-tools project after the shared exchange library refactoring.

## 📦 Dependency Structure

The project now uses a **shared exchange library** architecture with three main dependency files:

1. **`requirements.txt`** - Core trading client dependencies (helpers, strategies, etc.)
2. **`exchange_clients/pyproject.toml`** - Exchange-specific SDKs (managed per-exchange)
3. **`funding_rate_service/requirements.txt`** - Funding rate service dependencies (FastAPI, database, etc.)

---

## 🚀 Quick Start Installation

### For Trading Client Users

```bash
# 1. Install core dependencies
pip install -r requirements.txt

# 2. Install exchange client library with ALL exchange SDKs
pip install -e './exchange_clients[all]'

# That's it! You can now use all exchanges.
```

### For Funding Rate Service Users

```bash
# From the funding_rate_service directory
cd funding_rate_service

# 1. Install service dependencies
pip install -r requirements.txt

# 2. Install exchange client library (from parent directory)
pip install -e '../exchange_clients[all]'

# That's it! The service can now collect funding rates from all exchanges.
```

---

## 🎯 Selective Exchange Installation

If you only need specific exchanges, you can install just those SDKs to save time and avoid conflicts:

### Install Single Exchange

```bash
# Install only Lighter
pip install -e './exchange_clients[lighter]'

# Install only GRVT
pip install -e './exchange_clients[grvt]'

# Install only EdgeX
pip install -e './exchange_clients[edgex]'

# Install only Backpack
pip install -e './exchange_clients[backpack]'
```

### Install Multiple Specific Exchanges

```bash
# Install Lighter + GRVT + EdgeX
pip install -e './exchange_clients[lighter,grvt,edgex]'

# Install EdgeX + Backpack
pip install -e './exchange_clients[edgex,backpack]'
```

---

## 📋 Available Exchanges

| Exchange | SDK Required | Install Command | Notes |
|----------|-------------|----------------|-------|
| **Lighter** | ✅ Yes | `[lighter]` | Git-based SDK |
| **GRVT** | ✅ Yes | `[grvt]` | PyPI package |
| **EdgeX** | ✅ Yes | `[edgex]` | Forked SDK with post_only support |
| **Backpack** | ✅ Yes | `[backpack]` | bpx-py SDK |
| **Paradex** | ⚠️ Optional | `[paradex]` | Currently has dependency conflicts |
| **Aster** | ❌ No | `[aster]` | Uses direct API calls |

---

## 🔧 Development Setup

For development, install in editable mode:

```bash
# Install core dependencies
pip install -r requirements.txt

# Install exchange library in editable mode with all exchanges
pip install -e './exchange_clients[all]'

# Install development tools (optional)
pip install pytest pytest-asyncio black ruff mypy
```

---

## 🐳 Docker Setup

If you're using Docker, add these to your Dockerfile:

```dockerfile
# Copy dependency files
COPY requirements.txt .
COPY exchange_clients/ ./exchange_clients/

# Install dependencies
RUN pip install -r requirements.txt
RUN pip install -e './exchange_clients[all]'
```

---

## ⚠️ Important Notes

### 1. **Order Matters**
Always install `requirements.txt` **before** `exchange_clients`:
```bash
pip install -r requirements.txt  # First
pip install -e './exchange_clients[all]'  # Second
```

### 2. **Editable Mode (`-e`)**
The `-e` flag installs in editable mode, meaning changes to the `exchange_clients` code take effect immediately without reinstalling.

### 3. **Virtual Environment Recommended**
Always use a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 4. **Paradex Conflicts**
Paradex SDK (`paradex-py`) currently has dependency conflicts and is commented out. Install manually if needed:
```bash
pip install paradex-py  # May cause conflicts
```

---

## 🔄 Upgrading from Old Structure

If you're upgrading from the old `/exchanges/` structure:

```bash
# 1. Uninstall old exchange SDKs (if installed globally)
pip uninstall lighter-python edgex-python-sdk grvt-pysdk -y

# 2. Install new structure
pip install -r requirements.txt
pip install -e './exchange_clients[all]'
```

---

## 📚 What Gets Installed?

### Core (`requirements.txt`)
- `python-dotenv` - Environment variable management
- `pytz` - Timezone handling
- `pydantic` - Data validation
- `pycryptodome`, `ecdsa` - Cryptography
- `requests` - HTTP client

### Exchange Clients (`exchange_clients[all]`)
- **Base dependencies**: `aiohttp`, `websockets`, `tenacity`
- **Lighter**: `lighter-sdk`, `eth-account`
- **GRVT**: `grvt-pysdk`
- **EdgeX**: `edgex-python-sdk`, `httpx`
- **Backpack**: `bpx-py`, `cryptography`

### Funding Rate Service (`funding_rate_service/requirements.txt`)
- `fastapi`, `uvicorn` - Web framework
- `databases`, `asyncpg`, `psycopg2-binary` - Database
- `redis`, `aioredis` - Cache
- `pytest`, `black`, `ruff` - Development tools

---

## 🆘 Troubleshooting

### "No module named 'exchange_clients'"
```bash
# Make sure you installed the exchange_clients library
pip install -e './exchange_clients[all]'

# Verify installation
python -c "import exchange_clients; print('✅ Installed')"
```

### "No module named 'lighter'"
```bash
# The SDK wasn't installed. Install with the [lighter] extra
pip install -e './exchange_clients[lighter]'
```

### Dependency Conflicts
```bash
# Try installing without paradex
pip install -e './exchange_clients[lighter,grvt,edgex,backpack,aster]'
```

### Import Errors After Upgrade
```bash
# Clear Python cache
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# Reinstall
pip install --force-reinstall -e './exchange_clients[all]'
```

---

## ✅ Verify Installation

Run this to verify everything is installed correctly:

```python
# test_installation.py
from exchange_clients.factory import ExchangeFactory

print("Available exchanges:", ExchangeFactory.get_supported_exchanges())
print("✅ Installation successful!")
```

Expected output:
```
Available exchanges: ['edgex', 'backpack', 'paradex', 'aster', 'lighter', 'grvt']
✅ Installation successful!
```

---

**Last Updated:** 2025-10-07  
**Version:** 2.0 (Shared Exchange Library Architecture)

