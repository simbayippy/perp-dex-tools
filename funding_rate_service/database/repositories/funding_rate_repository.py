"""
Funding Rate Repository - handles all funding rate database operations
"""

from typing import Optional, List, Dict, Any
from databases import Database
from datetime import datetime, timedelta
from decimal import Decimal

from funding_rate_service.utils.logger import logger


class FundingRateRepository:
    """Repository for Funding Rate data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def insert(
        self,
        dex_id: int,
        symbol_id: int,
        funding_rate: Decimal,
        next_funding_time: Optional[datetime] = None,
        index_price: Optional[Decimal] = None,
        mark_price: Optional[Decimal] = None,
        open_interest_usd: Optional[Decimal] = None,
        volume_24h: Optional[Decimal] = None,
        collection_latency_ms: Optional[int] = None
    ) -> None:
        """
        Insert a new funding rate record
        
        Args:
            dex_id: DEX ID
            symbol_id: Symbol ID
            funding_rate: Funding rate as decimal
            next_funding_time: Next funding timestamp
            index_price: Index price
            mark_price: Mark price
            open_interest_usd: Open interest in USD
            volume_24h: 24h volume
            collection_latency_ms: Collection latency in milliseconds
        """
        query = """
            INSERT INTO funding_rates (
                time, dex_id, symbol_id, funding_rate,
                next_funding_time, index_price, mark_price,
                open_interest_usd, volume_24h, collection_latency_ms
            )
            VALUES (
                NOW(), :dex_id, :symbol_id, :funding_rate,
                :next_funding_time, :index_price, :mark_price,
                :open_interest_usd, :volume_24h, :collection_latency_ms
            )
        """
        
        await self.db.execute(
            query,
            {
                "dex_id": dex_id,
                "symbol_id": symbol_id,
                "funding_rate": funding_rate,
                "next_funding_time": next_funding_time,
                "index_price": index_price,
                "mark_price": mark_price,
                "open_interest_usd": open_interest_usd,
                "volume_24h": volume_24h,
                "collection_latency_ms": collection_latency_ms
            }
        )
    
    async def get_latest_all(self) -> List[Dict[str, Any]]:
        """
        Get latest funding rates for all DEX-symbol combinations
        
        Returns:
            List of latest rates with DEX and symbol names
        """
        query = """
            SELECT DISTINCT ON (fr.dex_id, fr.symbol_id)
                fr.time,
                fr.dex_id,
                d.name as dex_name,
                fr.symbol_id,
                s.symbol,
                fr.funding_rate,
                fr.next_funding_time,
                fr.index_price,
                fr.mark_price,
                fr.open_interest_usd,
                fr.volume_24h
            FROM funding_rates fr
            JOIN dexes d ON fr.dex_id = d.id
            JOIN symbols s ON fr.symbol_id = s.id
            WHERE d.is_active = TRUE
            ORDER BY fr.dex_id, fr.symbol_id, fr.time DESC
        """
        return await self.db.fetch_all(query)
    
    async def get_latest_by_dex(self, dex_name: str) -> List[Dict[str, Any]]:
        """Get latest funding rates for a specific DEX"""
        query = """
            SELECT DISTINCT ON (fr.symbol_id)
                fr.time,
                s.symbol,
                fr.funding_rate,
                fr.next_funding_time,
                fr.index_price,
                fr.mark_price,
                fr.open_interest_usd,
                fr.volume_24h
            FROM funding_rates fr
            JOIN symbols s ON fr.symbol_id = s.id
            JOIN dexes d ON fr.dex_id = d.id
            WHERE d.name = :dex_name AND d.is_active = TRUE
            ORDER BY fr.symbol_id, fr.time DESC
        """
        return await self.db.fetch_all(query, {"dex_name": dex_name})
    
    async def get_latest_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Get latest funding rates across all DEXs for a specific symbol"""
        query = """
            SELECT DISTINCT ON (fr.dex_id)
                fr.time,
                d.name as dex_name,
                fr.funding_rate,
                fr.next_funding_time,
                fr.index_price,
                fr.mark_price,
                fr.open_interest_usd,
                fr.volume_24h
            FROM funding_rates fr
            JOIN dexes d ON fr.dex_id = d.id
            JOIN symbols s ON fr.symbol_id = s.id
            WHERE s.symbol = :symbol AND d.is_active = TRUE
            ORDER BY fr.dex_id, fr.time DESC
        """
        return await self.db.fetch_all(query, {"symbol": symbol.upper()})
    
    async def get_latest_specific(
        self, 
        dex_name: str, 
        symbol: str
    ) -> Optional[Dict[str, Any]]:
        """Get latest funding rate for specific DEX and symbol"""
        query = """
            SELECT 
                fr.time,
                fr.funding_rate,
                fr.next_funding_time,
                fr.index_price,
                fr.mark_price,
                fr.open_interest_usd,
                fr.volume_24h
            FROM funding_rates fr
            JOIN dexes d ON fr.dex_id = d.id
            JOIN symbols s ON fr.symbol_id = s.id
            WHERE d.name = :dex_name 
              AND s.symbol = :symbol
              AND d.is_active = TRUE
            ORDER BY fr.time DESC
            LIMIT 1
        """
        return await self.db.fetch_one(
            query, 
            {"dex_name": dex_name, "symbol": symbol.upper()}
        )
    
    async def get_history(
        self,
        dex_name: str,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get historical funding rates
        
        Args:
            dex_name: DEX name
            symbol: Symbol
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of records
            
        Returns:
            List of historical rates
        """
        if not start_time:
            start_time = datetime.utcnow() - timedelta(days=30)
        if not end_time:
            end_time = datetime.utcnow()
        
        query = """
            SELECT 
                fr.time,
                fr.funding_rate,
                fr.index_price,
                fr.mark_price,
                fr.open_interest_usd,
                fr.volume_24h
            FROM funding_rates fr
            JOIN dexes d ON fr.dex_id = d.id
            JOIN symbols s ON fr.symbol_id = s.id
            WHERE d.name = :dex_name 
              AND s.symbol = :symbol
              AND fr.time BETWEEN :start_time AND :end_time
            ORDER BY fr.time DESC
            LIMIT :limit
        """
        return await self.db.fetch_all(
            query,
            {
                "dex_name": dex_name,
                "symbol": symbol.upper(),
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit
            }
        )
    
    async def get_stats(
        self,
        dex_name: str,
        symbol: str,
        days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Get statistical analysis of funding rates
        
        Args:
            dex_name: DEX name
            symbol: Symbol
            days: Number of days to analyze
            
        Returns:
            Statistics dictionary
        """
        query = """
            SELECT 
                COUNT(*) as sample_count,
                AVG(funding_rate) as avg_rate,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY funding_rate) as median_rate,
                STDDEV(funding_rate) as std_dev,
                MIN(funding_rate) as min_rate,
                MAX(funding_rate) as max_rate,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY funding_rate) as percentile_25,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY funding_rate) as percentile_75,
                SUM(CASE WHEN funding_rate > 0 THEN 1 ELSE 0 END)::float / COUNT(*) as positive_rate_freq
            FROM funding_rates fr
            JOIN dexes d ON fr.dex_id = d.id
            JOIN symbols s ON fr.symbol_id = s.id
            WHERE d.name = :dex_name 
              AND s.symbol = :symbol
              AND fr.time >= NOW() - INTERVAL ':days days'
        """
        return await self.db.fetch_one(
            query,
            {"dex_name": dex_name, "symbol": symbol.upper(), "days": days}
        )
    
    async def upsert_latest(
        self,
        dex_id: int,
        symbol_id: int,
        funding_rate: Decimal,
        next_funding_time: Optional[datetime] = None
    ) -> None:
        """
        Upsert latest funding rate (for caching latest rates)
        
        This updates the latest_funding_rates table for fast API responses.
        """
        query = """
            INSERT INTO latest_funding_rates (dex_id, symbol_id, funding_rate, next_funding_time, updated_at)
            VALUES (:dex_id, :symbol_id, :funding_rate, :next_funding_time, NOW())
            ON CONFLICT (dex_id, symbol_id) 
            DO UPDATE SET 
                funding_rate = EXCLUDED.funding_rate,
                next_funding_time = EXCLUDED.next_funding_time,
                updated_at = NOW()
        """
        await self.db.execute(
            query,
            {
                "dex_id": dex_id,
                "symbol_id": symbol_id,
                "funding_rate": funding_rate,
                "next_funding_time": next_funding_time
            }
        )

