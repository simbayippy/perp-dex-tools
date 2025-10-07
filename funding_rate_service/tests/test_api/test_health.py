"""
Tests for Health API endpoints
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test root endpoint"""
    response = await client.get("/")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["service"] == "Funding Rate Service"
    assert data["status"] == "running"
    assert "docs" in data


@pytest.mark.asyncio
async def test_ping_endpoint(client: AsyncClient):
    """Test simple ping endpoint"""
    response = await client.get("/ping")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_simple(client: AsyncClient, api_prefix: str):
    """Test simple health check"""
    response = await client.get(f"{api_prefix}/health/simple")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] == "ok"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_health_comprehensive(client: AsyncClient, api_prefix: str):
    """Test comprehensive health check"""
    response = await client.get(f"{api_prefix}/health")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check top-level fields
    assert "status" in data
    assert data["status"] in ["healthy", "degraded", "unhealthy"]
    assert "timestamp" in data
    assert "database" in data
    assert "dex_health" in data
    assert "dex_summary" in data
    assert "data_freshness" in data
    
    # Check database health
    assert "connected" in data["database"]
    
    # Check DEX health
    assert isinstance(data["dex_health"], list)
    
    # Check DEX summary
    assert "total" in data["dex_summary"]
    assert "active" in data["dex_summary"]
    assert "healthy" in data["dex_summary"]


@pytest.mark.asyncio
async def test_health_database(client: AsyncClient, api_prefix: str):
    """Test database health check"""
    response = await client.get(f"{api_prefix}/health/database")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "status" in data
    assert "connected" in data
    assert "timestamp" in data
    
    if data["connected"]:
        assert "statistics" in data
        assert "symbols" in data["statistics"]
        assert "dexes" in data["statistics"]


@pytest.mark.asyncio
async def test_health_dex_valid(client: AsyncClient, api_prefix: str):
    """Test DEX-specific health check for valid DEX"""
    # Test with a known DEX (assuming 'lighter' exists)
    response = await client.get(f"{api_prefix}/health/dex/lighter")
    
    # Should return 200 even if DEX doesn't exist in DB (will return 404)
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "dex_name" in data
        assert "status" in data
        assert "is_active" in data


@pytest.mark.asyncio
async def test_health_dex_invalid(client: AsyncClient, api_prefix: str):
    """Test DEX-specific health check for invalid DEX"""
    response = await client.get(f"{api_prefix}/health/dex/nonexistent_dex")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data

