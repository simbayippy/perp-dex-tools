"""
Opportunity Repository - handles all arbitrage opportunity database operations
"""

from typing import Optional, List, Dict, Any
from databases import Database
from datetime import datetime, timedelta
from decimal import Decimal

from funding_rate_service.models.filters import OpportunityFilter
from funding_rate_service.utils.logger import logger


class OpportunityRepository:
    """Repository for Arbitrage Opportunity data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def insert(
        self,
        symbol_id: int,
        long_dex_id: int,
        short_dex_id: int,
        long_rate: Decimal,
        short_rate: Decimal,
        divergence: Decimal,
        estimated_fees: Decimal,
        net_profit_percent: Decimal,
        annualized_apy: Optional[Decimal] = None,
        long_dex_volume_24h: Optional[Decimal] = None,
        short_dex_volume_24h: Optional[Decimal] = None,
        long_dex_oi_usd: Optional[Decimal] = None,
        short_dex_oi_usd: Optional[Decimal] = None,
        long_dex_spread_bps: Optional[int] = None,
        short_dex_spread_bps: Optional[int] = None,
        valid_until: Optional[datetime] = None,
        metadata: Optional[Dict] = None
    ) -> int:
        """
        Insert a new arbitrage opportunity
        
        Returns:
            Opportunity ID
        """
        # Calculate derived metrics
        min_volume = None
        if long_dex_volume_24h and short_dex_volume_24h:
            min_volume = min(long_dex_volume_24h, short_dex_volume_24h)
        
        min_oi = None
        max_oi = None
        oi_ratio = None
        if long_dex_oi_usd and short_dex_oi_usd:
            min_oi = min(long_dex_oi_usd, short_dex_oi_usd)
            max_oi = max(long_dex_oi_usd, short_dex_oi_usd)
            if short_dex_oi_usd > 0:
                oi_ratio = long_dex_oi_usd / short_dex_oi_usd
        
        avg_spread = None
        if long_dex_spread_bps is not None and short_dex_spread_bps is not None:
            avg_spread = (long_dex_spread_bps + short_dex_spread_bps) // 2
        
        query = """
            INSERT INTO opportunities (
                symbol_id, long_dex_id, short_dex_id,
                long_rate, short_rate, divergence,
                estimated_fees, net_profit_percent, annualized_apy,
                long_dex_volume_24h, short_dex_volume_24h, min_volume_24h,
                long_dex_oi_usd, short_dex_oi_usd, min_oi_usd, max_oi_usd, oi_ratio,
                long_dex_spread_bps, short_dex_spread_bps, avg_spread_bps,
                discovered_at, valid_until, metadata
            )
            VALUES (
                :symbol_id, :long_dex_id, :short_dex_id,
                :long_rate, :short_rate, :divergence,
                :estimated_fees, :net_profit_percent, :annualized_apy,
                :long_dex_volume_24h, :short_dex_volume_24h, :min_volume_24h,
                :long_dex_oi_usd, :short_dex_oi_usd, :min_oi_usd, :max_oi_usd, :oi_ratio,
                :long_dex_spread_bps, :short_dex_spread_bps, :avg_spread_bps,
                NOW(), :valid_until, :metadata
            )
            RETURNING id
        """
        
        return await self.db.fetch_val(
            query,
            {
                "symbol_id": symbol_id,
                "long_dex_id": long_dex_id,
                "short_dex_id": short_dex_id,
                "long_rate": long_rate,
                "short_rate": short_rate,
                "divergence": divergence,
                "estimated_fees": estimated_fees,
                "net_profit_percent": net_profit_percent,
                "annualized_apy": annualized_apy,
                "long_dex_volume_24h": long_dex_volume_24h,
                "short_dex_volume_24h": short_dex_volume_24h,
                "min_volume_24h": min_volume,
                "long_dex_oi_usd": long_dex_oi_usd,
                "short_dex_oi_usd": short_dex_oi_usd,
                "min_oi_usd": min_oi,
                "max_oi_usd": max_oi,
                "oi_ratio": oi_ratio,
                "long_dex_spread_bps": long_dex_spread_bps,
                "short_dex_spread_bps": short_dex_spread_bps,
                "avg_spread_bps": avg_spread,
                "valid_until": valid_until,
                "metadata": metadata
            }
        )
    
    async def find_opportunities(
        self,
        filters: OpportunityFilter
    ) -> List[Dict[str, Any]]:
        """
        Find opportunities based on filters
        
        Args:
            filters: OpportunityFilter with all filter criteria
            
        Returns:
            List of opportunities with DEX and symbol names
        """
        # Build dynamic query
        where_clauses = []
        params = {}
        
        # Base query
        query = """
            SELECT 
                o.id,
                s.symbol,
                d1.name as long_dex,
                d2.name as short_dex,
                o.long_rate,
                o.short_rate,
                o.divergence,
                o.estimated_fees,
                o.net_profit_percent,
                o.annualized_apy,
                o.long_dex_volume_24h,
                o.short_dex_volume_24h,
                o.min_volume_24h,
                o.long_dex_oi_usd,
                o.short_dex_oi_usd,
                o.min_oi_usd,
                o.max_oi_usd,
                o.oi_ratio,
                o.long_dex_spread_bps,
                o.short_dex_spread_bps,
                o.avg_spread_bps,
                o.discovered_at,
                o.valid_until,
                o.metadata
            FROM opportunities o
            JOIN symbols s ON o.symbol_id = s.id
            JOIN dexes d1 ON o.long_dex_id = d1.id
            JOIN dexes d2 ON o.short_dex_id = d2.id
            WHERE 1=1
        """
        
        # Apply filters
        if filters.symbol:
            where_clauses.append("s.symbol = :symbol")
            params["symbol"] = filters.symbol.upper()
        
        if filters.long_dex:
            where_clauses.append("d1.name = :long_dex")
            params["long_dex"] = filters.long_dex.lower()
        
        if filters.short_dex:
            where_clauses.append("d2.name = :short_dex")
            params["short_dex"] = filters.short_dex.lower()
        
        if filters.include_dexes:
            dex_list = [d.lower() for d in filters.include_dexes]
            where_clauses.append("(d1.name = ANY(:include_dexes) AND d2.name = ANY(:include_dexes))")
            params["include_dexes"] = dex_list
        
        if filters.exclude_dexes:
            dex_list = [d.lower() for d in filters.exclude_dexes]
            where_clauses.append("d1.name != ALL(:exclude_dexes) AND d2.name != ALL(:exclude_dexes)")
            params["exclude_dexes"] = dex_list

        if filters.required_dex:
            where_clauses.append("(d1.name = :required_dex OR d2.name = :required_dex)")
            params["required_dex"] = filters.required_dex.lower()
        
        if filters.min_divergence:
            where_clauses.append("o.divergence >= :min_divergence")
            params["min_divergence"] = filters.min_divergence
        
        if filters.min_profit_percent:
            where_clauses.append("o.net_profit_percent >= :min_profit")
            params["min_profit"] = filters.min_profit_percent
        
        if filters.min_apy:
            where_clauses.append("o.annualized_apy >= :min_apy")
            params["min_apy"] = filters.min_apy
        
        if filters.min_volume_24h:
            where_clauses.append("o.min_volume_24h >= :min_volume")
            params["min_volume"] = filters.min_volume_24h
        
        if filters.max_volume_24h:
            where_clauses.append("o.min_volume_24h <= :max_volume")
            params["max_volume"] = filters.max_volume_24h
        
        # OI filters (key for low OI farming!)
        if filters.min_oi_usd:
            where_clauses.append("o.min_oi_usd >= :min_oi")
            params["min_oi"] = filters.min_oi_usd
        
        if filters.max_oi_usd:
            if filters.required_dex:
                where_clauses.append(
                    "((d1.name = :required_dex AND o.long_dex_oi_usd <= :required_max_oi) "
                    "OR (d2.name = :required_dex AND o.short_dex_oi_usd <= :required_max_oi))"
                )
                params["required_max_oi"] = filters.max_oi_usd
            else:
                where_clauses.append("o.min_oi_usd <= :max_oi")
                params["max_oi"] = filters.max_oi_usd
        
        if filters.oi_ratio_min:
            where_clauses.append("o.oi_ratio >= :oi_ratio_min")
            params["oi_ratio_min"] = filters.oi_ratio_min
        
        if filters.oi_ratio_max:
            where_clauses.append("o.oi_ratio <= :oi_ratio_max")
            params["oi_ratio_max"] = filters.oi_ratio_max
        
        if filters.oi_imbalance:
            if filters.oi_imbalance == "long_heavy":
                where_clauses.append("o.oi_ratio > 1.2")
            elif filters.oi_imbalance == "short_heavy":
                where_clauses.append("o.oi_ratio < 0.8")
            elif filters.oi_imbalance == "balanced":
                where_clauses.append("o.oi_ratio BETWEEN 0.8 AND 1.2")
        
        if filters.max_spread_bps:
            where_clauses.append("o.avg_spread_bps <= :max_spread")
            params["max_spread"] = filters.max_spread_bps
        
        # Add WHERE clauses
        if where_clauses:
            query += " AND " + " AND ".join(where_clauses)
        
        # Add sorting
        valid_sort_fields = {
            "net_profit_percent": "o.net_profit_percent",
            "divergence": "o.divergence",
            "annualized_apy": "o.annualized_apy",
            "min_oi_usd": "o.min_oi_usd",
            "min_volume_24h": "o.min_volume_24h"
        }
        sort_field = valid_sort_fields.get(filters.sort_by, "o.net_profit_percent")
        sort_order = "DESC" if filters.sort_desc else "ASC"
        
        query += f" ORDER BY {sort_field} {sort_order}"
        
        # Add limit
        query += " LIMIT :limit"
        params["limit"] = filters.limit
        
        return await self.db.fetch_all(query, params)
    
    async def get_best_opportunity(
        self,
        filters: OpportunityFilter
    ) -> Optional[Dict[str, Any]]:
        """Get the single best opportunity based on filters"""
        filters.limit = 1
        results = await self.find_opportunities(filters)
        return results[0] if results else None
    
    async def get_by_symbol(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get opportunities for a specific symbol"""
        query = """
            SELECT 
                o.*,
                s.symbol,
                d1.name as long_dex,
                d2.name as short_dex
            FROM opportunities o
            JOIN symbols s ON o.symbol_id = s.id
            JOIN dexes d1 ON o.long_dex_id = d1.id
            JOIN dexes d2 ON o.short_dex_id = d2.id
            WHERE s.symbol = :symbol
            ORDER BY o.net_profit_percent DESC
            LIMIT :limit
        """
        return await self.db.fetch_all(
            query,
            {"symbol": symbol.upper(), "limit": limit}
        )
    
    async def cleanup_old_opportunities(self, hours: int = 24) -> int:
        """
        Delete opportunities older than specified hours
        
        Args:
            hours: Age threshold in hours
            
        Returns:
            Number of deleted records
        """
        query = """
            DELETE FROM opportunities
            WHERE discovered_at < NOW() - INTERVAL ':hours hours'
        """
        result = await self.db.execute(query, {"hours": hours})
        logger.info(f"Cleaned up {result} old opportunities (older than {hours}h)")
        return result
    
    async def get_count(self, filters: Optional[OpportunityFilter] = None) -> int:
        """Get total count of opportunities matching filters"""
        if not filters:
            query = "SELECT COUNT(*) FROM opportunities"
            return await self.db.fetch_val(query)
        
        # Build filtered count query (similar to find_opportunities but just count)
        # For simplicity, reuse the find query but replace SELECT with COUNT
        # In production, you'd optimize this
        query = "SELECT COUNT(*) FROM opportunities o JOIN symbols s ON o.symbol_id = s.id WHERE 1=1"
        
        if filters.symbol:
            query += f" AND s.symbol = '{filters.symbol.upper()}'"
        
        return await self.db.fetch_val(query)
