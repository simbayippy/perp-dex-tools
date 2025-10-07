"""
Tests for DEXes API endpoints
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_all_dexes(client: AsyncClient, api_prefix: str):
    """Test getting all DEXs"""
    response = await client.get(f"{api_prefix}/dexes")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check response structure
    assert "dexes" in data
    assert "count" in data
    
    # DEXs should be a list
    assert isinstance(data["dexes"], list)
    
    # If there are DEXs, check structure
    if data["dexes"]:
        dex = data["dexes"][0]
        assert "name" in dex
        assert "display_name" in dex
        assert "is_active" in dex
        assert "fee_structure" in dex
        assert "supported_symbols_count" in dex
        assert "is_healthy" in dex
        
        # Check fee structure
        assert "maker_fee_percent" in dex["fee_structure"]
        assert "taker_fee_percent" in dex["fee_structure"]
        assert "has_fee_tiers" in dex["fee_structure"]


@pytest.mark.asyncio
async def test_get_dex_metadata_valid(client: AsyncClient, api_prefix: str):
    """Test getting metadata for a valid DEX"""
    # Test with a known DEX (may not exist in test DB)
    response = await client.get(f"{api_prefix}/dexes/lighter")
    
    # Could be 404 if DEX doesn't exist
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        
        # Check all required fields
        assert "name" in data
        assert "display_name" in data
        assert "is_active" in data
        assert "supports_websocket" in data
        assert "fee_structure" in data
        assert "collection_interval_seconds" in data
        assert "rate_limit_per_minute" in data
        assert "supported_symbols_count" in data
        assert "health" in data
        assert "created_at" in data
        assert "updated_at" in data
        
        # Check fee structure
        fee_struct = data["fee_structure"]
        assert "maker_fee_percent" in fee_struct
        assert "taker_fee_percent" in fee_struct
        assert "has_fee_tiers" in fee_struct
        
        # Check health
        health = data["health"]
        assert "is_healthy" in health
        assert "consecutive_errors" in health


@pytest.mark.asyncio
async def test_get_dex_metadata_invalid(client: AsyncClient, api_prefix: str):
    """Test getting metadata for an invalid DEX"""
    response = await client.get(f"{api_prefix}/dexes/nonexistent_dex")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_dex_metadata_case_insensitive(client: AsyncClient, api_prefix: str):
    """Test that DEX names are case-insensitive"""
    response1 = await client.get(f"{api_prefix}/dexes/lighter")
    response2 = await client.get(f"{api_prefix}/dexes/LIGHTER")
    response3 = await client.get(f"{api_prefix}/dexes/Lighter")
    
    # All should have the same status code
    assert response1.status_code == response2.status_code == response3.status_code
    
    # If successful, should return same data
    if response1.status_code == 200:
        assert response1.json()["name"] == response2.json()["name"] == response3.json()["name"]


@pytest.mark.asyncio
async def test_get_dex_symbols_valid(client: AsyncClient, api_prefix: str):
    """Test getting symbols for a valid DEX"""
    response = await client.get(f"{api_prefix}/dexes/lighter/symbols")
    
    # Could be 404 if DEX doesn't exist
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        
        # Check response structure
        assert "dex_name" in data
        assert "symbols" in data
        assert "count" in data
        
        # Symbols should be a list
        assert isinstance(data["symbols"], list)
        
        # If there are symbols, check structure
        if data["symbols"]:
            symbol = data["symbols"][0]
            assert "symbol" in symbol
            assert "dex_symbol_format" in symbol
            assert "is_active" in symbol


@pytest.mark.asyncio
async def test_get_dex_symbols_invalid(client: AsyncClient, api_prefix: str):
    """Test getting symbols for an invalid DEX"""
    response = await client.get(f"{api_prefix}/dexes/nonexistent_dex/symbols")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_dex_symbols_market_data(client: AsyncClient, api_prefix: str):
    """Test that symbol response includes market data"""
    response = await client.get(f"{api_prefix}/dexes/lighter/symbols")
    
    if response.status_code == 200:
        data = response.json()
        
        # If there are symbols with data, check market data fields
        if data["symbols"]:
            symbol = data["symbols"][0]
            
            # These fields might be None but should exist
            assert "volume_24h" in symbol
            assert "open_interest_usd" in symbol
            assert "spread_bps" in symbol
            assert "min_order_size" in symbol
            assert "last_updated" in symbol


@pytest.mark.asyncio
async def test_dexes_endpoint_performance(client: AsyncClient, api_prefix: str):
    """Test that DEXes endpoint returns quickly"""
    import time
    
    start = time.time()
    response = await client.get(f"{api_prefix}/dexes")
    elapsed = time.time() - start
    
    assert response.status_code == 200
    # Should return within 1 second
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_dex_metadata_fee_structure_values(client: AsyncClient, api_prefix: str):
    """Test that fee structure has valid values"""
    response = await client.get(f"{api_prefix}/dexes")
    
    if response.status_code == 200:
        data = response.json()
        
        for dex in data["dexes"]:
            fee_struct = dex["fee_structure"]
            
            # Fees should be non-negative and reasonable (< 1%)
            assert 0 <= fee_struct["maker_fee_percent"] <= 0.01
            assert 0 <= fee_struct["taker_fee_percent"] <= 0.01
            
            # Taker fee should typically be >= maker fee
            assert fee_struct["taker_fee_percent"] >= fee_struct["maker_fee_percent"]


@pytest.mark.asyncio
async def test_dex_symbols_sorted_by_volume(client: AsyncClient, api_prefix: str):
    """Test that symbols are sorted by volume (descending)"""
    response = await client.get(f"{api_prefix}/dexes/lighter/symbols")
    
    if response.status_code == 200:
        data = response.json()
        
        # Check if sorted by volume (descending)
        if len(data["symbols"]) > 1:
            volumes = [s["volume_24h"] for s in data["symbols"] if s["volume_24h"] is not None]
            
            # Should be sorted descending
            if len(volumes) > 1:
                for i in range(len(volumes) - 1):
                    assert volumes[i] >= volumes[i + 1]

