"""
Pytest configuration and shared fixtures

This file is automatically loaded by pytest and provides:
- Python path setup
- Shared fixtures
- Test environment configuration
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Pytest configuration
pytest_plugins = ['pytest_asyncio']

