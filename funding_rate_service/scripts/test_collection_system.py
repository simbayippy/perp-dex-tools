#!/usr/bin/env python3
"""
Test script for the complete collection system

This script tests:
1. Lighter adapter (standalone)
2. Collection orchestrator (with database)
3. End-to-end data flow

Usage:
    # Test adapter only (no database needed)
    python scripts/test_collection_system.py --adapter-only
    
    # Test full system (requires database)
    python scripts/test_collection_system.py
"""

import asyncio
import sys
from pathlib import Path
import argparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collection.adapters.lighter_adapter import LighterAdapter
from collection.orchestrator import CollectionOrchestrator
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


async def test_adapter_only():
    """Test just the Lighter adapter (no database)"""
    print("\n" + "="*70)
    print("TEST 1: Lighter Adapter (Standalone)")
    print("="*70 + "\n")
    
    adapter = LighterAdapter()
    
    try:
        print("📡 Fetching funding rates from Lighter...")
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
        test_symbols = ["BTC-PERP", "ETH-PERP", "1000PEPE-PERP", "SOL-USD"]
        for dex_symbol in test_symbols:
            normalized = adapter.normalize_symbol(dex_symbol)
            reverse = adapter.get_dex_symbol_format(normalized)
            print(f"   {dex_symbol:<18} -> {normalized:<10} -> {reverse}")
        
        print("\n✅ Adapter test passed!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ Adapter test failed: {e}")
        logger.exception("Adapter test failed")
        return False
    
    finally:
        await adapter.close()


async def test_full_system():
    """Test the complete system with database"""
    print("\n" + "="*70)
    print("TEST 2: Complete Collection System (with Database)")
    print("="*70 + "\n")
    
    adapter = None
    
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
        
        # Create adapter
        adapter = LighterAdapter()
        
        # Create orchestrator
        print("🎭 Creating orchestrator...")
        orchestrator = CollectionOrchestrator(database, adapters=[adapter])
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
        query = "SELECT COUNT(*) FROM funding_rates WHERE time >= NOW() - INTERVAL '1 minute'"
        recent_rates = await database.fetch_val(query)
        print(f"   Found {recent_rates} rates in database (last minute)")
        
        if recent_rates > 0:
            print("✅ Data successfully stored in database!")
        else:
            print("⚠️  No recent data found in database")
        
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
        if adapter:
            await adapter.close()
        await database.disconnect()
        print("="*70 + "\n")


async def main():
    """Main test function"""
    parser = argparse.ArgumentParser(description='Test collection system')
    parser.add_argument(
        '--adapter-only',
        action='store_true',
        help='Test only the adapter (no database needed)'
    )
    args = parser.parse_args()
    
    print("\n" + "="*70)
    print("Funding Rate Collection System - Test Suite")
    print("="*70)
    
    results = []
    
    # Test 1: Adapter only
    adapter_result = await test_adapter_only()
    results.append(('Adapter Test', adapter_result))
    
    # Test 2: Full system (only if not adapter-only mode)
    if not args.adapter_only:
        system_result = await test_full_system()
        results.append(('System Test', system_result))
    else:
        print("\n⏭️  Skipping system test (--adapter-only mode)\n")
    
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

