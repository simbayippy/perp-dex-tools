"""
Funding Rate Analyzer - EXTRACTED from Hummingbot v2_funding_rate_arb.py

⭐ This is the CORE logic from Hummingbot ⭐

Critical for funding arbitrage:
- Rate normalization (different DEXes have different intervals)
- Fee-adjusted profitability calculation
- Finding best DEX pair

Source: docs/hummingbot_reference/cli_display/v2_funding_rate_arb.py
Reference: docs/hummingbot_patterns/funding_rate_calcs.py
"""

from decimal import Decimal
from typing import Dict, Tuple, Optional, List
from strategies.components import TradeFeeCalculator


# ============================================================================
# Funding Intervals by DEX
# ============================================================================

# From v2_funding_rate_arb.py lines 83-86
# Different DEXes pay funding at different intervals!
FUNDING_PAYMENT_INTERVALS = {
    # DEX name: seconds between funding payments
    'hyperliquid': 60 * 60 * 1,  # 1 hour = 3,600 seconds
    'lighter': 60 * 60 * 1,       # 1 hour
    'backpack': 60 * 60 * 8,      # 8 hours = 28,800 seconds
    'grvt': 60 * 60 * 8,          # 8 hours
    'paradex': 60 * 60 * 1,       # 1 hour 
    'edgex': 60 * 60 * 1,         # 1 hour 
    'aster': 60 * 60 * 8,         # 8 hours 
}

# How long to calculate profitability over (24 hours)
FUNDING_PROFITABILITY_INTERVAL = 60 * 60 * 24  # 24 hours


# ============================================================================
# Funding Rate Analyzer
# ============================================================================

class FundingRateAnalyzer:
    """
    Critical funding rate calculations.
    
    Direct extraction from Hummingbot v2_funding_rate_arb.py
    
    Why this is critical:
    --------------------
    You CANNOT compare funding rates directly across DEXes!
    
    Example:
    - Lighter: 0.01% per hour (pays 24x per day)
    - Backpack: 0.01% per 8 hours (pays 3x per day)
    
    The Lighter rate is 8x more valuable!
    
    Solution: Normalize to per-second rate before comparing.
    """
    
    def __init__(self, funding_intervals: Dict[str, int] = None):
        """
        Initialize analyzer.
        
        Args:
            funding_intervals: Override default intervals (for testing)
        """
        self.funding_intervals = funding_intervals or FUNDING_PAYMENT_INTERVALS
    
    # ========================================================================
    # Core Logic: Rate Normalization
    # ========================================================================
    
    def get_normalized_funding_rate_in_seconds(
        self,
        dex_name: str,
        funding_rate: Decimal
    ) -> Decimal:
        """
        ⭐ CRITICAL FUNCTION from v2_funding_rate_arb.py line 196-197 ⭐
        
        Normalize funding rate to per-second basis for fair comparison.
        
        Why?
        ----
        Different DEXes have different payment intervals:
        - Lighter: 1 hour (3,600 seconds)
        - Backpack: 8 hours (28,800 seconds)
        
        If both show 0.01% funding rate:
        - Lighter: 0.01% per 1 hour = 0.24% per day
        - Backpack: 0.01% per 8 hours = 0.03% per day
        
        Lighter is 8x more valuable!
        
        Args:
            dex_name: Name of the DEX
            funding_rate: The raw funding rate (e.g., 0.0001 = 0.01%)
        
        Returns:
            Funding rate per second (for fair comparison)
        
        Example:
        --------
        >>> lighter_rate = Decimal("0.0001")  # 0.01% per hour
        >>> normalized = analyzer.get_normalized_funding_rate_in_seconds('lighter', lighter_rate)
        >>> # normalized = 0.0001 / 3600 = 2.77e-8 per second
        
        >>> backpack_rate = Decimal("0.0001")  # 0.01% per 8 hours
        >>> normalized = analyzer.get_normalized_funding_rate_in_seconds('backpack', backpack_rate)
        >>> # normalized = 0.0001 / 28800 = 3.47e-9 per second
        >>> # Lighter's rate is 8x higher when normalized!
        """
        interval_seconds = self.funding_intervals.get(
            dex_name,
            60 * 60 * 8  # Default to 8 hours if unknown
        )
        
        # Rate per second
        return funding_rate / Decimal(str(interval_seconds))
    
    # ========================================================================
    # Profitability Calculation
    # ========================================================================
    
    def calculate_profitability_after_fees(
        self,
        symbol: str,
        dex1_name: str,
        dex2_name: str,
        funding_rates: Dict[str, Decimal],
        position_size: Decimal,
        fee_calculator: TradeFeeCalculator,
        profitability_horizon_hours: int = 24
    ) -> Decimal:
        """
        ⭐ CRITICAL FUNCTION from v2_funding_rate_arb.py lines 134-180 ⭐
        
        Calculate NET profitability after all fees.
        
        Formula:
        --------
        1. Normalize funding rates to per-second
        2. Calculate rate difference (arbitrage spread)
        3. Annualize to desired horizon (e.g., 24 hours)
        4. Subtract total fees (entry + exit on both sides)
        
        Net Profit = (Rate Spread × Horizon) - Total Fees
        
        Args:
            symbol: Trading pair (e.g., 'BTC')
            dex1_name: First DEX
            dex2_name: Second DEX
            funding_rates: {dex_name: funding_rate}
            position_size: Position size in USD
            fee_calculator: TradeFeeCalculator instance
            profitability_horizon_hours: Calculate profit over N hours (default 24)
        
        Returns:
            Net profitability as percentage (e.g., 0.01 = 1%)
        
        Example:
        --------
        >>> funding_rates = {
        ...     'lighter': Decimal('0.0001'),  # 0.01% per hour
        ...     'grvt': Decimal('0.00005')     # 0.005% per 8 hours
        ... }
        >>> profit = analyzer.calculate_profitability_after_fees(
        ...     'BTC', 'lighter', 'grvt', funding_rates,
        ...     Decimal('10000'), fee_calc, 24
        ... )
        >>> # Returns net profit percentage after fees
        """
        # Step 1: Normalize rates to per-second
        rate1_per_sec = self.get_normalized_funding_rate_in_seconds(
            dex1_name, funding_rates[dex1_name]
        )
        rate2_per_sec = self.get_normalized_funding_rate_in_seconds(
            dex2_name, funding_rates[dex2_name]
        )
        
        # Step 2: Calculate rate difference (spread)
        rate_diff_per_sec = abs(rate1_per_sec - rate2_per_sec)
        
        # Step 3: Scale to desired time horizon
        horizon_seconds = profitability_horizon_hours * 60 * 60
        annualized_spread = rate_diff_per_sec * Decimal(str(horizon_seconds))
        
        # Step 4: Calculate total fees (4 trades: open long, open short, close long, close short)
        total_fees = fee_calculator.calculate_total_cost(
            dex1_name, dex2_name, position_size, is_maker=True
        )
        fee_pct = total_fees / position_size
        
        # Step 5: Net profitability
        net_profitability = annualized_spread - fee_pct
        
        return net_profitability
    
    # ========================================================================
    # Find Best DEX Pair
    # ========================================================================
    
    def find_best_opportunity(
        self,
        symbol: str,
        funding_rates: Dict[str, Decimal],
        position_size: Decimal,
        fee_calculator: TradeFeeCalculator,
        profitability_horizon_hours: int = 24
    ) -> Tuple[Optional[str], Optional[str], Decimal]:
        """
        ⭐ CRITICAL FUNCTION from v2_funding_rate_arb.py lines 181-194 ⭐
        
        Find the most profitable DEX pair for funding arbitrage.
        
        Strategy:
        ---------
        1. Try all combinations of DEX pairs
        2. For each pair, calculate net profitability
        3. Determine which side to long/short based on rates
        4. Return the best combination
        
        Args:
            symbol: Trading pair (e.g., 'BTC')
            funding_rates: {dex_name: funding_rate}
            position_size: Position size in USD
            fee_calculator: TradeFeeCalculator instance
            profitability_horizon_hours: Calculate over N hours
        
        Returns:
            (long_dex, short_dex, profitability)
            
            Returns (None, None, 0) if no profitable combination
        
        Logic:
        ------
        - High funding rate = Short (you receive funding)
        - Low funding rate = Long (you pay funding)
        
        Example:
        --------
        >>> funding_rates = {
        ...     'lighter': Decimal('0.0002'),     # High rate
        ...     'backpack': Decimal('0.00005'),   # Low rate
        ...     'grvt': Decimal('0.00008'),       # Medium rate
        ... }
        >>> long, short, profit = analyzer.find_best_opportunity(
        ...     'BTC', funding_rates, Decimal('10000'), fee_calc
        ... )
        >>> print(f"Long {long}, Short {short}, Profit: {profit*100:.2f}%")
        """
        dex_names = list(funding_rates.keys())
        
        best_long = None
        best_short = None
        highest_profitability = Decimal("0")
        
        # Try all pairs
        for i, dex1 in enumerate(dex_names):
            for dex2 in dex_names[i+1:]:  # Only try each pair once
                # Calculate profitability for this pair
                profitability = self.calculate_profitability_after_fees(
                    symbol, dex1, dex2, funding_rates,
                    position_size, fee_calculator,
                    profitability_horizon_hours
                )
                
                # Only consider if profitable
                if profitability > highest_profitability:
                    highest_profitability = profitability
                    
                    # Determine which DEX to long/short
                    # Rule: Short the high rate (you receive funding)
                    #       Long the low rate (you pay funding)
                    rate1_normalized = self.get_normalized_funding_rate_in_seconds(
                        dex1, funding_rates[dex1]
                    )
                    rate2_normalized = self.get_normalized_funding_rate_in_seconds(
                        dex2, funding_rates[dex2]
                    )
                    
                    if rate1_normalized > rate2_normalized:
                        # DEX1 has higher rate → short DEX1, long DEX2
                        best_short = dex1
                        best_long = dex2
                    else:
                        # DEX2 has higher rate → short DEX2, long DEX1
                        best_short = dex2
                        best_long = dex1
        
        return (best_long, best_short, highest_profitability)
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def get_funding_interval(self, dex_name: str) -> int:
        """
        Get funding payment interval for a DEX.
        
        Args:
            dex_name: DEX name
            
        Returns:
            Seconds between funding payments
        """
        return self.funding_intervals.get(dex_name, 60 * 60 * 8)
    
    def update_funding_interval(self, dex_name: str, interval_seconds: int):
        """
        Update funding interval for a DEX.
        
        Use this when DEX changes their funding schedule.
        
        Args:
            dex_name: DEX name
            interval_seconds: New interval in seconds
        """
        self.funding_intervals[dex_name] = interval_seconds

