#!/usr/bin/env python3
"""
Test script for Phase 3: Business Logic

This script demonstrates:
1. Fee Calculator - calculating trading fees and net profitability
2. Opportunity Finder - finding arbitrage opportunities from collected data

Usage:
    # Test fee calculator only
    python scripts/test_phase3.py --fees-only
    
    # Test opportunity finder (requires database with funding rates)
    python scripts/test_phase3.py
    
    # Test with specific symbol
    python scripts/test_phase3.py --symbol BTC
"""

import asyncio
import sys
from pathlib import Path
from decimal import Decimal
import argparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fee_calculator import FeeCalculator, fee_calculator
from core.opportunity_finder import init_opportunity_finder
from models.filters import OpportunityFilter
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


async def test_fee_calculator():
    """Test the fee calculator"""
    print("\n" + "="*70)
    print("TEST: Fee Calculator")
    print("="*70 + "\n")
    
    calc = fee_calculator
    
    # Test 1: Basic fee calculation
    print("📊 Test 1: Basic Fee Calculation")
    print("-"*70)
    
    costs = calc.calculate_costs(
        dex_long='grvt',
        dex_short='lighter',
        funding_rate_long=Decimal('-0.00005'),  # Pay -0.005% (receive)
        funding_rate_short=Decimal('0.0001'),    # Receive +0.01%
        use_maker_orders=True
    )
    
    print(f"Scenario: Go LONG on GRVT (-0.005%), SHORT on Lighter (+0.01%)")
    print(f"  Entry Fee:        {float(costs.entry_fee)*10000:.2f} bps")
    print(f"  Exit Fee:         {float(costs.exit_fee)*10000:.2f} bps")
    print(f"  Total Fees:       {float(costs.total_fee_bps):.2f} bps")
    print(f"  Funding Profit:   {float(costs.net_rate + costs.total_fee):.6f} ({float((costs.net_rate + costs.total_fee)*100):.4f}%)")
    print(f"  Net Rate:         {float(costs.net_rate):.6f} ({float(costs.net_rate*100):.4f}%)")
    print(f"  Annualized APY:   {float(costs.net_apy):.2f}%")
    print(f"  Profitable:       {'✅ YES' if costs.is_profitable else '❌ NO'}\n")
    
    # Test 2: Compare maker vs taker
    print("📊 Test 2: Maker vs Taker Orders")
    print("-"*70)
    
    maker_costs = calc.calculate_costs(
        dex_long='lighter',
        dex_short='edgex',
        funding_rate_long=Decimal('0.00005'),
        funding_rate_short=Decimal('0.00015'),
        use_maker_orders=True
    )
    
    taker_costs = calc.calculate_costs(
        dex_long='lighter',
        dex_short='edgex',
        funding_rate_long=Decimal('0.00005'),
        funding_rate_short=Decimal('0.00015'),
        use_maker_orders=False
    )
    
    print(f"Scenario: Lighter LONG (+0.005%), EdgeX SHORT (+0.015%)")
    print(f"\n  Maker Orders:")
    print(f"    Fees:       {float(maker_costs.total_fee_bps):.2f} bps")
    print(f"    Net APY:    {float(maker_costs.net_apy):.2f}%")
    print(f"    Profitable: {'✅' if maker_costs.is_profitable else '❌'}")
    
    print(f"\n  Taker Orders:")
    print(f"    Fees:       {float(taker_costs.total_fee_bps):.2f} bps")
    print(f"    Net APY:    {float(taker_costs.net_apy):.2f}%")
    print(f"    Profitable: {'✅' if taker_costs.is_profitable else '❌'}")
    
    print(f"\n  💡 Maker orders save {float(taker_costs.total_fee_bps - maker_costs.total_fee_bps):.2f} bps in fees!\n")
    
    # Test 3: Absolute profit calculation
    print("📊 Test 3: Absolute Profit Calculation")
    print("-"*70)
    
    position_sizes = [Decimal('1000'), Decimal('10000'), Decimal('100000')]
    
    for size in position_sizes:
        profit = calc.calculate_absolute_profit(maker_costs, size, holding_periods=1)
        print(f"  Position Size: ${float(size):,.0f}")
        print(f"    Gross Profit: ${float(profit['gross_profit']):.2f}")
        print(f"    Total Fees:   ${float(profit['total_fees']):.2f}")
        print(f"    Net Profit:   ${float(profit['net_profit']):.2f}")
        print(f"    ROI:          {float(profit['roi']):.4f}%\n")
    
    print("✅ Fee calculator tests passed!\n")
    return True


async def test_opportunity_finder(symbol_filter: str = None):
    """Test the opportunity finder (requires database)"""
    print("\n" + "="*70)
    print("TEST: Opportunity Finder (with Database)")
    print("="*70 + "\n")
    
    try:
        # Connect to database
        print("🔌 Connecting to database...")
        await database.connect()
        print("✅ Database connected\n")
        
        # Load mappers
        print("📚 Loading mappers...")
        await dex_mapper.load_from_db(database)
        await symbol_mapper.load_from_db(database)
        print(f"✅ Loaded {len(dex_mapper)} DEXs and {len(symbol_mapper)} symbols\n")
        
        # Initialize opportunity finder
        print("🔍 Initializing opportunity finder...")
        finder = init_opportunity_finder(
            database=database,
            fee_calculator=fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        print("✅ Opportunity finder ready\n")
        
        # Test 1: Find all opportunities
        print("📊 Test 1: Find All Opportunities")
        print("-"*70)
        
        filters = OpportunityFilter(
            min_profit_percent=Decimal('0'),  # Any positive profit
            limit=10
        )
        
        if symbol_filter:
            filters.symbol = symbol_filter
            print(f"Filtering by symbol: {symbol_filter}\n")
        
        opportunities = await finder.find_opportunities(filters)
        
        print(f"Found {len(opportunities)} profitable opportunities\n")
        
        if opportunities:
            print(f"{'Rank':<5} {'Symbol':<8} {'Long DEX':<12} {'Short DEX':<12} {'Net APY':<12} {'Profit':<12}")
            print("-"*70)
            
            for i, opp in enumerate(opportunities[:10], 1):
                print(
                    f"{i:<5} {opp.symbol:<8} {opp.long_dex:<12} {opp.short_dex:<12} "
                    f"{float(opp.annualized_apy):>10.2f}% "
                    f"{float(opp.net_profit_percent):>10.6f}"
                )
            
            print()
            
            # Show best opportunity details
            best = opportunities[0]
            print(f"🏆 Best Opportunity:")
            print(f"   Symbol:           {best.symbol}")
            print(f"   Strategy:         LONG {best.long_dex} / SHORT {best.short_dex}")
            print(f"   Long Rate:        {float(best.long_rate):.6f} ({float(best.long_rate*100):.4f}%)")
            print(f"   Short Rate:       {float(best.short_rate):.6f} ({float(best.short_rate*100):.4f}%)")
            print(f"   Divergence:       {float(best.divergence):.6f} ({float(best.divergence*100):.4f}%)")
            print(f"   Estimated Fees:   {float(best.estimated_fees):.6f} ({float(best.estimated_fees*100):.4f}%)")
            print(f"   Net Profit:       {float(best.net_profit_percent):.6f} ({float(best.net_profit_percent*100):.4f}%)")
            print(f"   Annualized APY:   {float(best.annualized_apy):.2f}%")
            
            if best.min_volume_24h:
                print(f"   Min 24h Volume:   ${float(best.min_volume_24h):,.0f}")
            if best.min_oi_usd:
                print(f"   Min OI:           ${float(best.min_oi_usd):,.0f}")
                print(f"   OI Imbalance:     {best.oi_imbalance}")
            
            print()
        else:
            print("⚠️  No profitable opportunities found")
            print("   This might be because:")
            print("   1. No funding rate data in database yet")
            print("   2. All current rates result in negative profit after fees")
            print("   3. Run collection first: python scripts/test_collection_system.py\n")
        
        # Test 2: Filter by low OI (if we have opportunities)
        if opportunities and not symbol_filter:
            print("📊 Test 2: Low OI Opportunities (< $2M)")
            print("-"*70)
            
            low_oi_filters = OpportunityFilter(
                max_oi_usd=Decimal('2000000'),  # < $2M
                limit=5
            )
            
            low_oi_opps = await finder.find_opportunities(low_oi_filters)
            
            print(f"Found {len(low_oi_opps)} low OI opportunities\n")
            
            if low_oi_opps:
                for opp in low_oi_opps[:5]:
                    print(
                        f"  {opp.symbol:<8} {opp.long_dex:<12} / {opp.short_dex:<12} "
                        f"APY: {float(opp.annualized_apy):>7.2f}% "
                        f"Min OI: ${float(opp.min_oi_usd or 0):>10,.0f}"
                    )
                print()
        
        print("✅ Opportunity finder tests passed!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ Opportunity finder test failed: {e}")
        logger.exception("Opportunity finder test failed")
        return False
    
    finally:
        await database.disconnect()
        print("="*70 + "\n")


async def main():
    """Main test function"""
    parser = argparse.ArgumentParser(description='Test Phase 3: Business Logic')
    parser.add_argument(
        '--fees-only',
        action='store_true',
        help='Test only the fee calculator (no database needed)'
    )
    parser.add_argument(
        '--symbol',
        help='Filter opportunities by symbol (e.g., BTC)'
    )
    args = parser.parse_args()
    
    print("\n" + "="*70)
    print("Phase 3: Business Logic - Test Suite")
    print("="*70)
    
    results = []
    
    # Test fee calculator (always)
    fee_result = await test_fee_calculator()
    results.append(('Fee Calculator', fee_result))
    
    # Test opportunity finder (unless --fees-only)
    if not args.fees_only:
        opp_result = await test_opportunity_finder(args.symbol)
        results.append(('Opportunity Finder', opp_result))
    else:
        print("\n⏭️  Skipping opportunity finder test (--fees-only mode)\n")
    
    # Summary
    print("="*70)
    print("Test Summary")
    print("="*70)
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name:<30} {status}")
    print("="*70 + "\n")
    
    # Exit code
    all_passed = all(result for _, result in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())

