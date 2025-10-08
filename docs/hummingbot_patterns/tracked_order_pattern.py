"""
PATTERN 4: Tracked Order (Lazy Loading)
========================================

Extracted from: Hummingbot position_executor
Source: docs/hummingbot_reference/position_executor/NOTES.md

Purpose:
--------
Lightweight order tracking with lazy loading to avoid unnecessary API calls.

Why This Pattern?
-----------------
✅ Don't fetch order data until needed
✅ Cache fetched data to avoid repeated calls
✅ Consistent interface across different exchanges
✅ Easy to mock in tests

Key Concept:
------------
Instead of immediately fetching full order details when you get an order ID,
store just the ID and fetch details only when accessed.

This saves API calls and improves performance.

"""

from typing import Optional
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime


# ============================================================================
# CORE PATTERN: Lazy-Loaded Order
# ============================================================================

class TrackedOrder:
    """
    Lightweight order wrapper with lazy loading.
    
    Pattern from Hummingbot:
    - Store only order_id initially
    - Fetch full order details when first accessed
    - Cache the details for subsequent accesses
    
    Benefits:
    ---------
    1. Reduced API calls (only fetch when needed)
    2. Faster initialization
    3. Consistent interface
    4. Easy to test (mock the fetch)
    """
    
    def __init__(
        self,
        order_id: Optional[str] = None,
        exchange_client=None  # Your exchange client
    ):
        # Only store the ID
        self._order_id = order_id
        self._exchange_client = exchange_client
        
        # Lazy-loaded data (None until first access)
        self._order_data: Optional['OrderData'] = None
    
    # ========================================================================
    # Core Properties
    # ========================================================================
    
    @property
    def order_id(self) -> Optional[str]:
        """Get order ID (always available)"""
        return self._order_id
    
    @order_id.setter
    def order_id(self, value: str):
        """Set order ID and invalidate cached data"""
        self._order_id = value
        self._order_data = None  # Invalidate cache
    
    @property
    def order(self) -> Optional['OrderData']:
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
    
    # ========================================================================
    # Private Methods
    # ========================================================================
    
    def _fetch_order_from_exchange(self) -> Optional['OrderData']:
        """
        Fetch order details from exchange.
        
        In your implementation, call your exchange client:
        
        return self._exchange_client.get_order(self._order_id)
        """
        if self._exchange_client is None:
            return None
        
        # Call exchange API
        try:
            return self._exchange_client.get_order(self._order_id)
        except Exception as e:
            print(f"Error fetching order {self._order_id}: {e}")
            return None
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def refresh(self):
        """
        Force refresh order data from exchange.
        
        Use this to update order status.
        """
        self._order_data = None  # Clear cache
        _ = self.order  # Trigger lazy load
    
    def __repr__(self):
        return f"TrackedOrder(order_id={self._order_id}, loaded={self._order_data is not None})"


# ============================================================================
# Supporting Data Class
# ============================================================================

@dataclass
class OrderData:
    """
    Full order details (fetched from exchange).
    
    This is what gets lazy-loaded.
    """
    order_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    order_type: str  # 'LIMIT', 'MARKET'
    
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


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

class MockExchangeClient:
    """Mock exchange client for example"""
    
    def get_order(self, order_id: str) -> OrderData:
        """Simulate fetching order from exchange"""
        print(f"[API CALL] Fetching order {order_id} from exchange...")
        
        # Simulate API response
        return OrderData(
            order_id=order_id,
            symbol='BTC',
            side='BUY',
            order_type='LIMIT',
            original_amount=Decimal('1.0'),
            executed_amount_base=Decimal('1.0'),
            executed_amount_quote=Decimal('50000'),
            limit_price=Decimal('50000'),
            average_executed_price=Decimal('50000'),
            status='filled',
            created_at=datetime.now(),
            updated_at=datetime.now(),
            fee_amount=Decimal('25'),
            fee_currency='USD'
        )


def example_lazy_loading():
    """
    Demonstrate lazy loading behavior
    """
    print("=" * 60)
    print("Example: Lazy Loading Pattern")
    print("=" * 60)
    
    client = MockExchangeClient()
    
    # Create tracked order with just ID
    print("\n1. Creating tracked order (no API call yet)...")
    order = TrackedOrder(order_id='order_123', exchange_client=client)
    print(f"   Order created: {order}")
    
    # Access order_id (no API call)
    print(f"\n2. Accessing order_id: {order.order_id}")
    print("   (No API call - just returning the ID)")
    
    # Access executed_amount (triggers lazy load)
    print(f"\n3. Accessing executed_amount_base...")
    amount = order.executed_amount_base
    print(f"   Amount: {amount}")
    print("   ^^^ API call was made here!")
    
    # Access average_price (uses cached data)
    print(f"\n4. Accessing average_executed_price...")
    price = order.average_executed_price
    print(f"   Price: {price}")
    print("   (No API call - used cached data)")
    
    # Refresh to get latest status
    print(f"\n5. Refreshing order data...")
    order.refresh()
    print("   Fresh data fetched")


def example_multiple_orders():
    """
    Show efficiency with multiple orders
    """
    print("\n" + "=" * 60)
    print("Example: Multiple Orders Efficiency")
    print("=" * 60)
    
    client = MockExchangeClient()
    
    # Create 10 orders
    print("\n1. Creating 10 tracked orders...")
    orders = [
        TrackedOrder(order_id=f'order_{i}', exchange_client=client)
        for i in range(10)
    ]
    print(f"   Created {len(orders)} orders (0 API calls)")
    
    # Only access data for filled orders
    print("\n2. Checking which orders are filled...")
    filled_count = 0
    for i, order in enumerate(orders):
        if i % 3 == 0:  # Only check every 3rd order
            if order.is_filled:
                filled_count += 1
    
    print(f"   Filled: {filled_count}")
    print(f"   API calls made: {(len(orders) // 3) + 1}")
    print("   (Only fetched data for orders we checked)")


# ============================================================================
# HOW TO INTEGRATE INTO YOUR CODE
# ============================================================================

"""
Integration with your exchange clients:
---------------------------------------

# In your exchange_clients/base.py
class BaseExchangeClient:
    async def get_order(self, order_id: str) -> OrderData:
        '''Fetch order details from exchange'''
        # Your API call here
        pass

# In your position tracking
from strategies.components.tracked_order import TrackedOrder

class Position:
    def __init__(self):
        self.orders: List[TrackedOrder] = []
    
    def add_order(self, order_id: str, exchange_client):
        '''Add order by ID only (no API call)'''
        tracked = TrackedOrder(order_id, exchange_client)
        self.orders.append(tracked)
    
    def get_total_filled_amount(self):
        '''Calculate total filled (triggers lazy loads)'''
        total = Decimal("0")
        for order in self.orders:
            total += order.executed_amount_base
        return total

# Usage in strategy
async def _monitor_positions(self):
    for position in self.positions:
        # Only fetch order data when needed
        filled_amount = position.get_total_filled_amount()
        
        # If you need fresh data
        for order in position.orders:
            order.refresh()

"""

# ============================================================================
# ALTERNATIVE: Eager Loading (When to Use)
# ============================================================================

class EagerTrackedOrder:
    """
    Alternative: Eager loading (fetch immediately).
    
    Use when:
    - You know you'll need the data
    - API calls are cheap/fast
    - Caching doesn't help
    """
    
    def __init__(self, order_id: str, exchange_client):
        self._order_id = order_id
        self._exchange_client = exchange_client
        
        # Fetch immediately
        self._order_data = self._fetch_order_from_exchange()
    
    def _fetch_order_from_exchange(self):
        return self._exchange_client.get_order(self._order_id)
    
    @property
    def executed_amount_base(self) -> Decimal:
        # No lazy loading - data already fetched
        return self._order_data.executed_amount_base if self._order_data else Decimal("0")


# ============================================================================
# RUN EXAMPLES
# ============================================================================

if __name__ == "__main__":
    example_lazy_loading()
    example_multiple_orders()


# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

"""
1. ✅ Lazy loading reduces unnecessary API calls
2. ✅ Cache fetched data to avoid repeated calls
3. ✅ Property pattern provides clean interface
4. ✅ Easy to test with mock exchange clients
5. ✅ Use refresh() when you need latest data

Extract for your code:
----------------------
- TrackedOrder class → strategies/components/tracked_order.py
- OrderData dataclass → Define in your models
- Lazy loading pattern → Use in position tracking

When to use:
------------
✅ Use lazy loading when:
  - You create many orders but only access some
  - API calls are expensive/slow
  - Order status doesn't change frequently

❌ Use eager loading when:
  - You always need the data
  - API calls are very fast
  - Real-time status is critical

For funding arbitrage:
----------------------
- Create TrackedOrder when position opens
- Lazy load for PnL calculations (only when needed)
- Refresh when checking exit conditions
- Cache reduces API calls during monitoring
"""

