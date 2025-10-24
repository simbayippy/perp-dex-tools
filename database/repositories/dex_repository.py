"""
DEX Repository - handles all DEX-related database operations
"""

from typing import Optional, List, Dict, Any
from databases import Database
from datetime import datetime

from funding_rate_service.models.dex import DEXMetadata, DEXFeeStructure
from funding_rate_service.utils.logger import logger


class DEXRepository:
    """Repository for DEX data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def get_all(self) -> List[Dict[str, Any]]:
        """Get all DEXs"""
        query = "SELECT * FROM dexes ORDER BY id"
        return await self.db.fetch_all(query)
    
    async def get_active(self) -> List[Dict[str, Any]]:
        """Get all active DEXs"""
        query = "SELECT * FROM dexes WHERE is_active = TRUE ORDER BY id"
        return await self.db.fetch_all(query)
    
    async def get_by_id(self, dex_id: int) -> Optional[Dict[str, Any]]:
        """Get DEX by ID"""
        query = "SELECT * FROM dexes WHERE id = :id"
        return await self.db.fetch_one(query, {"id": dex_id})
    
    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get DEX by name"""
        query = "SELECT * FROM dexes WHERE name = :name"
        return await self.db.fetch_one(query, {"name": name.lower()})
    
    async def get_metadata(self, dex_name: str) -> Optional[DEXMetadata]:
        """
        Get DEX metadata as a Pydantic model
        
        Args:
            dex_name: DEX name
            
        Returns:
            DEXMetadata or None if not found
        """
        row = await self.get_by_name(dex_name)
        if not row:
            return None
        
        # Convert to DEXMetadata
        fee_structure = DEXFeeStructure(
            maker_fee_percent=row['maker_fee_percent'],
            taker_fee_percent=row['taker_fee_percent'],
            has_fee_tiers=row['has_fee_tiers'],
            fee_tiers=row['fee_metadata'].get('tiers') if row['fee_metadata'] else None
        )
        
        return DEXMetadata(
            id=row['id'],
            name=row['name'],
            display_name=row['display_name'],
            api_base_url=row['api_base_url'],
            websocket_url=row['websocket_url'],
            is_active=row['is_active'],
            supports_websocket=row['supports_websocket'],
            fee_structure=fee_structure,
            collection_interval_seconds=row['collection_interval_seconds'],
            rate_limit_per_minute=row['rate_limit_per_minute'],
            last_successful_fetch=row['last_successful_fetch'],
            last_error=row['last_error'],
            consecutive_errors=row['consecutive_errors'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
    
    async def update_last_fetch(
        self, 
        dex_id: int, 
        success: bool, 
        error_message: Optional[str] = None
    ) -> None:
        """
        Update last fetch timestamp and error counters
        
        Args:
            dex_id: DEX ID
            success: Whether the fetch was successful
            error_message: Error message if failed
        """
        if success:
            query = """
                UPDATE dexes 
                SET last_successful_fetch = NOW(),
                    consecutive_errors = 0,
                    updated_at = NOW()
                WHERE id = :id
            """
            await self.db.execute(query, {"id": dex_id})
            logger.debug(f"Updated DEX {dex_id} last_successful_fetch")
        else:
            query = """
                UPDATE dexes 
                SET last_error = NOW(),
                    consecutive_errors = consecutive_errors + 1,
                    updated_at = NOW()
                WHERE id = :id
            """
            await self.db.execute(query, {"id": dex_id})
            logger.warning(f"DEX {dex_id} fetch failed: {error_message}")
    
    async def update_fees(
        self,
        dex_name: str,
        maker_fee: float,
        taker_fee: float,
        has_tiers: bool = False,
        fee_metadata: Optional[Dict] = None
    ) -> None:
        """Update DEX fee structure"""
        query = """
            UPDATE dexes 
            SET maker_fee_percent = :maker_fee,
                taker_fee_percent = :taker_fee,
                has_fee_tiers = :has_tiers,
                fee_metadata = :fee_metadata,
                updated_at = NOW()
            WHERE name = :name
        """
        await self.db.execute(
            query,
            {
                "name": dex_name,
                "maker_fee": maker_fee,
                "taker_fee": taker_fee,
                "has_tiers": has_tiers,
                "fee_metadata": fee_metadata
            }
        )
        logger.info(f"Updated fees for DEX: {dex_name}")
    
    async def get_health_stats(self, dex_id: int, hours: int = 24) -> Dict[str, Any]:
        """
        Get health statistics for a DEX
        
        Args:
            dex_id: DEX ID
            hours: Number of hours to look back
            
        Returns:
            Dictionary with health stats
        """
        query = """
            SELECT 
                COUNT(*) as total_collections,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_collections,
                AVG(CASE WHEN status = 'success' 
                    THEN EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000 
                    ELSE NULL END) as avg_latency_ms
            FROM collection_logs
            WHERE dex_id = :dex_id
              AND started_at >= NOW() - INTERVAL ':hours hours'
        """
        result = await self.db.fetch_one(
            query, 
            {"dex_id": dex_id, "hours": hours}
        )
        
        total = result['total_collections'] or 0
        successful = result['successful_collections'] or 0
        
        return {
            "total_collections": total,
            "successful_collections": successful,
            "error_rate_percent": (
                (total - successful) / total * 100 if total > 0 else 0
            ),
            "avg_collection_latency_ms": result['avg_latency_ms'] or 0
        }

