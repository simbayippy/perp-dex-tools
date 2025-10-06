"""
Fast bidirectional mappers for IDs <-> Names

These mappers provide O(1) lookups between:
- DEX ID (int) <-> DEX Name (str)
- Symbol ID (int) <-> Symbol (str)

Loaded once at startup and kept in memory for performance.
"""

from typing import Dict, Optional
from databases import Database
from utils.logger import logger


class DEXMapper:
    """
    Fast bidirectional mapping between DEX IDs and names
    
    Usage:
        mapper = DEXMapper()
        await mapper.load_from_db(db)
        
        dex_id = mapper.get_id("lighter")  # Returns 1
        dex_name = mapper.get_name(1)      # Returns "lighter"
    """
    
    def __init__(self):
        self._id_to_name: Dict[int, str] = {}
        self._name_to_id: Dict[str, int] = {}
        self._loaded = False
    
    async def load_from_db(self, db: Database) -> None:
        """
        Load DEX mappings from database
        
        This should be called once at application startup.
        """
        try:
            rows = await db.fetch_all("SELECT id, name FROM dexes ORDER BY id")
            
            self._id_to_name = {row['id']: row['name'] for row in rows}
            self._name_to_id = {row['name']: row['id'] for row in rows}
            
            self._loaded = True
            logger.info(f"DEXMapper loaded: {len(self._id_to_name)} DEXs")
            logger.debug(f"DEX mappings: {self._name_to_id}")
            
        except Exception as e:
            logger.error(f"Failed to load DEX mappings: {e}")
            raise
    
    def get_id(self, name: str) -> Optional[int]:
        """
        Get DEX ID from name
        
        Args:
            name: DEX name (e.g., "lighter", "edgex")
            
        Returns:
            DEX ID or None if not found
        """
        return self._name_to_id.get(name.lower())
    
    def get_name(self, dex_id: int) -> Optional[str]:
        """
        Get DEX name from ID
        
        Args:
            dex_id: DEX ID
            
        Returns:
            DEX name or None if not found
        """
        return self._id_to_name.get(dex_id)
    
    def get_all_names(self) -> list[str]:
        """Get all DEX names"""
        return list(self._name_to_id.keys())
    
    def get_all_ids(self) -> list[int]:
        """Get all DEX IDs"""
        return list(self._id_to_name.keys())
    
    def add(self, dex_id: int, name: str) -> None:
        """
        Add a new DEX mapping (used when new DEX is discovered)
        
        Args:
            dex_id: DEX ID
            name: DEX name
        """
        self._id_to_name[dex_id] = name
        self._name_to_id[name] = dex_id
        logger.info(f"Added new DEX mapping: {name} -> {dex_id}")
    
    def is_loaded(self) -> bool:
        """Check if mapper has been loaded"""
        return self._loaded
    
    def __len__(self) -> int:
        """Return number of DEXs"""
        return len(self._id_to_name)


class SymbolMapper:
    """
    Fast bidirectional mapping between Symbol IDs and symbol strings
    
    Symbols are dynamically discovered from DEX APIs, so this mapper
    can grow over time as new symbols are found.
    
    Usage:
        mapper = SymbolMapper()
        await mapper.load_from_db(db)
        
        symbol_id = mapper.get_id("BTC")  # Returns 1
        symbol = mapper.get_name(1)       # Returns "BTC"
    """
    
    def __init__(self):
        self._id_to_symbol: Dict[int, str] = {}
        self._symbol_to_id: Dict[str, int] = {}
        self._loaded = False
    
    async def load_from_db(self, db: Database) -> None:
        """
        Load symbol mappings from database
        
        This should be called once at application startup.
        """
        try:
            rows = await db.fetch_all("SELECT id, symbol FROM symbols ORDER BY id")
            
            self._id_to_symbol = {row['id']: row['symbol'] for row in rows}
            self._symbol_to_id = {row['symbol']: row['id'] for row in rows}
            
            self._loaded = True
            logger.info(f"SymbolMapper loaded: {len(self._id_to_symbol)} symbols")
            
        except Exception as e:
            logger.error(f"Failed to load symbol mappings: {e}")
            raise
    
    def get_id(self, symbol: str) -> Optional[int]:
        """
        Get symbol ID from symbol string
        
        Args:
            symbol: Symbol (e.g., "BTC", "ETH")
            
        Returns:
            Symbol ID or None if not found
        """
        return self._symbol_to_id.get(symbol.upper())
    
    def get_name(self, symbol_id: int) -> Optional[str]:
        """
        Get symbol string from ID
        
        Args:
            symbol_id: Symbol ID
            
        Returns:
            Symbol string or None if not found
        """
        return self._id_to_symbol.get(symbol_id)
    
    def get_all_symbols(self) -> list[str]:
        """Get all symbol strings"""
        return list(self._symbol_to_id.keys())
    
    def get_all_ids(self) -> list[int]:
        """Get all symbol IDs"""
        return list(self._id_to_symbol.keys())
    
    def add(self, symbol_id: int, symbol: str) -> None:
        """
        Add a new symbol mapping (used when new symbol is discovered)
        
        Args:
            symbol_id: Symbol ID
            symbol: Symbol string
        """
        symbol = symbol.upper()
        self._id_to_symbol[symbol_id] = symbol
        self._symbol_to_id[symbol] = symbol_id
        logger.info(f"Added new symbol mapping: {symbol} -> {symbol_id}")
    
    def is_loaded(self) -> bool:
        """Check if mapper has been loaded"""
        return self._loaded
    
    def __len__(self) -> int:
        """Return number of symbols"""
        return len(self._id_to_symbol)


# Global mapper instances (initialized at startup)
dex_mapper = DEXMapper()
symbol_mapper = SymbolMapper()

