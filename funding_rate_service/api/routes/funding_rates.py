"""
Funding Rates API Routes

Endpoints for accessing latest and historical funding rates.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, Dict, Any
from datetime import datetime

from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from core.historical_analyzer import HistoricalAnalyzer
from core.dependencies import get_historical_analyzer
from models.funding_rate import (
    FundingRateResponse,
    LatestFundingRates,
    AllLatestFundingRates
)
from models.history import FundingRateHistory, FundingRateStats
from utils.logger import logger


router = APIRouter()


@router.get("/funding-rates")
async def get_all_funding_rates(
    dex: Optional[str] = Query(None, description="Filter by specific DEX"),
    symbol: Optional[str] = Query(None, description="Filter by specific symbol"),
    include_metadata: bool = Query(False, description="Include DEX metadata")
) -> Dict[str, Any]:
    """
    Get latest funding rates across all DEXs and symbols
    
    Returns funding rates in a nested structure: symbol -> dex -> rate
    """
    try:
        # Build query
        query = """
            SELECT 
                s.symbol,
                d.name as dex_name,
                lfr.funding_rate,
                lfr.next_funding_time,
                lfr.updated_at
            FROM latest_funding_rates lfr
            JOIN symbols s ON lfr.symbol_id = s.id
            JOIN dexes d ON lfr.dex_id = d.id
            WHERE d.is_active = TRUE
        """
        
        params = {}
        
        if symbol:
            query += " AND s.symbol = :symbol"
            params["symbol"] = symbol
        
        if dex:
            query += " AND d.name = :dex"
            params["dex"] = dex
        
        query += " ORDER BY s.symbol, d.name"
        
        # Execute query
        rows = await database.fetch_all(query, values=params)
        
        if not rows:
            return {
                "data": {},
                "updated_at": datetime.utcnow().isoformat(),
                "count": 0
            }
        
        # Organize data: symbol -> dex -> rate
        data = {}
        latest_update = None
        
        for row in rows:
            symbol_name = row['symbol']
            dex_name = row['dex_name']
            
            if symbol_name not in data:
                data[symbol_name] = {}
            
            data[symbol_name][dex_name] = float(row['funding_rate'])
            
            # Track latest update time
            if latest_update is None or row['updated_at'] > latest_update:
                latest_update = row['updated_at']
        
        response = {
            "data": data,
            "updated_at": latest_update.isoformat() if latest_update else datetime.utcnow().isoformat(),
            "count": len(rows)
        }
        
        # Add metadata if requested
        if include_metadata:
            # Get DEX metadata
            dex_query = "SELECT id, name, display_name, maker_fee_percent, taker_fee_percent FROM dexes WHERE is_active = TRUE"
            dex_rows = await database.fetch_all(dex_query)
            
            dex_metadata = {}
            for dex_row in dex_rows:
                dex_metadata[dex_row['name']] = {
                    "name": dex_row['name'],
                    "display_name": dex_row['display_name'],
                    "maker_fee_percent": float(dex_row['maker_fee_percent']),
                    "taker_fee_percent": float(dex_row['taker_fee_percent'])
                }
            
            response["dex_metadata"] = dex_metadata
        
        return response
        
    except Exception as e:
        logger.error(f"Error fetching funding rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/funding-rates/compare")
async def compare_funding_rates(
    symbol: str = Query(..., description="Symbol to compare (e.g., BTC)"),
    dex1: str = Query(..., description="First DEX name"),
    dex2: str = Query(..., description="Second DEX name")
) -> Dict[str, Any]:
    """
    Compare current funding rates between two DEXs for a specific symbol
    
    Perfect for position monitoring - quickly see rate divergence between your positions
    on different exchanges and decide if it's time to exit or rebalance.
    """
    try:
        # Normalize inputs
        symbol_upper = symbol.upper()
        dex1_lower = dex1.lower()
        dex2_lower = dex2.lower()
        
        # Verify DEXs exist
        dex1_id = dex_mapper.get_id(dex1_lower)
        dex2_id = dex_mapper.get_id(dex2_lower)
        
        if dex1_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex1}' not found")
        if dex2_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex2}' not found")
        
        # Verify symbol exists
        symbol_id = symbol_mapper.get_id(symbol_upper)
        if symbol_id is None:
            raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
        
        # Fetch rates for both DEXs
        query = """
            SELECT 
                d.name as dex_name,
                lfr.funding_rate,
                lfr.next_funding_time,
                lfr.updated_at
            FROM latest_funding_rates lfr
            JOIN dexes d ON lfr.dex_id = d.id
            WHERE lfr.symbol_id = :symbol_id 
            AND lfr.dex_id IN (:dex1_id, :dex2_id)
        """
        
        rows = await database.fetch_all(
            query,
            values={
                "symbol_id": symbol_id,
                "dex1_id": dex1_id,
                "dex2_id": dex2_id
            }
        )
        
        # Parse results
        rates_by_dex = {}
        for row in rows:
            rates_by_dex[row['dex_name']] = {
                "name": row['dex_name'],
                "funding_rate": float(row['funding_rate']),
                "next_funding_time": row['next_funding_time'].isoformat() if row['next_funding_time'] else None,
                "timestamp": row['updated_at'].isoformat()
            }
        
        # Check if we got both rates
        if dex1_lower not in rates_by_dex:
            raise HTTPException(
                status_code=404,
                detail=f"No funding rate found for {symbol} on {dex1}"
            )
        if dex2_lower not in rates_by_dex:
            raise HTTPException(
                status_code=404,
                detail=f"No funding rate found for {symbol} on {dex2}"
            )
        
        # Calculate divergence
        rate1 = rates_by_dex[dex1_lower]['funding_rate']
        rate2 = rates_by_dex[dex2_lower]['funding_rate']
        divergence = abs(rate2 - rate1)
        divergence_bps = divergence * 10000  # Convert to basis points
        
        # Determine recommendations (long the lower rate, short the higher rate)
        if rate1 < rate2:
            long_recommendation = dex1_lower
            short_recommendation = dex2_lower
            estimated_net_profit_8h = rate2 - rate1  # Profit from rate difference
        else:
            long_recommendation = dex2_lower
            short_recommendation = dex1_lower
            estimated_net_profit_8h = rate1 - rate2
        
        return {
            "symbol": symbol_upper,
            "dex1": rates_by_dex[dex1_lower],
            "dex2": rates_by_dex[dex2_lower],
            "divergence": divergence,
            "divergence_bps": round(divergence_bps, 2),
            "long_recommendation": long_recommendation,
            "short_recommendation": short_recommendation,
            "estimated_net_profit_8h": estimated_net_profit_8h,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing funding rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/funding-rates/{dex}")
async def get_dex_funding_rates(
    dex: str
) -> Dict[str, Any]:
    """
    Get funding rates for a specific DEX
    
    Returns all symbols and their rates for the specified DEX
    """
    try:
        # Verify DEX exists
        dex_id = dex_mapper.get_id(dex.lower())
        if dex_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        # Query rates
        query = """
            SELECT 
                s.symbol,
                lfr.funding_rate,
                lfr.next_funding_time,
                lfr.updated_at
            FROM latest_funding_rates lfr
            JOIN symbols s ON lfr.symbol_id = s.id
            WHERE lfr.dex_id = :dex_id
            ORDER BY s.symbol
        """
        
        rows = await database.fetch_all(query, values={"dex_id": dex_id})
        
        if not rows:
            return {
                "dex_name": dex,
                "rates": {},
                "updated_at": datetime.utcnow().isoformat()
            }
        
        # Format response
        rates = {}
        latest_update = None
        
        for row in rows:
            # Calculate annualized rate (8-hour periods, 3x per day)
            funding_rate = row['funding_rate']
            annualized_rate = float(funding_rate) * 365 * 3
            
            rates[row['symbol']] = {
                "funding_rate": float(funding_rate),
                "annualized_rate": annualized_rate,
                "next_funding_time": row['next_funding_time'].isoformat() if row['next_funding_time'] else None,
                "timestamp": row['updated_at'].isoformat()
            }
            
            if latest_update is None or row['updated_at'] > latest_update:
                latest_update = row['updated_at']
        
        return {
            "dex_name": dex,
            "rates": rates,
            "updated_at": latest_update.isoformat() if latest_update else datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching rates for {dex}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/funding-rates/{dex}/{symbol}")
async def get_dex_symbol_funding_rate(
    dex: str,
    symbol: str
) -> Dict[str, Any]:
    """
    Get funding rate for specific DEX and symbol
    
    Returns detailed information including prices and next funding time
    """
    try:
        # Verify DEX and symbol exist
        dex_id = dex_mapper.get_id(dex.lower())
        if dex_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        symbol_id = symbol_mapper.get_id(symbol.upper())
        if symbol_id is None:
            raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
        
        # Query rate with additional details
        query = """
            SELECT 
                lfr.funding_rate,
                lfr.next_funding_time,
                lfr.updated_at,
                ds.volume_24h,
                ds.open_interest_usd
            FROM latest_funding_rates lfr
            LEFT JOIN dex_symbols ds ON ds.dex_id = lfr.dex_id AND ds.symbol_id = lfr.symbol_id
            WHERE lfr.dex_id = :dex_id AND lfr.symbol_id = :symbol_id
        """
        
        row = await database.fetch_one(query, values={"dex_id": dex_id, "symbol_id": symbol_id})
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No funding rate found for {symbol} on {dex}"
            )
        
        # Calculate annualized rate
        funding_rate = row['funding_rate']
        annualized_rate = float(funding_rate) * 365 * 3
        
        return {
            "dex_name": dex,
            "symbol": symbol,
            "funding_rate": float(funding_rate),
            "annualized_rate": annualized_rate,
            "next_funding_time": row['next_funding_time'].isoformat() if row['next_funding_time'] else None,
            "timestamp": row['updated_at'].isoformat(),
            "volume_24h": float(row['volume_24h']) if row['volume_24h'] else None,
            "open_interest_usd": float(row['open_interest_usd']) if row['open_interest_usd'] else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching rate for {symbol} on {dex}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/funding-rates/{dex}/{symbol}")
async def get_historical_funding_rates(
    dex: str,
    symbol: str,
    analyzer: HistoricalAnalyzer = Depends(get_historical_analyzer),
    period: Optional[str] = Query("7d", description="Period (e.g., '7d', '30d', '90d')"),
    limit: Optional[int] = Query(1000, ge=1, le=10000, description="Max data points")
) -> FundingRateHistory:
    """
    Get historical funding rates for a symbol on a DEX
    
    Returns time-series data with statistics
    """
    try:
        # Parse period
        period_days = _parse_period(period)
        
        # Use historical analyzer (injected dependency)
        history = await analyzer.get_funding_rate_history(
            symbol=symbol.upper(),
            dex_name=dex.lower(),
            period_days=period_days,
            limit=limit
        )
        
        return history
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching historical rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/funding-rates/{symbol}")
async def get_funding_rate_stats(
    symbol: str,
    analyzer: HistoricalAnalyzer = Depends(get_historical_analyzer),
    dex: Optional[str] = Query(None, description="Specific DEX or all DEXs"),
    period: Optional[str] = Query("30d", description="Analysis period (e.g., '7d', '30d', '90d')")
) -> FundingRateStats:
    """
    Get statistical analysis of funding rates
    
    Includes: average, median, volatility, percentiles, APY, etc.
    """
    try:
        # Parse period
        period_days = _parse_period(period)
        
        # Use historical analyzer (injected dependency)
        stats = await analyzer.get_funding_rate_stats(
            symbol=symbol.upper(),
            dex_name=dex.lower() if dex else None,
            period_days=period_days
        )
        
        return stats
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error calculating stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _parse_period(period: str) -> int:
    """
    Parse period string to days
    
    Args:
        period: Period string (e.g., '7d', '30d', '90d')
        
    Returns:
        Number of days
        
    Raises:
        ValueError: If period format is invalid
    """
    period = period.lower().strip()
    
    if period.endswith('d'):
        try:
            return int(period[:-1])
        except ValueError:
            raise ValueError(f"Invalid period format: {period}")
    else:
        raise ValueError(f"Period must end with 'd' (days): {period}")