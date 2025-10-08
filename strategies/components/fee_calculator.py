"""
Fee Calculator - Trading Fee Calculations

Pattern extracted from Hummingbot TradeFeeBase.
Critical for funding arbitrage profitability - must account for all 4 trades:
1. Open long
2. Open short  
3. Close long
4. Close short
"""

from decimal import Decimal
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# Enums
# ============================================================================

class OrderType(Enum):
    """Order type affects fees"""
    MARKET = "MARKET"  # Usually taker
    LIMIT = "LIMIT"    # Can be maker or taker
    LIMIT_MAKER = "LIMIT_MAKER"  # Always maker


class TradeType(Enum):
    """Trade direction"""
    BUY = "BUY"
    SELL = "SELL"


class PositionAction(Enum):
    """Opening or closing position"""
    OPEN = "OPEN"
    CLOSE = "CLOSE"


# ============================================================================
# Fee Schedules
# ============================================================================

# Fee schedules by DEX (maker/taker percentages)
# Source: Actual DEX fee schedules - UPDATE THESE WITH REAL VALUES
FEE_SCHEDULES = {
    # DEX: {'maker': fee_pct, 'taker': fee_pct}
    
    'lighter': {
        'maker': Decimal('0.0000'),   # 0% maker - Zero fees
        'taker': Decimal('0.0000'),   # 0% taker - Zero fees
    },
    'backpack': {
        'maker': Decimal('0.0002'),   # 0.02% maker
        'taker': Decimal('0.0005'),   # 0.05% taker
    },
    'grvt': {
        'maker': Decimal('-0.0001'),  # -0.01% maker (rebate!)
        'taker': Decimal('0.00055'),  # 0.055% taker
    },
    'hyperliquid': {
        'maker': Decimal('0.00015'),  # 0.015% maker
        'taker': Decimal('0.00045'),  # 0.045% taker
    },
    'paradex': {
        'maker': Decimal('0.00003'),  # 0.003% maker
        'taker': Decimal('0.0002'),   # 0.02% taker
    },
    'aster': {
        'maker': Decimal('0.00005'),  # 0.005% maker
        'taker': Decimal('0.0004'),   # 0.04% taker
    },
    'edgex': {
        'maker': Decimal('0.00015'),  # 0.015% maker
        'taker': Decimal('0.00038'),  # 0.038% taker
    },
}


# ============================================================================
# Fee Data Class
# ============================================================================

@dataclass
class TradeFee:
    """
    Represents calculated trading fee.
    
    Pattern from Hummingbot TradeFeeBase.
    """
    percent: Decimal  # Fee as percentage (0.0005 = 0.05%)
    flat_fees: Decimal = Decimal("0")  # Flat fee in quote currency
    
    def calculate_fee_amount(self, trade_value: Decimal) -> Decimal:
        """
        Calculate total fee in quote currency.
        
        Args:
            trade_value: Trade value in quote currency (USD)
        
        Returns:
            Total fee amount
        """
        percentage_fee = trade_value * self.percent
        return percentage_fee + self.flat_fees


# ============================================================================
# Fee Calculator
# ============================================================================

class FeeCalculator:
    """
    Calculate trading fees across DEXes.
    
    Pattern from Hummingbot TradeFeeBase and connector implementations.
    
    Critical for funding arbitrage:
    - Must account for 4 trades (open long, open short, close long, close short)
    - Maker fees < Taker fees (use limit orders when possible)
    - Different DEXes have different fee schedules
    """
    
    def __init__(self, fee_schedules: Dict[str, Dict[str, Decimal]] = None):
        """
        Initialize fee calculator.
        
        Args:
            fee_schedules: Override default fee schedules (for testing or custom rates)
        """
        self.fee_schedules = fee_schedules or FEE_SCHEDULES
    
    # ========================================================================
    # Single Trade Fee
    # ========================================================================
    
    def get_fee(
        self,
        dex_name: str,
        order_type: OrderType = OrderType.LIMIT,
        is_maker: bool = True
    ) -> TradeFee:
        """
        Calculate fee for a single trade.
        
        Pattern from Hummingbot connector.get_fee()
        
        Args:
            dex_name: Exchange name
            order_type: MARKET or LIMIT
            is_maker: True for maker, False for taker
        
        Returns:
            TradeFee object
        """
        # Get fee schedule for this DEX
        schedule = self.fee_schedules.get(dex_name, {
            'maker': Decimal('0.001'),  # Default 0.1%
            'taker': Decimal('0.001')
        })
        
        # Determine maker or taker
        if order_type == OrderType.MARKET:
            # Market orders are always taker
            fee_pct = schedule['taker']
        elif order_type == OrderType.LIMIT_MAKER:
            # Limit maker orders are always maker
            fee_pct = schedule['maker']
        else:  # LIMIT
            # Limit orders can be either
            fee_pct = schedule['maker'] if is_maker else schedule['taker']
        
        return TradeFee(percent=fee_pct)
    
    # ========================================================================
    # Entry Cost (Open Position)
    # ========================================================================
    
    def calculate_entry_cost(
        self,
        dex_name: str,
        position_size_usd: Decimal,
        is_maker: bool = True
    ) -> Decimal:
        """
        Calculate cost to ENTER a position (open long or short).
        
        Args:
            dex_name: Exchange name
            position_size_usd: Position size in USD
            is_maker: True for limit orders (maker), False for market (taker)
        
        Returns:
            Entry fee in USD
        """
        fee = self.get_fee(
            dex_name=dex_name,
            order_type=OrderType.LIMIT if is_maker else OrderType.MARKET,
            is_maker=is_maker
        )
        
        return fee.calculate_fee_amount(position_size_usd)
    
    # ========================================================================
    # Total Cost (Entry + Exit, Both Sides) - CRITICAL FOR FUNDING ARB
    # ========================================================================
    
    def calculate_total_cost(
        self,
        dex1_name: str,
        dex2_name: str,
        position_size_usd: Decimal,
        is_maker: bool = True
    ) -> Decimal:
        """
        ⭐ CRITICAL for funding arbitrage ⭐
        
        Calculate TOTAL cost for full funding arb cycle:
        1. Open long on DEX1
        2. Open short on DEX2
        3. Close long on DEX1
        4. Close short on DEX2
        
        Total: 4 trades
        
        Args:
            dex1_name: First DEX (long side)
            dex2_name: Second DEX (short side)
            position_size_usd: Position size in USD
            is_maker: Use maker fees (limit orders)
        
        Returns:
            Total fees in USD for complete cycle
        """
        # Entry fees (open both sides)
        entry_dex1 = self.calculate_entry_cost(dex1_name, position_size_usd, is_maker)
        entry_dex2 = self.calculate_entry_cost(dex2_name, position_size_usd, is_maker)
        
        # Exit fees (close both sides) - same as entry
        exit_dex1 = entry_dex1
        exit_dex2 = entry_dex2
        
        # Total
        total = entry_dex1 + entry_dex2 + exit_dex1 + exit_dex2
        
        return total
    
    def calculate_total_cost_percentage(
        self,
        dex1_name: str,
        dex2_name: str,
        position_size_usd: Decimal,
        is_maker: bool = True
    ) -> Decimal:
        """
        Total cost as percentage of position size.
        
        Easier to compare with funding rate spread.
        
        Returns:
            Total fee percentage (e.g., 0.002 = 0.2%)
        """
        total_cost = self.calculate_total_cost(
            dex1_name, dex2_name, position_size_usd, is_maker
        )
        
        return total_cost / position_size_usd
    
    # ========================================================================
    # Minimum Profitable Spread
    # ========================================================================
    
    def get_minimum_profitable_spread(
        self,
        dex1_name: str,
        dex2_name: str,
        time_horizon_hours: int = 24,
        is_maker: bool = True
    ) -> Decimal:
        """
        Calculate minimum funding rate spread to be profitable.
        
        The spread must exceed total fees to be worth it.
        
        Args:
            dex1_name: First DEX
            dex2_name: Second DEX
            time_horizon_hours: How long to hold position
            is_maker: Use maker fees
        
        Returns:
            Minimum spread as percentage (e.g., 0.002 = 0.2% over time_horizon)
        """
        # Get total fee percentage
        total_fee_pct = self.calculate_total_cost_percentage(
            dex1_name, dex2_name,
            Decimal("10000"),  # Dummy position size (percentage is same)
            is_maker
        )
        
        # This must be earned over time_horizon to break even
        return total_fee_pct
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def get_fee_schedule(self, dex_name: str) -> Dict[str, Decimal]:
        """
        Get fee schedule for a DEX.
        
        Args:
            dex_name: DEX name
            
        Returns:
            Dict with 'maker' and 'taker' fee percentages
        """
        return self.fee_schedules.get(dex_name, {
            'maker': Decimal('0.001'),
            'taker': Decimal('0.001')
        })
    
    def update_fee_schedule(
        self, 
        dex_name: str, 
        maker_fee: Decimal, 
        taker_fee: Decimal
    ):
        """
        Update fee schedule for a DEX.
        
        Use this when DEX changes fees.
        
        Args:
            dex_name: DEX name
            maker_fee: New maker fee percentage
            taker_fee: New taker fee percentage
        """
        self.fee_schedules[dex_name] = {
            'maker': maker_fee,
            'taker': taker_fee
        }

