"""
Fee Calculator

Calculates trading fees for funding rate arbitrage opportunities.
Supports maker/taker fees, fee tiers, and position sizing.
"""

from decimal import Decimal
from typing import Optional, Dict
from dataclasses import dataclass

from utils.logger import logger


@dataclass
class FeeStructure:
    """Trading fee structure for a DEX"""
    maker_fee: Decimal  # e.g., 0.0002 = 0.02%
    taker_fee: Decimal  # e.g., 0.0005 = 0.05%
    name: str = "default"


@dataclass
class TradingCosts:
    """
    Breakdown of trading costs for an arbitrage opportunity
    """
    entry_fee: Decimal  # Fee to open position
    exit_fee: Decimal   # Fee to close position
    total_fee: Decimal  # Total fee (entry + exit)
    total_fee_bps: Decimal  # Total fee in basis points
    net_rate: Decimal   # Funding rate after fees
    net_apy: Decimal    # Annualized APY after fees
    is_profitable: bool  # Whether the opportunity is profitable after fees
    
    def __repr__(self) -> str:
        return (
            f"TradingCosts(total={float(self.total_fee_bps):.2f}bps, "
            f"net_rate={float(self.net_rate):.6f}, "
            f"net_apy={float(self.net_apy):.2f}%, "
            f"profitable={self.is_profitable})"
        )


class FeeCalculator:
    """
    Calculate trading fees for funding rate arbitrage
    
    Funding rate arbitrage involves:
    1. Going LONG on DEX A (pay/receive funding)
    2. Going SHORT on DEX B (receive/pay funding)
    
    Fees are incurred:
    - Entry: Opening both positions (2 trades)
    - Exit: Closing both positions (2 trades)
    
    For maker orders: 4 x maker_fee
    For taker orders: 4 x taker_fee
    For mixed: 2 x maker_fee + 2 x taker_fee
    """
    
    # Default fee structures for common DEXs
    DEFAULT_FEES: Dict[str, FeeStructure] = {
        'lighter': FeeStructure(
            maker_fee=Decimal('0.0000'),  # 
            taker_fee=Decimal('0.0000'),  # 
            name='lighter'
        ),
        'grvt': FeeStructure(
            maker_fee=Decimal('0.0002'),  # 0.02%
            taker_fee=Decimal('0.0005'),  # 0.05%
            name='grvt'
        ),
        'edgex': FeeStructure(
            maker_fee=Decimal('0.0002'),  # 0.02%
            taker_fee=Decimal('0.00055'), # 0.055%
            name='edgex'
        ),
        'hyperliquid': FeeStructure(
            maker_fee=Decimal('0.00020'),  # 0.02%
            taker_fee=Decimal('0.00045'),  # 0.045%
            name='hyperliquid'
        ),
        'default': FeeStructure(
            maker_fee=Decimal('0.0003'),  # Conservative default
            taker_fee=Decimal('0.0006'),
            name='default'
        ),
    }
    
    # Funding rate payment frequency (most DEXs use 8 hours)
    FUNDING_INTERVAL_HOURS = Decimal('8')
    HOURS_PER_YEAR = Decimal('8760')  # 365 * 24
    
    def __init__(self):
        """Initialize fee calculator"""
        self.fee_structures = self.DEFAULT_FEES.copy()
        logger.info(f"FeeCalculator initialized with {len(self.fee_structures)} DEX fee structures")
    
    def add_fee_structure(
        self,
        dex_name: str,
        maker_fee: Decimal,
        taker_fee: Decimal
    ) -> None:
        """
        Add or update fee structure for a DEX
        
        Args:
            dex_name: Name of the DEX
            maker_fee: Maker fee as decimal (e.g., 0.0002 for 0.02%)
            taker_fee: Taker fee as decimal
        """
        self.fee_structures[dex_name.lower()] = FeeStructure(
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            name=dex_name.lower()
        )
        logger.info(
            f"Added fee structure for {dex_name}: "
            f"maker={float(maker_fee)*10000:.1f}bps, "
            f"taker={float(taker_fee)*10000:.1f}bps"
        )
    
    def get_fee_structure(self, dex_name: str) -> FeeStructure:
        """
        Get fee structure for a DEX
        
        Args:
            dex_name: Name of the DEX
            
        Returns:
            Fee structure (uses default if DEX not found)
        """
        dex_name_lower = dex_name.lower()
        if dex_name_lower not in self.fee_structures:
            logger.warning(
                f"No fee structure found for {dex_name}, using default"
            )
            return self.fee_structures['default']
        
        return self.fee_structures[dex_name_lower]
    
    def calculate_costs(
        self,
        dex_long: str,
        dex_short: str,
        funding_rate_long: Decimal,
        funding_rate_short: Decimal,
        use_maker_orders: bool = True,
        position_size_usd: Optional[Decimal] = None
    ) -> TradingCosts:
        """
        Calculate trading costs for a funding rate arbitrage opportunity
        
        Args:
            dex_long: DEX where we go LONG
            dex_short: DEX where we go SHORT
            funding_rate_long: Funding rate on long DEX (we pay if positive)
            funding_rate_short: Funding rate on short DEX (we receive if positive)
            use_maker_orders: Whether to use maker orders (default: True)
            position_size_usd: Position size in USD (for absolute fee calculation)
            
        Returns:
            TradingCosts with detailed breakdown
            
        Example:
            # BTC funding: +0.01% on Lighter, -0.005% on GRVT
            # Profit = go LONG on GRVT (pay -0.005%), SHORT on Lighter (receive +0.01%)
            # Net funding = 0.01% - (-0.005%) = 0.015% per 8h
            costs = calculator.calculate_costs(
                dex_long='grvt',
                dex_short='lighter',
                funding_rate_long=Decimal('-0.00005'),
                funding_rate_short=Decimal('0.0001')
            )
        """
        # Get fee structures
        fees_long = self.get_fee_structure(dex_long)
        fees_short = self.get_fee_structure(dex_short)
        
        # Select fee type (maker or taker)
        fee_long = fees_long.maker_fee if use_maker_orders else fees_long.taker_fee
        fee_short = fees_short.maker_fee if use_maker_orders else fees_short.taker_fee
        
        # Entry: Open LONG on dex_long + Open SHORT on dex_short
        entry_fee = fee_long + fee_short
        
        # Exit: Close LONG on dex_long + Close SHORT on dex_short
        exit_fee = fee_long + fee_short
        
        # Total fee (round trip)
        total_fee = entry_fee + exit_fee
        
        # Convert to basis points (1 bps = 0.0001 = 0.01%)
        total_fee_bps = total_fee * Decimal('10000')
        
        # Calculate funding profit
        # We RECEIVE funding_rate_short (from short position)
        # We PAY funding_rate_long (from long position)
        # Net = what we receive - what we pay
        funding_profit = funding_rate_short - funding_rate_long
        
        # Net rate after fees (per funding interval)
        net_rate = funding_profit - total_fee
        
        # Annualized APY
        # Funding happens every 8 hours (3x per day, ~1095x per year)
        payments_per_year = self.HOURS_PER_YEAR / self.FUNDING_INTERVAL_HOURS
        net_apy = net_rate * payments_per_year * Decimal('100')  # Convert to percentage
        
        # Profitable if net rate is positive
        is_profitable = net_rate > 0
        
        costs = TradingCosts(
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            total_fee=total_fee,
            total_fee_bps=total_fee_bps,
            net_rate=net_rate,
            net_apy=net_apy,
            is_profitable=is_profitable
        )
        
        logger.debug(
            f"Calculated costs for {dex_long} LONG / {dex_short} SHORT: "
            f"fees={float(total_fee_bps):.2f}bps, "
            f"net_rate={float(net_rate):.6f}, "
            f"net_apy={float(net_apy):.2f}%"
        )
        
        return costs
    
    def calculate_absolute_profit(
        self,
        costs: TradingCosts,
        position_size_usd: Decimal,
        holding_periods: int = 1
    ) -> Dict[str, Decimal]:
        """
        Calculate absolute profit in USD
        
        Args:
            costs: Trading costs from calculate_costs()
            position_size_usd: Position size in USD (e.g., 10000 for $10k)
            holding_periods: Number of funding intervals to hold (default: 1)
            
        Returns:
            Dictionary with profit breakdown:
            {
                'gross_profit': Decimal,     # Profit before fees
                'total_fees': Decimal,       # Total fees paid
                'net_profit': Decimal,       # Net profit after fees
                'roi': Decimal               # Return on investment (%)
            }
        """
        # Gross profit (before fees)
        gross_profit = (costs.net_rate + costs.total_fee) * position_size_usd * holding_periods
        
        # Total fees
        total_fees = costs.total_fee * position_size_usd
        
        # Net profit
        net_profit = costs.net_rate * position_size_usd * holding_periods
        
        # ROI
        roi = (net_profit / position_size_usd) * Decimal('100') if position_size_usd > 0 else Decimal('0')
        
        return {
            'gross_profit': gross_profit,
            'total_fees': total_fees,
            'net_profit': net_profit,
            'roi': roi
        }
    
    def compare_opportunities(
        self,
        opportunities: list,
        position_size_usd: Decimal = Decimal('10000')
    ) -> list:
        """
        Compare multiple opportunities and rank by profitability
        
        Args:
            opportunities: List of dicts with 'dex_long', 'dex_short', 'rate_long', 'rate_short'
            position_size_usd: Position size for comparison
            
        Returns:
            Sorted list of opportunities with costs and rankings
        """
        results = []
        
        for opp in opportunities:
            costs = self.calculate_costs(
                dex_long=opp['dex_long'],
                dex_short=opp['dex_short'],
                funding_rate_long=opp['rate_long'],
                funding_rate_short=opp['rate_short']
            )
            
            profit = self.calculate_absolute_profit(costs, position_size_usd)
            
            results.append({
                **opp,
                'costs': costs,
                'profit': profit,
                'net_apy': costs.net_apy
            })
        
        # Sort by net APY (descending)
        results.sort(key=lambda x: x['net_apy'], reverse=True)
        
        return results


# Global instance
fee_calculator = FeeCalculator()

