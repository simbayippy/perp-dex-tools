"""
Tracked Order - Lightweight Order Tracking with Lazy Loading

Pattern extracted from Hummingbot ExecutorBase.
Avoids unnecessary API calls by lazy-loading order details only when accessed.
"""

from typing import Optional
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OrderData:
    """
    Full order details (fetched from exchange).
    
    This is what gets lazy-loaded when order properties are accessed.
    """
    order_id: str
    symbol: str
    side: str  # 'buy' or 'sell'
    order_type: str  # 'limit', 'market'
    
    # Amounts
    original_amount: Decimal
    executed_amount_base: Decimal
    executed_amount_quote: Decimal
    
    # Prices
    limit_price: Optional[Decimal]
    average_executed_price: Decimal
    
    # Status
    status: str  # 'open', 'filled', 'cancelled', 'expired'
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    
    # Fees
    fee_amount: Decimal = Decimal("0")
    fee_currency: str = "USD"


class TrackedOrder:
    """
    Lightweight order wrapper with lazy loading.
    
    Pattern from Hummingbot:
    - Store only order_id initially (lightweight)
    - Fetch full details when first accessed (lazy)
    - Cache details for subsequent accesses (efficient)
    
    Benefits:
    - Reduced API calls (only fetch when needed)
    - Faster initialization
    - Consistent interface
    - Easy to test (mock the fetch)
    
    Usage:
    ------
    # Create with just ID (no API call)
    order = TrackedOrder(order_id='order_123', exchange_client=client)
    
    # Access properties triggers lazy load (one API call)
    amount = order.executed_amount_base
    price = order.average_executed_price  # Uses cached data
    
    # Force refresh for latest status
    order.refresh()
    """
    
    def __init__(
        self,
        order_id: Optional[str] = None,
        exchange_client=None
    ):
        """
        Initialize tracked order.
        
        Args:
            order_id: Exchange order ID
            exchange_client: Exchange client for fetching order data
        """
        # Only store the ID (lightweight)
        self._order_id = order_id
        self._exchange_client = exchange_client
        
        # Lazy-loaded data (None until first access)
        self._order_data: Optional[OrderData] = None
    
    # ========================================================================
    # Core Properties
    # ========================================================================
    
    @property
    def order_id(self) -> Optional[str]:
        """Get order ID (always available, no API call)"""
        return self._order_id
    
    @order_id.setter
    def order_id(self, value: str):
        """Set order ID and invalidate cached data"""
        self._order_id = value
        self._order_data = None  # Invalidate cache
    
    @property
    def order(self) -> Optional[OrderData]:
        """
        Get full order data (lazy loaded).
        
        Pattern:
        1. Check if already loaded (cached)
        2. If not, fetch from exchange
        3. Cache for future accesses
        4. Return cached data
        """
        if self._order_data is None and self._order_id is not None:
            # Lazy load: fetch from exchange
            self._order_data = self._fetch_order_from_exchange()
        
        return self._order_data
    
    # ========================================================================
    # Convenience Properties (Lazy-Loaded)
    # ========================================================================
    
    @property
    def executed_amount_base(self) -> Decimal:
        """
        Amount executed in base currency.
        
        Lazy loads order if not already loaded.
        """
        if self.order is None:
            return Decimal("0")
        return self.order.executed_amount_base
    
    @property
    def executed_amount_quote(self) -> Decimal:
        """Amount executed in quote currency"""
        if self.order is None:
            return Decimal("0")
        return self.order.executed_amount_quote
    
    @property
    def average_executed_price(self) -> Decimal:
        """Average fill price"""
        if self.order is None:
            return Decimal("0")
        return self.order.average_executed_price
    
    @property
    def is_done(self) -> bool:
        """Is order fully filled or cancelled?"""
        if self.order is None:
            return False
        return self.order.status in ['filled', 'cancelled', 'expired']
    
    @property
    def is_filled(self) -> bool:
        """Is order completely filled?"""
        if self.order is None:
            return False
        return self.order.status == 'filled'
    
    @property
    def is_cancelled(self) -> bool:
        """Was order cancelled?"""
        if self.order is None:
            return False
        return self.order.status == 'cancelled'
    
    @property
    def fee_amount(self) -> Decimal:
        """Trading fee paid"""
        if self.order is None:
            return Decimal("0")
        return self.order.fee_amount
    
    # ========================================================================
    # Private Methods
    # ========================================================================
    
    def _fetch_order_from_exchange(self) -> Optional[OrderData]:
        """
        Fetch order details from exchange.
        
        In your implementation, this calls your exchange client.
        Override or inject different fetchers for different exchanges.
        """
        if self._exchange_client is None:
            return None
        
        try:
            # Call exchange API through client
            # Assuming exchange_client has a get_order method
            return self._exchange_client.get_order(self._order_id)
        except Exception as e:
            # Log error but don't crash
            # In production, use proper logging
            print(f"Error fetching order {self._order_id}: {e}")
            return None
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def refresh(self):
        """
        Force refresh order data from exchange.
        
        Use this to update order status.
        Clears cache and triggers lazy load on next property access.
        """
        self._order_data = None  # Clear cache
        _ = self.order  # Trigger lazy load
    
    def is_loaded(self) -> bool:
        """Check if order data has been loaded"""
        return self._order_data is not None
    
    def __repr__(self):
        return f"TrackedOrder(order_id={self._order_id}, loaded={self.is_loaded()})"

