"""
Common utilities for GRVT exchange

Shared functions used by both the trading client and funding adapter.
"""

import re
from decimal import Decimal
from typing import Dict, Any
from pysdk.grvt_ccxt_env import GrvtEnv


def get_grvt_env(environment: str) -> GrvtEnv:
    """
    Convert environment string to GRVT enum
    
    Args:
        environment: "prod", "testnet", "staging", or "dev"
        
    Returns:
        GrvtEnv enum value
    """
    env_map = {
        'prod': GrvtEnv.PROD,
        'testnet': GrvtEnv.TESTNET,
        'staging': GrvtEnv.STAGING,
        'dev': GrvtEnv.DEV
    }
    return env_map.get(environment.lower(), GrvtEnv.PROD)


def normalize_symbol(symbol: str) -> str:
    """
    Normalize GRVT symbol format to standard format
    
    Args:
        symbol: GRVT-specific symbol format
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    
    # Remove common suffixes
    normalized = normalized.replace('_PERP', '')
    normalized = normalized.replace('PERP', '')
    normalized = normalized.replace('_USDT', '')
    normalized = normalized.replace('USDT', '')
    
    # Handle multipliers
    match = re.match(r'^(\d+)([A-Z]+)$', normalized)
    if match:
        _, symbol_part = match.groups()
        normalized = symbol_part
    
    normalized = normalized.strip('-_/')
    
    return normalized


def get_grvt_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to GRVT-specific format
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC")
        
    Returns:
        GRVT-specific format
    """
    return normalized_symbol.upper()


def parse_grvt_order(order_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse GRVT order response into standardized format
    
    Args:
        order_data: Raw order data from GRVT API
        
    Returns:
        Standardized order data
    """
    legs = order_data.get('legs', [])
    if not legs:
        return {}
    
    leg = legs[0]
    state = order_data.get('state', {})
    
    return {
        'order_id': order_data.get('order_id', ''),
        'side': 'buy' if leg.get('is_buying_asset') else 'sell',
        'size': Decimal(leg.get('size', 0)),
        'price': Decimal(leg.get('limit_price', 0)),
        'status': state.get('status', ''),
        'filled_size': Decimal(state.get('traded_size', ['0'])[0]) if isinstance(state.get('traded_size'), list) else Decimal(0),
        'remaining_size': Decimal(state.get('book_size', ['0'])[0]) if isinstance(state.get('book_size'), list) else Decimal(0)
    }


def calculate_open_interest_usd(contracts: str, mark_price: str) -> Decimal:
    """
    Calculate open interest in USD
    
    Args:
        contracts: Open interest in contracts
        mark_price: Mark price
        
    Returns:
        Open interest in USD
    """
    return Decimal(str(contracts)) * Decimal(str(mark_price))

