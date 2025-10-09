"""
PATTERN 5: Fee Calculation (TradeFeeBase)
=========================================

Extracted from: Hummingbot TradeFeeBase
Source: docs/hummingbot_reference/funding_payments/NOTES.md

Purpose:
--------
Accurately calculate trading fees across different DEXes with:
- Maker/taker fee differentiation
- Per-DEX fee schedules
- Position open/close fee calculation
- Fee impact on profitability

Why This Pattern?
-----------------
✅ Fees can make/break funding arbitrage profitability
✅ Different DEXes have different fee structures
✅ Must account for 4 trades: open long, open short, close long, close short
✅ Maker fees often lower than taker fees

Key Insight:
------------
A 0.1% funding rate opportunity can become unprofitable after fees!

Example:
- Funding spread: 0.1% per day
- Fees: 0.05% per trade × 4 trades = 0.2%
- Net result: -0.1% loss!

"""

from decimal import Decimal
from typing import Dict, Tuple
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# CORE PATTERN: Fee Types
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
# CORE PATTERN: Fee Schedules
# ============================================================================

# Fee schedules by DEX (maker/taker in basis points)
# Source: Actual DEX fee schedules as of 2024
FEE_SCHEDULES = {
    # DEX: {'maker': fee_pct, 'taker': fee_pct}
    
    # CEX Perpetuals
    'binance_perpetual': {
        'maker': Decimal('0.0002'),   # 0.02% maker
        'taker': Decimal('0.0005'),   # 0.05% taker
    },
    'bybit_perpetual': {
        'maker': Decimal('0.0001'),   # 0.01% maker
        'taker': Decimal('0.0006'),   # 0.06% taker
    },
    
    # DEX Perpetuals
    'lighter': {
        'maker': Decimal('0.0002'),   # 0.02% maker
        'taker': Decimal('0.0005'),   # 0.05% taker
    },
    'backpack': {
        'maker': Decimal('0.0002'),   # 0.02% maker
        'taker': Decimal('0.0005'),   # 0.05% taker
    },
    'grvt': {
        'maker': Decimal('0.0002'),   # 0.02% maker
        'taker': Decimal('0.0004'),   # 0.04% taker
    },
    'hyperliquid_perpetual': {
        'maker': Decimal('0.00020'),  # 0.02% maker
        'taker': Decimal('0.00035'),  # 0.035% taker
    },
    'paradex': {
        'maker': Decimal('0.0002'),   # 0.02% maker (assumed)
        'taker': Decimal('0.0005'),   # 0.05% taker (assumed)
    },
    'aster': {
        'maker': Decimal('0.0002'),   # 0.02% maker (assumed)
        'taker': Decimal('0.0005'),   # 0.05% taker (assumed)
    },
    'edgex': {
        'maker': Decimal('0.0002'),   # 0.02% maker (assumed)
        'taker': Decimal('0.0005'),   # 0.05% taker (assumed)
    },
}


# ============================================================================
# CORE PATTERN: Fee Calculation
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


class FeeCalculator:
    """
    Calculate trading fees across DEXes.
    
    Pattern from Hummingbot TradeFeeBase and connector implementations.
    """
    
    def __init__(self, fee_schedules: Dict[str, Dict[str, Decimal]] = None):
        """
        Args:
            fee_schedules: Override default fee schedules (for testing)
        """
        self.fee_schedules = fee_schedules or FEE_SCHEDULES
    
    # ========================================================================
    # Single Trade Fee
    # ========================================================================
    
    def get_fee(
        self,
        dex_name: str,
        order_type: OrderType,
        trade_type: TradeType,
        position_action: PositionAction,
        amount: Decimal,
        price: Decimal,
        is_maker: bool = True
    ) -> TradeFee:
        """
        Calculate fee for a single trade.
        
        Pattern from Hummingbot connector.get_fee()
        
        Args:
            dex_name: Exchange name
            order_type: MARKET or LIMIT
            trade_type: BUY or SELL
            position_action: OPEN or CLOSE
            amount: Trade amount in base currency
            price: Trade price
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
            trade_type=TradeType.BUY,  # Direction doesn't affect fee
            position_action=PositionAction.OPEN,
            amount=Decimal("1"),  # Dummy amount
            price=Decimal("1"),   # Dummy price
            is_maker=is_maker
        )
        
        return fee.calculate_fee_amount(position_size_usd)
    
    # ========================================================================
    # Total Cost (Entry + Exit, Both Sides)
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
            Decimal("10000"),  # Dummy position size
            is_maker
        )
        
        # This must be earned over time_horizon to break even
        return total_fee_pct


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

def example_1_single_trade_fee():
    """Calculate fee for a single trade"""
    print("=" * 60)
    print("Example 1: Single Trade Fee")
    print("=" * 60)
    
    calc = FeeCalculator()
    
    # Market order on Lighter (taker fee)
    fee = calc.get_fee(
        dex_name='lighter',
        order_type=OrderType.MARKET,
        trade_type=TradeType.BUY,
        position_action=PositionAction.OPEN,
        amount=Decimal('1.0'),
        price=Decimal('50000'),
        is_maker=False
    )
    
    trade_value = Decimal('50000')  # 1 BTC × $50k
    fee_amount = fee.calculate_fee_amount(trade_value)
    
    print(f"\nMarket order on Lighter (taker):")
    print(f"  Trade Value: ${trade_value}")
    print(f"  Fee Rate: {fee.percent * 100:.3f}%")
    print(f"  Fee Amount: ${fee_amount:.2f}")
    
    # Limit order on GRVT (maker fee)
    fee = calc.get_fee(
        dex_name='grvt',
        order_type=OrderType.LIMIT,
        trade_type=TradeType.BUY,
        position_action=PositionAction.OPEN,
        amount=Decimal('1.0'),
        price=Decimal('50000'),
        is_maker=True
    )
    
    fee_amount = fee.calculate_fee_amount(trade_value)
    
    print(f"\nLimit order on GRVT (maker):")
    print(f"  Trade Value: ${trade_value}")
    print(f"  Fee Rate: {fee.percent * 100:.3f}%")
    print(f"  Fee Amount: ${fee_amount:.2f}")


def example_2_total_cost():
    """Calculate total cost for funding arb"""
    print("\n" + "=" * 60)
    print("Example 2: Total Funding Arb Cost")
    print("=" * 60)
    
    calc = FeeCalculator()
    
    position_size = Decimal('10000')
    
    # Maker fees (limit orders)
    total_maker = calc.calculate_total_cost(
        'lighter', 'grvt', position_size, is_maker=True
    )
    total_maker_pct = calc.calculate_total_cost_percentage(
        'lighter', 'grvt', position_size, is_maker=True
    )
    
    # Taker fees (market orders)
    total_taker = calc.calculate_total_cost(
        'lighter', 'grvt', position_size, is_maker=False
    )
    total_taker_pct = calc.calculate_total_cost_percentage(
        'lighter', 'grvt', position_size, is_maker=False
    )
    
    print(f"\nPosition Size: ${position_size}")
    print(f"\nUsing Maker Fees (Limit Orders):")
    print(f"  Total Cost: ${total_maker:.2f}")
    print(f"  Cost %: {total_maker_pct * 100:.3f}%")
    print(f"\nUsing Taker Fees (Market Orders):")
    print(f"  Total Cost: ${total_taker:.2f}")
    print(f"  Cost %: {total_taker_pct * 100:.3f}%")
    print(f"\nSavings with maker orders: ${total_taker - total_maker:.2f}")


def example_3_minimum_spread():
    """Calculate break-even funding spread"""
    print("\n" + "=" * 60)
    print("Example 3: Minimum Profitable Spread")
    print("=" * 60)
    
    calc = FeeCalculator()
    
    # 24-hour holding period
    min_spread_24h = calc.get_minimum_profitable_spread(
        'lighter', 'backpack', time_horizon_hours=24, is_maker=True
    )
    
    # 7-day holding period
    min_spread_7d = calc.get_minimum_profitable_spread(
        'lighter', 'backpack', time_horizon_hours=168, is_maker=True
    )
    
    print(f"\nLighter ↔ Backpack (maker fees):")
    print(f"\n24-hour position:")
    print(f"  Minimum spread: {min_spread_24h * 100:.3f}%")
    print(f"  (Must earn at least this over 24h to break even)")
    print(f"\n7-day position:")
    print(f"  Minimum spread: {min_spread_7d * 100:.3f}%")
    print(f"  (Same fees, but spread over longer period)")


def example_4_profitability_check():
    """Check if an opportunity is profitable after fees"""
    print("\n" + "=" * 60)
    print("Example 4: Profitability Check")
    print("=" * 60)
    
    calc = FeeCalculator()
    
    # Opportunity data
    position_size = Decimal('10000')
    funding_spread_24h = Decimal('0.002')  # 0.2% over 24 hours
    
    # Calculate fees
    total_fees = calc.calculate_total_cost(
        'lighter', 'grvt', position_size, is_maker=True
    )
    fee_pct = total_fees / position_size
    
    # Calculate profit
    gross_profit = position_size * funding_spread_24h
    net_profit = gross_profit - total_fees
    net_profit_pct = net_profit / position_size
    
    print(f"\nOpportunity Analysis:")
    print(f"  Position Size: ${position_size}")
    print(f"  Funding Spread (24h): {funding_spread_24h * 100:.2f}%")
    print(f"  Gross Profit: ${gross_profit:.2f}")
    print(f"\n  Total Fees (4 trades): ${total_fees:.2f} ({fee_pct * 100:.3f}%)")
    print(f"\n  Net Profit: ${net_profit:.2f} ({net_profit_pct * 100:.3f}%)")
    
    if net_profit > 0:
        print(f"  ✅ Profitable!")
    else:
        print(f"  ❌ Not profitable after fees!")


# ============================================================================
# RUN EXAMPLES
# ============================================================================

if __name__ == "__main__":
    example_1_single_trade_fee()
    example_2_total_cost()
    example_3_minimum_spread()
    example_4_profitability_check()


# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

"""
1. ⭐⭐⭐ Always account for 4 trades (open long, open short, close long, close short)
2. ⭐⭐ Maker fees < Taker fees (use limit orders when possible)
3. ⭐⭐ Minimum profitable spread = Total fee % / Time horizon
4. ⭐ Different DEXes have different fee schedules
5. ⭐ Fees can turn profitable opportunities unprofitable

Extract for your code:
----------------------
1. FEE_SCHEDULES dict → Update with actual DEX fees
2. FeeCalculator class → strategies/components/fee_calculator.py
3. calculate_total_cost() → Use in profitability checks
4. get_minimum_profitable_spread() → Filter opportunities

Common Mistakes to Avoid:
-------------------------
❌ Only accounting for entry fees (forgetting exit)
❌ Only accounting for one side (forgetting both long and short)
❌ Assuming all DEXes have same fees
❌ Not differentiating maker/taker fees
❌ Comparing gross profit to funding spread without subtracting fees

Integration with funding_analyzer.py:
--------------------------------------
# In your funding_analyzer.py
from strategies.components.fee_calculator import FeeCalculator

def calculate_profitability_after_fees(
    dex1, dex2, funding_spread, position_size
):
    fee_calc = FeeCalculator()
    
    # Get total fees
    total_fees = fee_calc.calculate_total_cost(
        dex1, dex2, position_size, is_maker=True
    )
    
    # Calculate net profit
    gross_profit = position_size * funding_spread
    net_profit = gross_profit - total_fees
    
    return net_profit / position_size  # Return as percentage

Real-World Example:
-------------------
Opportunity shows 0.15% funding spread over 24h.
Looks profitable! But:

- Entry fees: 0.02% × 2 = 0.04%
- Exit fees: 0.02% × 2 = 0.04%
- Total fees: 0.08%

Net profit: 0.15% - 0.08% = 0.07%

Still profitable, but nearly half eaten by fees!
This is why Hummingbot uses fee-adjusted calculations.
"""

