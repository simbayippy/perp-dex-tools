"""
PATTERN 3: Funding Rate Calculations ⭐⭐⭐⭐⭐
=============================================

Extracted from: Hummingbot v2_funding_rate_arb.py
Source: docs/hummingbot_reference/cli_display/v2_funding_rate_arb.py

⭐ THIS IS THE MOST CRITICAL PATTERN ⭐

Purpose:
--------
Correctly calculate funding arbitrage profitability accounting for:
1. Different funding intervals across DEXes (1h vs 8h)
2. Fee-adjusted profitability
3. Finding the best DEX pair

Why This Pattern?
-----------------
✅ Battle-tested by Hummingbot users in production
✅ Handles the complexity of different funding intervals
✅ Accounts for entry/exit fees correctly
✅ Prevents unprofitable trades that look profitable

Key Insight:
------------
You CANNOT compare funding rates directly across DEXes!
- Binance: 8-hour funding (0.01% per 8h)
- Hyperliquid: 1-hour funding (0.01% per 1h)

The 0.01% on Hyperliquid is 8x more valuable than Binance!

MUST normalize to per-second rate first.

"""

from decimal import Decimal
from typing import Dict, Tuple, Optional


# ============================================================================
# CORE PATTERN: Funding Intervals by DEX
# ============================================================================

# From v2_funding_rate_arb.py lines 83-86
FUNDING_PAYMENT_INTERVALS = {
    'hyperliquid_perpetual': 60 * 60 * 1,  # 1 hour = 3,600 seconds
    'lighter': 60 * 60 * 1,                 # 1 hour
    'backpack': 60 * 60 * 8,                # 8 hours
    'grvt': 60 * 60 * 8,                    # 8 hours
    'paradex': 60 * 60 * 8,                 # 1 hour 
    'edgex': 60 * 60 * 1,                   # 1 hour 
    'aster': 60 * 60 * 8,                   # 8 hours 
}

# How long to calculate profitability over (24 hours)
FUNDING_PROFITABILITY_INTERVAL = 60 * 60 * 24  # 24 hours


# ============================================================================
# CORE PATTERN: Rate Normalization
# ============================================================================

def get_normalized_funding_rate_in_seconds(
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
    >>> normalized = get_normalized_funding_rate_in_seconds('lighter', lighter_rate)
    >>> # normalized = 0.0001 / 3600 = 2.77e-8 per second
    
    >>> backpack_rate = Decimal("0.0001")  # 0.01% per 8 hours
    >>> normalized = get_normalized_funding_rate_in_seconds('backpack', backpack_rate)
    >>> # normalized = 0.0001 / 28800 = 3.47e-9 per second
    >>> # Note: Lighter's rate is 8x higher when normalized!
    """
    interval_seconds = FUNDING_PAYMENT_INTERVALS.get(
        dex_name,
        60 * 60 * 8  # Default to 8 hours if unknown
    )
    
    # Rate per second
    return funding_rate / Decimal(str(interval_seconds))


# ============================================================================
# CORE PATTERN: Profitability Calculation
# ============================================================================

def calculate_profitability_after_fees(
    dex1_name: str,
    dex2_name: str,
    dex1_funding_rate: Decimal,
    dex2_funding_rate: Decimal,
    position_size_usd: Decimal,
    dex1_fee_pct: Decimal,
    dex2_fee_pct: Decimal,
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
    4. Subtract entry fees (open both sides)
    5. Subtract exit fees (close both sides)
    
    Net Profit = (Rate Spread * Horizon) - (Entry Fees + Exit Fees)
    
    Args:
        dex1_name: First DEX
        dex2_name: Second DEX
        dex1_funding_rate: Raw funding rate on DEX1
        dex2_funding_rate: Raw funding rate on DEX2
        position_size_usd: Position size in USD
        dex1_fee_pct: Trading fee on DEX1 (e.g., 0.0005 = 0.05%)
        dex2_fee_pct: Trading fee on DEX2
        profitability_horizon_hours: Calculate profit over N hours (default 24)
    
    Returns:
        Net profitability as percentage (e.g., 0.01 = 1%)
    
    Example:
    --------
    >>> # Lighter: 0.01% per hour, GRVT: 0.005% per 8 hours
    >>> profit = calculate_profitability_after_fees(
    ...     'lighter', 'grvt',
    ...     Decimal('0.0001'), Decimal('0.00005'),
    ...     Decimal('10000'),  # $10k position
    ...     Decimal('0.0005'), Decimal('0.0004'),  # Fees
    ...     24  # 24 hour horizon
    ... )
    >>> # Returns net profit percentage after fees
    """
    # Step 1: Normalize rates to per-second
    rate1_per_sec = get_normalized_funding_rate_in_seconds(dex1_name, dex1_funding_rate)
    rate2_per_sec = get_normalized_funding_rate_in_seconds(dex2_name, dex2_funding_rate)
    
    # Step 2: Calculate rate difference (spread)
    rate_diff_per_sec = abs(rate1_per_sec - rate2_per_sec)
    
    # Step 3: Scale to desired time horizon
    horizon_seconds = profitability_horizon_hours * 60 * 60
    annualized_spread = rate_diff_per_sec * Decimal(str(horizon_seconds))
    
    # Step 4: Calculate total fees
    # Entry: Open long on one DEX, open short on another
    entry_fee_dex1 = dex1_fee_pct
    entry_fee_dex2 = dex2_fee_pct
    
    # Exit: Close both positions
    exit_fee_dex1 = dex1_fee_pct
    exit_fee_dex2 = dex2_fee_pct
    
    # Total fee percentage
    total_fee_pct = entry_fee_dex1 + entry_fee_dex2 + exit_fee_dex1 + exit_fee_dex2
    
    # Step 5: Net profitability
    net_profitability = annualized_spread - total_fee_pct
    
    return net_profitability


# ============================================================================
# CORE PATTERN: Find Best DEX Pair
# ============================================================================

def get_most_profitable_combination(
    symbol: str,
    funding_rates: Dict[str, Decimal],
    position_size_usd: Decimal,
    fee_schedules: Dict[str, Decimal],
    profitability_horizon_hours: int = 24
) -> Tuple[Optional[str], Optional[str], Optional[str], Decimal]:
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
        position_size_usd: Position size
        fee_schedules: {dex_name: fee_pct}
        profitability_horizon_hours: Calculate over N hours
    
    Returns:
        (long_dex, short_dex, side_label, profitability)
        
        side_label:
            "SHORT_LONG" means short on first DEX, long on second
            None if no profitable combination
    
    Example:
    --------
    >>> funding_rates = {
    ...     'lighter': Decimal('0.0001'),     # 0.01% per hour
    ...     'backpack': Decimal('0.00005'),   # 0.005% per 8 hours
    ...     'grvt': Decimal('0.00008'),       # 0.008% per 8 hours
    ... }
    >>> fees = {
    ...     'lighter': Decimal('0.0005'),
    ...     'backpack': Decimal('0.0005'),
    ...     'grvt': Decimal('0.0004'),
    ... }
    >>> long, short, side, profit = get_most_profitable_combination(
    ...     'BTC', funding_rates, Decimal('10000'), fees
    ... )
    >>> print(f"Best: Long {long}, Short {short}, Profit: {profit*100:.2f}%")
    """
    dex_names = list(funding_rates.keys())
    
    best_long = None
    best_short = None
    best_side = None
    highest_profitability = Decimal("0")
    
    # Try all pairs
    for i, dex1 in enumerate(dex_names):
        for dex2 in dex_names[i+1:]:  # Only try each pair once
            # Calculate profitability for this pair
            profitability = calculate_profitability_after_fees(
                dex1, dex2,
                funding_rates[dex1],
                funding_rates[dex2],
                position_size_usd,
                fee_schedules.get(dex1, Decimal('0.001')),
                fee_schedules.get(dex2, Decimal('0.001')),
                profitability_horizon_hours
            )
            
            # Only consider if profitable
            if profitability > highest_profitability:
                highest_profitability = profitability
                
                # Determine which DEX to long/short
                # Rule: Short the high rate (you receive funding)
                #       Long the low rate (you pay funding)
                rate1_normalized = get_normalized_funding_rate_in_seconds(
                    dex1, funding_rates[dex1]
                )
                rate2_normalized = get_normalized_funding_rate_in_seconds(
                    dex2, funding_rates[dex2]
                )
                
                if rate1_normalized > rate2_normalized:
                    # DEX1 has higher rate → short DEX1, long DEX2
                    best_short = dex1
                    best_long = dex2
                    best_side = "SHORT_LONG"
                else:
                    # DEX2 has higher rate → short DEX2, long DEX1
                    best_short = dex2
                    best_long = dex1
                    best_side = "SHORT_LONG"
    
    return (best_long, best_short, best_side, highest_profitability)


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

def example_1_rate_normalization():
    """
    Example: Why normalization is critical
    """
    print("=" * 60)
    print("Example 1: Rate Normalization")
    print("=" * 60)
    
    # Same raw rate on different DEXes
    raw_rate = Decimal("0.0001")  # 0.01%
    
    # Lighter: 1 hour interval
    lighter_normalized = get_normalized_funding_rate_in_seconds('lighter', raw_rate)
    lighter_daily = lighter_normalized * 86400  # Scale to 24 hours
    
    # Backpack: 8 hour interval
    backpack_normalized = get_normalized_funding_rate_in_seconds('backpack', raw_rate)
    backpack_daily = backpack_normalized * 86400
    
    print(f"\nRaw Rate: {raw_rate} (0.01%)")
    print(f"\nLighter (1h interval):")
    print(f"  Normalized: {lighter_normalized:.10f} per second")
    print(f"  Daily Rate: {lighter_daily * 100:.4f}%")
    print(f"\nBackpack (8h interval):")
    print(f"  Normalized: {backpack_normalized:.10f} per second")
    print(f"  Daily Rate: {backpack_daily * 100:.4f}%")
    print(f"\nLighter is {lighter_daily / backpack_daily:.1f}x more valuable!")


def example_2_profitability_calculation():
    """
    Example: Calculate net profitability
    """
    print("\n" + "=" * 60)
    print("Example 2: Profitability Calculation")
    print("=" * 60)
    
    profit = calculate_profitability_after_fees(
        dex1_name='lighter',
        dex2_name='backpack',
        dex1_funding_rate=Decimal('0.0001'),   # 0.01% per hour
        dex2_funding_rate=Decimal('0.00005'),  # 0.005% per 8 hours
        position_size_usd=Decimal('10000'),
        dex1_fee_pct=Decimal('0.0005'),        # 0.05% fee
        dex2_fee_pct=Decimal('0.0005'),
        profitability_horizon_hours=24
    )
    
    print(f"\nLighter: 0.01% per hour")
    print(f"Backpack: 0.005% per 8 hours")
    print(f"Position Size: $10,000")
    print(f"Fees: 0.05% on each DEX")
    print(f"\nNet Profitability (24h): {profit * 100:.4f}%")
    print(f"Expected Profit: ${profit * Decimal('10000'):.2f}")


def example_3_find_best_pair():
    """
    Example: Find best DEX combination
    """
    print("\n" + "=" * 60)
    print("Example 3: Find Best DEX Pair")
    print("=" * 60)
    
    funding_rates = {
        'lighter': Decimal('0.0002'),      # 0.02% per hour (high)
        'backpack': Decimal('0.00005'),    # 0.005% per 8 hours (low)
        'grvt': Decimal('0.00008'),        # 0.008% per 8 hours (medium)
    }
    
    fee_schedules = {
        'lighter': Decimal('0.0005'),
        'backpack': Decimal('0.0005'),
        'grvt': Decimal('0.0004'),
    }
    
    long_dex, short_dex, side, profit = get_most_profitable_combination(
        symbol='BTC',
        funding_rates=funding_rates,
        position_size_usd=Decimal('10000'),
        fee_schedules=fee_schedules,
        profitability_horizon_hours=24
    )
    
    print(f"\nFunding Rates:")
    for dex, rate in funding_rates.items():
        interval = FUNDING_PAYMENT_INTERVALS.get(dex, 28800) / 3600
        print(f"  {dex}: {rate * 100:.3f}% per {interval}h")
    
    print(f"\nBest Combination:")
    print(f"  Long: {long_dex}")
    print(f"  Short: {short_dex}")
    print(f"  Net Profit (24h): {profit * 100:.4f}%")
    print(f"  Expected: ${profit * Decimal('10000'):.2f}")


# ============================================================================
# RUN EXAMPLES
# ============================================================================

if __name__ == "__main__":
    example_1_rate_normalization()
    example_2_profitability_calculation()
    example_3_find_best_pair()


# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

"""
1. ⭐⭐⭐ ALWAYS normalize funding rates before comparing DEXes
2. ⭐⭐⭐ Account for both entry AND exit fees (4 trades total)
3. ⭐⭐ Use per-second normalization for precision
4. ⭐ Test all DEX combinations to find best pair
5. ⭐ Calculate over realistic time horizon (24h typical)

Extract for your code:
----------------------
1. FUNDING_PAYMENT_INTERVALS dict → funding_analyzer.py
2. get_normalized_funding_rate_in_seconds() → CRITICAL, copy exactly
3. calculate_profitability_after_fees() → CRITICAL, copy exactly
4. get_most_profitable_combination() → Use in strategy.py

Common Mistakes to Avoid:
-------------------------
❌ Comparing raw rates without normalization
❌ Forgetting exit fees (only accounting for entry)
❌ Using simple subtraction instead of per-second normalization
❌ Not testing all DEX combinations
❌ Assuming all DEXes have 8-hour funding

Integration with your funding_rate_service:
--------------------------------------------
Your service stores raw rates in PostgreSQL.
When comparing rates for opportunities:

1. Fetch raw rates from DB
2. Pass through get_normalized_funding_rate_in_seconds()
3. Calculate profitability with fees
4. Filter by min_profitability threshold
5. Return top opportunities

This is MORE accurate than simple rate difference!
"""

