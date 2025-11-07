"""
Position manager module for Paradex client.

Handles position tracking, snapshots, and funding calculations.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from exchange_clients.base_models import ExchangePositionSnapshot, query_retry
from exchange_clients.paradex.client.utils.converters import build_snapshot_from_paradex
from exchange_clients.paradex.client.utils.helpers import to_decimal
from exchange_clients.paradex.common import normalize_symbol


class ParadexPositionManager:
    """
    Position manager for Paradex exchange.
    
    Handles:
    - Position snapshots
    - Position size queries
    - Position caching
    """
    
    def __init__(
        self,
        api_client: Any,
        config: Any,
        logger: Any,
        normalize_symbol_fn: Optional[Any] = None,
        market_data_manager: Optional[Any] = None,
    ):
        """
        Initialize position manager.
        
        Args:
            api_client: Paradex API client instance (paradex.api_client)
            config: Trading configuration object
            logger: Logger instance
            normalize_symbol_fn: Function to normalize symbols
            market_data_manager: Optional market data manager (for mark prices)
        """
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.normalize_symbol = normalize_symbol_fn or normalize_symbol
        self.market_data = market_data_manager
        
        # Position cache
        self._positions_cache: Dict[str, Dict[str, Any]] = {}
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _fetch_positions_sync(self) -> List[Dict[str, Any]]:
        """
        Fetch positions synchronously (SDK is blocking).
        
        Returns:
            List of position dictionaries
        """
        positions_response = self.api_client.fetch_positions()
        if not positions_response or 'results' not in positions_response:
            return []
        return positions_response['results']
    
    @query_retry(default_return=Decimal("0"))
    async def get_account_positions(self, contract_id: str) -> Decimal:
        """
        Get account position size for a specific contract.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            
        Returns:
            Position size as Decimal (absolute value)
        """
        try:
            # Fetch positions (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(
                None,
                self._fetch_positions_sync
            )
            
            # Find position for the contract
            for position in positions:
                if isinstance(position, dict):
                    market = position.get('market') or position.get('symbol')
                    status = str(position.get('status', '')).upper()
                    
                    if market == contract_id and status == 'OPEN':
                        size = to_decimal(position.get('size'), Decimal("0")) or Decimal("0")
                        return abs(size)
            
            return Decimal("0")
            
        except Exception as e:
            self.logger.error(f"Failed to get account positions for {contract_id}: {e}")
            return Decimal("0")
    
    async def get_position_snapshot(
        self,
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """
        Get position snapshot for a symbol.
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "BTC", "ETH")
            position_opened_at: Optional Unix timestamp (seconds) when position was opened.
                              Currently unused but kept for interface compatibility.
        
        Returns:
            ExchangePositionSnapshot with position details, or None if position not found
        """
        # Convert normalized symbol to Paradex format
        contract_id = f"{symbol.upper()}-USD-PERP"
        
        try:
            # Fetch positions (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(
                None,
                self._fetch_positions_sync
            )
            
            # Find position for this symbol
            position_data = None
            for position in positions:
                if isinstance(position, dict):
                    market = position.get('market') or position.get('symbol')
                    status = str(position.get('status', '')).upper()
                    
                    if market == contract_id and status == 'OPEN':
                        position_data = position
                        break
            
            if not position_data:
                return None
            
            # Build snapshot from position data
            snapshot = build_snapshot_from_paradex(symbol, position_data)
            
            # Enrich with mark price from market data if available
            if snapshot and self.market_data:
                try:
                    # Try to get mark price from markets_summary
                    summary_response = self.api_client.fetch_markets_summary({"market": contract_id})
                    if summary_response and 'results' in summary_response:
                        markets = summary_response['results']
                        if markets:
                            mark_price = to_decimal(markets[0].get('mark_price'))
                            if mark_price:
                                snapshot.mark_price = mark_price
                                
                                # Recalculate exposure if we have mark price
                                if snapshot.quantity > 0:
                                    snapshot.exposure_usd = abs(snapshot.quantity) * mark_price
                except Exception as e:
                    self.logger.debug(f"Failed to enrich snapshot with mark price: {e}")
            
            return snapshot
            
        except Exception as e:
            self.logger.error(f"Failed to get position snapshot for {symbol}: {e}")
            return None
    
    async def refresh_positions_cache(self) -> None:
        """
        Refresh the positions cache.
        
        This can be called periodically to keep position data fresh.
        """
        try:
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(
                None,
                self._fetch_positions_sync
            )
            
            # Update cache
            self._positions_cache.clear()
            for position in positions:
                if isinstance(position, dict):
                    market = position.get('market') or position.get('symbol')
                    status = str(position.get('status', '')).upper()
                    
                    if market and status == 'OPEN':
                        normalized = normalize_symbol(market)
                        self._positions_cache[normalized] = position
            
        except Exception as e:
            self.logger.error(f"Failed to refresh positions cache: {e}")

