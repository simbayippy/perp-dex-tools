"""Trade aggregation utilities."""

from decimal import Decimal
from typing import Dict, List, Any
from exchange_clients.base_models import TradeData


def aggregate_trades_by_order(trades: List[TradeData]) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate trades by order_id.
    
    Groups multiple fills for the same order into a single aggregated record.
    
    Args:
        trades: List of TradeData objects (may have multiple fills per order_id)
        
    Returns:
        Dictionary mapping order_id to aggregated trade data:
        {
            order_id: {
                'order_id': str,
                'trade_id': str (first trade_id),
                'timestamp': float (earliest timestamp),
                'side': str,
                'total_quantity': Decimal,
                'weighted_avg_price': Decimal,
                'total_fee': Decimal,
                'fee_currency': str,
                'realized_pnl': Decimal or None,
                'realized_funding': Decimal or None,
                'fill_count': int
            }
        }
    """
    aggregated: Dict[str, Dict[str, Any]] = {}
    
    for trade in trades:
        order_id = trade.order_id or f"unknown_{trade.trade_id}"
        
        if order_id not in aggregated:
            aggregated[order_id] = {
                'order_id': order_id,
                'trade_id': trade.trade_id,
                'timestamp': trade.timestamp,
                'side': trade.side,
                'total_quantity': Decimal("0"),
                'total_value': Decimal("0"),  # For weighted avg calculation
                'total_fee': Decimal("0"),
                'fee_currency': trade.fee_currency,
                'realized_pnl': trade.realized_pnl,
                'realized_funding': trade.realized_funding,
                'fill_count': 0,
            }
        
        agg = aggregated[order_id]
        agg['total_quantity'] += trade.quantity
        agg['total_value'] += trade.price * trade.quantity
        agg['total_fee'] += trade.fee
        agg['fill_count'] += 1
        
        # Update timestamp to earliest
        if trade.timestamp < agg['timestamp']:
            agg['timestamp'] = trade.timestamp
        
        # Accumulate realized_pnl and realized_funding if available
        if trade.realized_pnl is not None:
            if agg['realized_pnl'] is None:
                agg['realized_pnl'] = Decimal("0")
            agg['realized_pnl'] += trade.realized_pnl
        
        if trade.realized_funding is not None:
            if agg['realized_funding'] is None:
                agg['realized_funding'] = Decimal("0")
            agg['realized_funding'] += trade.realized_funding
    
    # Calculate weighted average price
    for order_id, agg in aggregated.items():
        if agg['total_quantity'] > 0:
            agg['weighted_avg_price'] = agg['total_value'] / agg['total_quantity']
        else:
            agg['weighted_avg_price'] = Decimal("0")
        # Remove temporary field
        del agg['total_value']
    
    return aggregated

