# Installation Guide

This guide explains how to install dependencies for the perp-dex-tools project after the shared exchange library refactoring.

## 📦 python installation

```
# 1. Activate your virtual environment
source venv/bin/activate

# 2. Install funding service dependencies (includes pytz)
pip install -r funding_rate_service/requirements.txt

# 3. Install exchange clients with all SDKs
pip install -e './exchange_clients[all]'
```