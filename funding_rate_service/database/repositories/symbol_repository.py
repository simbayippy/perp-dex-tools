"""
Symbol Repository - handles all symbol-related database operations
"""

from typing import Optional, List, Dict, Any
from databases import Database
from datetime import datetime

from funding_rate_service.utils.logger import logger


class SymbolRepository:
    """Repository for Symbol data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def get_all(self) -> List[Dict[str, Any]]:
        """Get all symbols"""
        query = "SELECT * FROM symbols ORDER BY id"
        return await self.db.fetch_all(query)
    
    async def get_active(self) -> List[Dict[str, Any]]:
        """Get all active symbols"""
        query = "SELECT * FROM symbols WHERE is_active = TRUE ORDER BY symbol"
        return await self.db.fetch_all(query)
    
    async def get_by_id(self, symbol_id: int) -> Optional[Dict[str, Any]]:
        """Get symbol by ID"""
        query = "SELECT * FROM symbols WHERE id = :id"
        return await self.db.fetch_one(query, {"id": symbol_id})
    
    async def get_by_name(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol by name"""
        query = "SELECT * FROM symbols WHERE symbol = :symbol"
        return await self.db.fetch_one(query, {"symbol": symbol.upper()})
    
    async def get_or_create(self, symbol: str, category: str = "crypto") -> int:
        """
        Get symbol ID, creating if doesn't exist (dynamic symbol discovery!)
        
        Args:
            symbol: Symbol name (e.g., "BTC")
            category: Symbol category (default: "crypto")
            
        Returns:
            Symbol ID
        """
        # Try to get existing
        result = await self.get_by_name(symbol)
        if result:
            return result['id']
        
        # Create new symbol
        query = """
            INSERT INTO symbols (symbol, category, first_seen)
            VALUES (:symbol, :category, NOW())
            ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol
            RETURNING id
        """
        new_id = await self.db.fetch_val(
            query, 
            {"symbol": symbol.upper(), "category": category}
        )
        
        logger.info(f"New symbol discovered: {symbol} (ID: {new_id})")
        return new_id
    
    async def get_dex_symbols(self, dex_id: int) -> List[Dict[str, Any]]:
        """
        Get all symbols available on a specific DEX
        
        Args:
            dex_id: DEX ID
            
        Returns:
            List of DEX symbol records with symbol info
        """
        query = """
            SELECT ds.*, s.symbol, s.display_name
            FROM dex_symbols ds
            JOIN symbols s ON ds.symbol_id = s.id
            WHERE ds.dex_id = :dex_id AND ds.is_active = TRUE
            ORDER BY ds.volume_24h DESC NULLS LAST
        """
        return await self.db.fetch_all(query, {"dex_id": dex_id})
    
    async def get_or_create_dex_symbol(
        self,
        dex_id: int,
        symbol_id: int,
        dex_symbol_format: str
    ) -> int:
        """
        Get or create dex_symbol mapping
        
        Args:
            dex_id: DEX ID
            symbol_id: Symbol ID
            dex_symbol_format: DEX-specific format (e.g., "BTC-PERP")
            
        Returns:
            dex_symbol ID
        """
        # Check if exists
        query = """
            SELECT id FROM dex_symbols 
            WHERE dex_id = :dex_id AND symbol_id = :symbol_id
        """
        result = await self.db.fetch_one(
            query,
            {"dex_id": dex_id, "symbol_id": symbol_id}
        )
        
        if result:
            return result['id']
        
        # Create new
        insert_query = """
            INSERT INTO dex_symbols (dex_id, symbol_id, dex_symbol_format, updated_at)
            VALUES (:dex_id, :symbol_id, :format, NOW())
            ON CONFLICT (dex_id, symbol_id) DO UPDATE 
            SET dex_symbol_format = EXCLUDED.dex_symbol_format,
                updated_at = NOW()
            RETURNING id
        """
        new_id = await self.db.fetch_val(
            insert_query,
            {
                "dex_id": dex_id,
                "symbol_id": symbol_id,
                "format": dex_symbol_format
            }
        )
        
        logger.debug(f"Created dex_symbol: DEX {dex_id}, Symbol {symbol_id}, Format: {dex_symbol_format}")
        return new_id
    
    async def update_dex_symbol_metrics(
        self,
        dex_id: int,
        symbol_id: int,
        volume_24h: Optional[float] = None,
        open_interest_usd: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None
    ) -> None:
        """
        Update market metrics for a DEX symbol
        
        Args:
            dex_id: DEX ID
            symbol_id: Symbol ID
            volume_24h: 24h volume in USD
            open_interest_usd: Open interest in USD
            best_bid: Best bid price
            best_ask: Best ask price
        """
        # Build dynamic update query
        updates = []
        params = {"dex_id": dex_id, "symbol_id": symbol_id}
        
        if volume_24h is not None:
            updates.append("volume_24h = :volume_24h")
            params["volume_24h"] = volume_24h
        
        if open_interest_usd is not None:
            updates.append("open_interest_usd = :open_interest_usd")
            params["open_interest_usd"] = open_interest_usd
        
        if best_bid is not None:
            updates.append("best_bid = :best_bid")
            params["best_bid"] = best_bid
        
        if best_ask is not None:
            updates.append("best_ask = :best_ask")
            params["best_ask"] = best_ask
        
        if updates:
            # Calculate spread if both bid and ask are provided
            if best_bid is not None and best_ask is not None and best_bid > 0:
                spread_bps = int((best_ask - best_bid) / best_bid * 10000)
                updates.append("spread_bps = :spread_bps")
                params["spread_bps"] = spread_bps
            
            updates.append("updated_at = NOW()")
            
            query = f"""
                UPDATE dex_symbols 
                SET {', '.join(updates)}
                WHERE dex_id = :dex_id AND symbol_id = :symbol_id
            """
            
            await self.db.execute(query, params)

