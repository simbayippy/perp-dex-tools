"""
Common utilities for EdgeX exchange

Shared functions used by both the trading client and funding adapter.
"""

import re
from decimal import Decimal
from typing import Dict, Any


def normalize_symbol(symbol: str) -> str:
    """
    Normalize EdgeX symbol format to standard format
    
    EdgeX format examples:
    - BTCUSDT -> BTC
    - ETHUSDT -> ETH
    - 1000PEPEUSDT -> PEPE (handle multiplier prefix)
    
    Args:
        symbol: EdgeX symbol format (e.g., "BTCUSDT")
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    
    # Remove USDT/USD suffix
    normalized = normalized.replace('USDT', '').replace('USD', '')
    
    # Handle multiplier prefixes (e.g., 1000PEPE -> PEPE)
    match = re.match(r'^(\d+)([A-Z]+)$', normalized)
    if match:
        _, symbol_part = match.groups()
        normalized = symbol_part
    
    # Clean up any remaining special characters
    normalized = normalized.strip('-_/')
    
    return normalized


def get_edgex_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to EdgeX format
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC")
        
    Returns:
        EdgeX format (e.g., "BTCUSDT")
    """
    return f"{normalized_symbol.upper()}USDT"


def parse_edgex_order(order_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse EdgeX order response into standardized format
    
    Args:
        order_data: Raw order data from EdgeX API
        
    Returns:
        Standardized order data
    """
    return {
        'order_id': order_data.get('id', ''),
        'side': order_data.get('side', '').lower(),
        'size': Decimal(order_data.get('size', 0)),
        'price': Decimal(order_data.get('price', 0)),
        'status': order_data.get('status', ''),
        'filled_size': Decimal(order_data.get('cumMatchSize', 0)),
        'remaining_size': Decimal(order_data.get('size', 0)) - Decimal(order_data.get('cumMatchSize', 0))
    }


def calculate_open_interest_usd(contracts: str, index_price: str) -> Decimal:
    """
    Calculate open interest in USD
    
    Args:
        contracts: Open interest in contracts
        index_price: Index price
        
    Returns:
        Open interest in USD
    """
    return Decimal(str(contracts)) * Decimal(str(index_price))

