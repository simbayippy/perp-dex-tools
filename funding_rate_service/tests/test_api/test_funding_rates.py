"""
Tests for Funding Rates API endpoints
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_all_funding_rates(client: AsyncClient, api_prefix: str):
    """Test getting all funding rates"""
    response = await client.get(f"{api_prefix}/funding-rates")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check response structure
    assert "data" in data
    assert "updated_at" in data
    assert "count" in data
    
    # Data should be dict of symbols -> dex -> rate
    assert isinstance(data["data"], dict)


@pytest.mark.asyncio
async def test_get_funding_rates_with_dex_filter(client: AsyncClient, api_prefix: str):
    """Test getting funding rates filtered by DEX"""
    response = await client.get(f"{api_prefix}/funding-rates?dex=lighter")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "data" in data
    assert isinstance(data["data"], dict)


@pytest.mark.asyncio
async def test_get_funding_rates_with_symbol_filter(client: AsyncClient, api_prefix: str):
    """Test getting funding rates filtered by symbol"""
    response = await client.get(f"{api_prefix}/funding-rates?symbol=BTC")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "data" in data
    
    # If there's data, it should only be for BTC
    if data["data"]:
        assert "BTC" in data["data"] or len(data["data"]) == 0


@pytest.mark.asyncio
async def test_get_funding_rates_with_metadata(client: AsyncClient, api_prefix: str):
    """Test getting funding rates with DEX metadata"""
    response = await client.get(f"{api_prefix}/funding-rates?include_metadata=true")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "data" in data
    assert "dex_metadata" in data
    
    # Check metadata structure
    if data["dex_metadata"]:
        dex_meta = list(data["dex_metadata"].values())[0]
        assert "name" in dex_meta
        assert "maker_fee_percent" in dex_meta
        assert "taker_fee_percent" in dex_meta


@pytest.mark.asyncio
async def test_get_dex_funding_rates_valid(client: AsyncClient, api_prefix: str):
    """Test getting funding rates for a valid DEX"""
    response = await client.get(f"{api_prefix}/funding-rates/lighter")
    
    # Should return 200 even if no data (empty rates)
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "dex_name" in data
        assert "rates" in data
        assert "updated_at" in data
        
        # Rates should be dict of symbol -> rate_info
        assert isinstance(data["rates"], dict)


@pytest.mark.asyncio
async def test_get_dex_funding_rates_invalid(client: AsyncClient, api_prefix: str):
    """Test getting funding rates for invalid DEX"""
    response = await client.get(f"{api_prefix}/funding-rates/nonexistent_dex")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_dex_symbol_funding_rate_valid(client: AsyncClient, api_prefix: str):
    """Test getting funding rate for specific DEX and symbol"""
    # Test with known DEX and symbol (may not exist)
    response = await client.get(f"{api_prefix}/funding-rates/lighter/BTC")
    
    # Could be 404 if no data exists
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "dex_name" in data
        assert "symbol" in data
        assert "funding_rate" in data
        assert "annualized_rate" in data
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_get_dex_symbol_funding_rate_invalid_dex(client: AsyncClient, api_prefix: str):
    """Test getting funding rate for invalid DEX"""
    response = await client.get(f"{api_prefix}/funding-rates/invalid_dex/BTC")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_dex_symbol_funding_rate_invalid_symbol(client: AsyncClient, api_prefix: str):
    """Test getting funding rate for invalid symbol"""
    response = await client.get(f"{api_prefix}/funding-rates/lighter/INVALID")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_historical_funding_rates(client: AsyncClient, api_prefix: str):
    """Test getting historical funding rates"""
    response = await client.get(f"{api_prefix}/history/funding-rates/lighter/BTC")
    
    # Could be 404 if DEX/symbol not found
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "dex_name" in data
        assert "symbol" in data
        assert "data_points" in data
        assert "avg_rate" in data
        assert "period_start" in data
        assert "period_end" in data


@pytest.mark.asyncio
async def test_get_historical_funding_rates_with_period(client: AsyncClient, api_prefix: str):
    """Test getting historical funding rates with custom period"""
    response = await client.get(f"{api_prefix}/history/funding-rates/lighter/BTC?period=30d")
    
    assert response.status_code in [200, 404]


@pytest.mark.asyncio
async def test_get_historical_funding_rates_invalid_period(client: AsyncClient, api_prefix: str):
    """Test getting historical funding rates with invalid period"""
    response = await client.get(f"{api_prefix}/history/funding-rates/lighter/BTC?period=invalid")
    
    # Should return 400 for invalid period format
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_funding_rate_stats(client: AsyncClient, api_prefix: str):
    """Test getting funding rate statistics"""
    response = await client.get(f"{api_prefix}/stats/funding-rates/BTC")
    
    # Could be 404 if symbol not found
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "symbol" in data
        assert "period_days" in data
        assert "avg_funding_rate" in data
        assert "median_funding_rate" in data
        assert "std_dev" in data
        assert "volatility" in data


@pytest.mark.asyncio
async def test_get_funding_rate_stats_with_dex(client: AsyncClient, api_prefix: str):
    """Test getting funding rate statistics for specific DEX"""
    response = await client.get(f"{api_prefix}/stats/funding-rates/BTC?dex=lighter")
    
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert "symbol" in data
        assert "dex_name" in data


@pytest.mark.asyncio
async def test_get_funding_rate_stats_with_period(client: AsyncClient, api_prefix: str):
    """Test getting funding rate statistics with custom period"""
    response = await client.get(f"{api_prefix}/stats/funding-rates/BTC?period=90d")
    
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert data["period_days"] == 90

