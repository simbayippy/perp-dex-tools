"""
Tests for Opportunities API endpoints
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_opportunities_default(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with default filters"""
    response = await client.get(f"{api_prefix}/opportunities")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check response structure
    assert "opportunities" in data
    assert "total_count" in data
    assert "filters_applied" in data
    assert "generated_at" in data
    
    # Opportunities should be a list
    assert isinstance(data["opportunities"], list)
    
    # Check filters applied
    assert "limit" in data["filters_applied"]
    assert data["filters_applied"]["limit"] == 10  # Default limit


@pytest.mark.asyncio
async def test_get_opportunities_with_symbol_filter(client: AsyncClient, api_prefix: str):
    """Test getting opportunities filtered by symbol"""
    response = await client.get(f"{api_prefix}/opportunities?symbol=BTC")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "opportunities" in data
    assert data["filters_applied"]["symbol"] == "BTC"
    
    # If there are opportunities, they should all be for BTC
    for opp in data["opportunities"]:
        assert opp["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_get_opportunities_with_min_profit(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with minimum profit filter"""
    response = await client.get(f"{api_prefix}/opportunities?min_profit=0.0001")
    
    assert response.status_code == 200
    data = response.json()
    
    # All opportunities should have profit >= 0.0001
    for opp in data["opportunities"]:
        assert opp["net_profit_percent"] >= 0.0001


@pytest.mark.asyncio
async def test_get_opportunities_with_max_oi(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with max OI filter (low OI farming)"""
    response = await client.get(f"{api_prefix}/opportunities?max_oi=2000000")
    
    assert response.status_code == 200
    data = response.json()
    
    # All opportunities should have OI <= 2M (if OI data exists)
    for opp in data["opportunities"]:
        if opp["min_oi_usd"] is not None:
            assert opp["min_oi_usd"] <= 2000000


@pytest.mark.asyncio
async def test_get_opportunities_with_include_dexes(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with included DEXs"""
    response = await client.get(f"{api_prefix}/opportunities?include_dexes=lighter,grvt")
    
    assert response.status_code == 200
    data = response.json()
    
    # All opportunities should involve lighter or grvt
    for opp in data["opportunities"]:
        assert opp["long_dex"] in ["lighter", "grvt"] or opp["short_dex"] in ["lighter", "grvt"]


@pytest.mark.asyncio
async def test_get_opportunities_with_exclude_dexes(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with excluded DEXs"""
    response = await client.get(f"{api_prefix}/opportunities?exclude_dexes=edgex")
    
    assert response.status_code == 200
    data = response.json()
    
    # No opportunities should involve edgex
    for opp in data["opportunities"]:
        assert opp["long_dex"] != "edgex"
        assert opp["short_dex"] != "edgex"


@pytest.mark.asyncio
async def test_get_opportunities_with_limit(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with custom limit"""
    response = await client.get(f"{api_prefix}/opportunities?limit=5")
    
    assert response.status_code == 200
    data = response.json()
    
    # Should return at most 5 opportunities
    assert len(data["opportunities"]) <= 5


@pytest.mark.asyncio
async def test_get_opportunities_with_sorting(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with custom sorting"""
    response = await client.get(f"{api_prefix}/opportunities?sort_by=divergence&sort_desc=true")
    
    assert response.status_code == 200
    data = response.json()
    
    # Opportunities should be sorted by divergence descending
    if len(data["opportunities"]) > 1:
        for i in range(len(data["opportunities"]) - 1):
            assert data["opportunities"][i]["divergence"] >= data["opportunities"][i + 1]["divergence"]


@pytest.mark.asyncio
async def test_get_opportunities_multiple_filters(client: AsyncClient, api_prefix: str):
    """Test getting opportunities with multiple filters"""
    response = await client.get(
        f"{api_prefix}/opportunities?"
        f"min_profit=0.0001&"
        f"max_oi=5000000&"
        f"min_volume=100000&"
        f"limit=20"
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify filters were applied
    assert data["filters_applied"]["min_profit_percent"] == 0.0001
    assert data["filters_applied"]["max_oi_usd"] == 5000000
    assert data["filters_applied"]["min_volume_24h"] == 100000
    assert data["filters_applied"]["limit"] == 20


@pytest.mark.asyncio
async def test_get_best_opportunity_default(client: AsyncClient, api_prefix: str):
    """Test getting the best opportunity"""
    response = await client.get(f"{api_prefix}/opportunities/best")
    
    assert response.status_code == 200
    data = response.json()
    
    # Should have opportunity field
    assert "opportunity" in data
    assert "generated_at" in data
    
    # Opportunity could be None if no data
    if data["opportunity"]:
        assert "symbol" in data["opportunity"]
        assert "net_profit_percent" in data["opportunity"]
        assert data["rank"] == 1


@pytest.mark.asyncio
async def test_get_best_opportunity_with_filters(client: AsyncClient, api_prefix: str):
    """Test getting the best opportunity with filters"""
    response = await client.get(f"{api_prefix}/opportunities/best?max_oi=1000000")
    
    assert response.status_code == 200
    data = response.json()
    
    # If there's an opportunity, it should meet the filter
    if data["opportunity"] and data["opportunity"]["min_oi_usd"]:
        assert data["opportunity"]["min_oi_usd"] <= 1000000


@pytest.mark.asyncio
async def test_get_opportunities_for_symbol_valid(client: AsyncClient, api_prefix: str):
    """Test getting opportunities for a valid symbol"""
    response = await client.get(f"{api_prefix}/opportunities/symbol/BTC")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "symbol" in data
    assert data["symbol"] == "BTC"
    assert "opportunities" in data
    assert "count" in data
    
    # All opportunities should be for BTC
    for opp in data["opportunities"]:
        # Note: symbol field isn't in the response for this endpoint
        assert "long_dex" in opp
        assert "short_dex" in opp


@pytest.mark.asyncio
async def test_get_opportunities_for_symbol_with_filters(client: AsyncClient, api_prefix: str):
    """Test getting opportunities for a symbol with filters"""
    response = await client.get(f"{api_prefix}/opportunities/symbol/ETH?min_profit=0.0002&limit=5")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["symbol"] == "ETH"
    assert len(data["opportunities"]) <= 5
    
    # All opportunities should have profit >= 0.0002
    for opp in data["opportunities"]:
        assert opp["net_profit_percent"] >= 0.0002


@pytest.mark.asyncio
async def test_compare_dex_opportunities(client: AsyncClient, api_prefix: str):
    """Test comparing opportunities between two DEXs"""
    response = await client.get(f"{api_prefix}/opportunities/compare?dex1=lighter&dex2=grvt")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "dex1" in data
    assert "dex2" in data
    assert data["dex1"] == "lighter"
    assert data["dex2"] == "grvt"
    assert "opportunities" in data
    assert "count" in data


@pytest.mark.asyncio
async def test_compare_dex_opportunities_with_symbol(client: AsyncClient, api_prefix: str):
    """Test comparing opportunities between two DEXs for specific symbol"""
    response = await client.get(f"{api_prefix}/opportunities/compare?dex1=lighter&dex2=grvt&symbol=BTC")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "dex1" in data
    assert "dex2" in data
    assert "opportunities" in data


@pytest.mark.asyncio
async def test_compare_dex_opportunities_missing_dex(client: AsyncClient, api_prefix: str):
    """Test comparing opportunities without required DEX parameter"""
    response = await client.get(f"{api_prefix}/opportunities/compare?dex1=lighter")
    
    # Should return 422 for missing required parameter
    assert response.status_code == 422

