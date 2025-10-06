"""
Shared fixtures for API tests
"""

import pytest
import asyncio
from httpx import AsyncClient
from typing import AsyncGenerator

from main import app
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_app():
    """
    Create test app instance with database connection
    """
    # Connect to database
    await database.connect()
    
    # Load mappers
    await dex_mapper.load_from_db(database)
    await symbol_mapper.load_from_db(database)
    
    yield app
    
    # Disconnect
    await database.disconnect()


@pytest.fixture
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """
    Create async HTTP client for testing
    """
    async with AsyncClient(app=test_app, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def api_prefix():
    """API prefix for all endpoints"""
    return "/api/v1"

