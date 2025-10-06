"""
DEXes API Routes

Endpoints for accessing DEX metadata, supported symbols, and health status.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from datetime import datetime

from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


router = APIRouter()


@router.get("/dexes", response_model=Dict[str, Any])
async def get_all_dexes() -> Dict[str, Any]:
    """
    Get all DEX metadata
    
    Returns information about all supported DEXs including:
    - Fee structures
    - Health status
    - Supported symbols count
    """
    try:
        query = """
            SELECT 
                d.id,
                d.name,
                d.display_name,
                d.is_active,
                d.maker_fee_percent,
                d.taker_fee_percent,
                d.has_fee_tiers,
                d.last_successful_fetch,
                d.last_error,
                d.consecutive_errors,
                COUNT(DISTINCT ds.symbol_id) as supported_symbols_count
            FROM dexes d
            LEFT JOIN dex_symbols ds ON ds.dex_id = d.id AND ds.is_active = TRUE
            GROUP BY d.id, d.name, d.display_name, d.is_active, 
                     d.maker_fee_percent, d.taker_fee_percent, d.has_fee_tiers,
                     d.last_successful_fetch, d.last_error, d.consecutive_errors
            ORDER BY d.name
        """
        
        rows = await database.fetch_all(query)
        
        dexes = []
        for row in rows:
            dexes.append({
                "name": row['name'],
                "display_name": row['display_name'],
                "is_active": row['is_active'],
                "fee_structure": {
                    "maker_fee_percent": float(row['maker_fee_percent']),
                    "taker_fee_percent": float(row['taker_fee_percent']),
                    "has_fee_tiers": row['has_fee_tiers']
                },
                "supported_symbols_count": row['supported_symbols_count'],
                "last_successful_fetch": row['last_successful_fetch'].isoformat() if row['last_successful_fetch'] else None,
                "last_error": row['last_error'].isoformat() if row['last_error'] else None,
                "consecutive_errors": row['consecutive_errors'],
                "is_healthy": row['consecutive_errors'] == 0 and row['is_active']
            })
        
        return {
            "dexes": dexes,
            "count": len(dexes)
        }
        
    except Exception as e:
        logger.error(f"Error fetching DEXes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dexes/{dex}", response_model=Dict[str, Any])
async def get_dex_metadata(dex: str) -> Dict[str, Any]:
    """
    Get metadata for a specific DEX
    
    Returns detailed information including fee structure and health status
    """
    try:
        # Verify DEX exists
        dex_id = dex_mapper.get_id(dex.lower())
        if dex_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        query = """
            SELECT 
                d.name,
                d.display_name,
                d.api_base_url,
                d.is_active,
                d.supports_websocket,
                d.maker_fee_percent,
                d.taker_fee_percent,
                d.has_fee_tiers,
                d.collection_interval_seconds,
                d.rate_limit_per_minute,
                d.last_successful_fetch,
                d.last_error,
                d.consecutive_errors,
                d.created_at,
                d.updated_at,
                COUNT(DISTINCT ds.symbol_id) as supported_symbols_count
            FROM dexes d
            LEFT JOIN dex_symbols ds ON ds.dex_id = d.id AND ds.is_active = TRUE
            WHERE d.id = $1
            GROUP BY d.id, d.name, d.display_name, d.api_base_url, d.is_active,
                     d.supports_websocket, d.maker_fee_percent, d.taker_fee_percent,
                     d.has_fee_tiers, d.collection_interval_seconds, d.rate_limit_per_minute,
                     d.last_successful_fetch, d.last_error, d.consecutive_errors,
                     d.created_at, d.updated_at
        """
        
        row = await database.fetch_one(query, dex_id)
        
        if not row:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        return {
            "name": row['name'],
            "display_name": row['display_name'],
            "api_base_url": row['api_base_url'],
            "is_active": row['is_active'],
            "supports_websocket": row['supports_websocket'],
            "fee_structure": {
                "maker_fee_percent": float(row['maker_fee_percent']),
                "taker_fee_percent": float(row['taker_fee_percent']),
                "has_fee_tiers": row['has_fee_tiers']
            },
            "collection_interval_seconds": row['collection_interval_seconds'],
            "rate_limit_per_minute": row['rate_limit_per_minute'],
            "supported_symbols_count": row['supported_symbols_count'],
            "health": {
                "last_successful_fetch": row['last_successful_fetch'].isoformat() if row['last_successful_fetch'] else None,
                "last_error": row['last_error'].isoformat() if row['last_error'] else None,
                "consecutive_errors": row['consecutive_errors'],
                "is_healthy": row['consecutive_errors'] == 0 and row['is_active']
            },
            "created_at": row['created_at'].isoformat(),
            "updated_at": row['updated_at'].isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching DEX metadata for {dex}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dexes/{dex}/symbols", response_model=Dict[str, Any])
async def get_dex_symbols(dex: str) -> Dict[str, Any]:
    """
    Get all symbols supported by a DEX
    
    Returns list of symbols with market data (volume, OI, spreads)
    """
    try:
        # Verify DEX exists
        dex_id = dex_mapper.get_id(dex.lower())
        if dex_id is None:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        query = """
            SELECT 
                s.symbol,
                ds.dex_symbol_format,
                ds.is_active,
                ds.min_order_size,
                ds.volume_24h,
                ds.open_interest_usd,
                ds.spread_bps,
                ds.last_updated
            FROM dex_symbols ds
            JOIN symbols s ON ds.symbol_id = s.id
            WHERE ds.dex_id = $1
            ORDER BY ds.volume_24h DESC NULLS LAST, s.symbol
        """
        
        rows = await database.fetch_all(query, dex_id)
        
        symbols = []
        for row in rows:
            symbols.append({
                "symbol": row['symbol'],
                "dex_symbol_format": row['dex_symbol_format'],
                "is_active": row['is_active'],
                "min_order_size": float(row['min_order_size']) if row['min_order_size'] else None,
                "volume_24h": float(row['volume_24h']) if row['volume_24h'] else None,
                "open_interest_usd": float(row['open_interest_usd']) if row['open_interest_usd'] else None,
                "spread_bps": row['spread_bps'],
                "last_updated": row['last_updated'].isoformat() if row['last_updated'] else None
            })
        
        return {
            "dex_name": dex,
            "symbols": symbols,
            "count": len(symbols)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching symbols for {dex}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

