#!/usr/bin/env python3
"""
Test script for the complete collection system

This script tests:
1. Multiple DEX adapters (Lighter, GRVT, EdgeX) - standalone
2. Collection orchestrator (with database)
3. End-to-end data flow

Usage:
    # Test adapters only (no database needed)
    python scripts/test_collection_system.py --adapter-only
    
    # Test specific adapter only
    python scripts/test_collection_system.py --adapter-only --adapter lighter
    python scripts/test_collection_system.py --adapter-only --adapter grvt
    python scripts/test_collection_system.py --adapter-only --adapter edgex
    
    # Test full system (requires database)
    python scripts/test_collection_system.py
"""

import asyncio
import sys
from pathlib import Path
import argparse

# Add funding_rate_service directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from collection.adapters import LighterAdapter, GrvtAdapter, EdgeXAdapter
from collection.orchestrator import CollectionOrchestrator
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


async def test_single_adapter(adapter_class, adapter_name, test_symbols):
    """Test a single adapter (no database)"""
    print("\n" + "="*70)
    print(f"TEST: {adapter_name} Adapter (Standalone)")
    print("="*70 + "\n")
    
    adapter = adapter_class()
    
    try:
        print(f"📡 Fetching funding rates from {adapter_name}...")
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ Success!")
        print(f"   Latency: {latency_ms}ms")
        print(f"   Fetched: {len(rates)} funding rates\n")
        
        if not rates:
            print("⚠️  No rates returned")
            return False
        
        # Display sample results
        print("-"*70)
        print(f"{'Symbol':<10} {'Funding Rate':<18} {'Annualized APY':<15}")
        print("-"*70)
        
        for symbol, rate in list(sorted(rates.items()))[:10]:
            annualized_apy = float(rate) * 365 * 3 * 100
            print(f"{symbol:<10} {float(rate):>17.10f} {annualized_apy:>14.2f}%")
        
        if len(rates) > 10:
            print(f"... and {len(rates) - 10} more symbols")
        print("-"*70)
        
        # Test symbol normalization
        print(f"\n🔄 Symbol Normalization Test:")
        for dex_symbol in test_symbols:
            normalized = adapter.normalize_symbol(dex_symbol)
            reverse = adapter.get_dex_symbol_format(normalized)
            print(f"   {dex_symbol:<18} -> {normalized:<10} -> {reverse}")
        
        # Test market data fetching
        print(f"\n📊 Market Data Test:")
        try:
            market_data = await adapter.fetch_market_data()
            
            if market_data:
                print(f"   ✅ Fetched market data for {len(market_data)} symbols")
                
                # Show sample
                for symbol, data in list(sorted(market_data.items()))[:3]:
                    volume = data.get('volume_24h')
                    oi = data.get('open_interest')
                    volume_str = f"${volume:,.2f}" if volume else "N/A"
                    oi_str = f"${oi:,.2f}" if oi else "N/A"
                    print(f"      {symbol}: Vol={volume_str}, OI={oi_str}")
            else:
                print(f"   ⚠️  No market data returned (may not be implemented yet)")
        except Exception as e:
            print(f"   ⚠️  Market data fetch failed (non-critical): {e}")
        
        print(f"\n✅ {adapter_name} adapter test passed!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ {adapter_name} adapter test failed: {e}")
        logger.exception(f"{adapter_name} adapter test failed")
        return False
    
    finally:
        await adapter.close()


async def test_adapters_only(adapter_filter=None):
    """Test all adapters or a specific one (no database)"""
    adapters_to_test = {
        'lighter': (LighterAdapter, 'Lighter', ["BTC-PERP", "ETH-PERP", "1000PEPE-PERP"]),
        'grvt': (GrvtAdapter, 'GRVT', ["BTC_USDT_Perp", "ETH_USDT_Perp", "SOL_USDT_Perp"]),
        'edgex': (EdgeXAdapter, 'EdgeX', ["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    }
    
    # Filter if specific adapter requested
    if adapter_filter:
        if adapter_filter not in adapters_to_test:
            print(f"❌ Unknown adapter: {adapter_filter}")
            return False
        adapters_to_test = {adapter_filter: adapters_to_test[adapter_filter]}
    
    results = []
    for adapter_key, (adapter_class, adapter_name, test_symbols) in adapters_to_test.items():
        result = await test_single_adapter(adapter_class, adapter_name, test_symbols)
        results.append((adapter_name, result))
    
    return all(result for _, result in results)


async def test_full_system():
    """Test the complete system with database"""
    print("\n" + "="*70)
    print("TEST: Complete Collection System (with Database)")
    print("="*70 + "\n")
    
    adapters = []
    
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
        
        # Create adapters for Lighter, GRVT, and EdgeX
        print("🔧 Initializing adapters...")
        adapters = [
            LighterAdapter(),
            GrvtAdapter(),
            EdgeXAdapter(),
        ]
        print(f"✅ Initialized {len(adapters)} adapters (Lighter, GRVT, EdgeX)\n")
        
        # Create orchestrator
        print("🎭 Creating orchestrator...")
        orchestrator = CollectionOrchestrator(database, adapters=adapters)
        print("✅ Orchestrator ready\n")
        
        # Run collection
        print("📡 Starting collection...")
        print("-"*70)
        result = await orchestrator.collect_all_rates()
        print("-"*70)
        
        # Display results
        print(f"\n📊 Collection Summary:")
        print(f"   Total DEXs:     {result['total_adapters']}")
        print(f"   Successful:     {result['successful']}")
        print(f"   Failed:         {result['failed']}")
        print(f"   Total Rates:    {result['total_rates']}")
        print(f"   Duration:       {result['duration_seconds']:.2f}s\n")
        
        # Show per-DEX results
        print("📋 Per-DEX Results:")
        for dex_name, dex_result in result['results'].items():
            if dex_result['success']:
                print(f"   ✅ {dex_name}:")
                print(f"      Rates:       {dex_result['rates_count']}")
                print(f"      New Symbols: {dex_result['new_symbols']}")
                print(f"      Latency:     {dex_result['latency_ms']}ms")
            else:
                print(f"   ❌ {dex_name}: {dex_result.get('error', 'Unknown error')}")
        
        # Verify data was stored
        print(f"\n🔍 Verifying data storage...")
        
        # Check funding rates
        query = "SELECT COUNT(*) FROM funding_rates WHERE time >= NOW() - INTERVAL '1 minute'"
        recent_rates = await database.fetch_val(query)
        print(f"   Funding rates: {recent_rates} (last minute)")
        
        if recent_rates > 0:
            print("   ✅ Funding rates successfully stored!")
        else:
            print("   ⚠️  No recent funding rates found")
        
        # Check market data
        market_data_query = """
            SELECT COUNT(*) 
            FROM dex_symbols 
            WHERE (volume_24h IS NOT NULL OR open_interest_usd IS NOT NULL)
            AND updated_at >= NOW() - INTERVAL '1 minute'
        """
        recent_market_data = await database.fetch_val(market_data_query)
        print(f"   Market data: {recent_market_data} symbols updated (last minute)")
        
        if recent_market_data > 0:
            print("   ✅ Market data successfully stored!")
            
            # Show sample market data
            sample_query = """
                SELECT 
                    s.normalized_name,
                    ds.volume_24h,
                    ds.open_interest_usd,
                    d.name as dex_name
                FROM dex_symbols ds
                JOIN symbols s ON ds.symbol_id = s.id
                JOIN dexes d ON ds.dex_id = d.id
                WHERE (ds.volume_24h IS NOT NULL OR ds.open_interest_usd IS NOT NULL)
                AND ds.updated_at >= NOW() - INTERVAL '1 minute'
                ORDER BY ds.volume_24h DESC NULLS LAST
                LIMIT 5
            """
            sample_data = await database.fetch_all(sample_query)
            
            if sample_data:
                print(f"\n   Sample market data:")
                print(f"   {'-'*70}")
                print(f"   {'DEX':<10} {'Symbol':<10} {'Volume 24h':<20} {'OI (USD)':<20}")
                print(f"   {'-'*70}")
                for row in sample_data:
                    vol_str = f"${row['volume_24h']:,.2f}" if row['volume_24h'] else "N/A"
                    oi_str = f"${row['open_interest_usd']:,.2f}" if row['open_interest_usd'] else "N/A"
                    print(f"   {row['dex_name']:<10} {row['normalized_name']:<10} {vol_str:<20} {oi_str:<20}")
                print(f"   {'-'*70}")
        else:
            print("   ⚠️  No recent market data found (may not be implemented yet)")
        
        # Show updated mapper stats
        print(f"\n📚 Updated Mappers:")
        print(f"   DEXs:    {len(dex_mapper)}")
        print(f"   Symbols: {len(symbol_mapper)}")
        
        print("\n✅ Full system test passed!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ System test failed: {e}")
        logger.exception("System test failed")
        return False
    
    finally:
        # Close all adapters
        for adapter in adapters:
            await adapter.close()
        await database.disconnect()
        print("="*70 + "\n")


async def main():
    """Main test function"""
    parser = argparse.ArgumentParser(description='Test collection system')
    parser.add_argument(
        '--adapter-only',
        action='store_true',
        help='Test only the adapters (no database needed)'
    )
    parser.add_argument(
        '--adapter',
        choices=['lighter', 'grvt', 'edgex', 'all'],
        default='all',
        help='Which adapter to test (only with --adapter-only)'
    )
    args = parser.parse_args()
    
    print("\n" + "="*70)
    print("Funding Rate Collection System - Test Suite")
    print("="*70)
    
    results = []
    
    # Test adapters (standalone)
    if args.adapter_only:
        adapter_filter = None if args.adapter == 'all' else args.adapter
        adapter_result = await test_adapters_only(adapter_filter)
        results.append(('Adapter Tests', adapter_result))
        print("\n⏭️  Skipping system test (--adapter-only mode)\n")
    else:
        # Test full system with database
        system_result = await test_full_system()
        results.append(('Full System Test', system_result))
    
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

