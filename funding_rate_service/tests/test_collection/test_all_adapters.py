#!/usr/bin/env python3
"""
Test script for all DEX adapters

Tests all implemented adapters individually (no database needed).

Usage:
    # Test all adapters
    python scripts/test_all_adapters.py
    
    # Test specific adapter
    python scripts/test_all_adapters.py --adapter lighter
"""

import asyncio
import sys
from pathlib import Path
import argparse

# Add funding_rate_service directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from collection.adapters import LighterAdapter, GrvtAdapter, EdgeXAdapter
from utils.logger import logger


async def test_adapter(adapter_class, name):
    """Test a single adapter"""
    print(f"\n{'='*70}")
    print(f"Testing {name} Adapter")
    print(f"{'='*70}\n")
    
    adapter = adapter_class()
    
    try:
        print(f"📡 Fetching funding rates from {name}...")
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ Success!")
        print(f"   Latency: {latency_ms}ms")
        print(f"   Fetched: {len(rates)} funding rates\n")
        
        if not rates:
            print(f"⚠️  No rates returned from {name}")
            return False, 0
        
        # Display sample results
        print(f"{'-'*70}")
        print(f"{'Symbol':<10} {'Funding Rate':<18} {'Annualized APY':<15}")
        print(f"{'-'*70}")
        
        for symbol, rate in list(sorted(rates.items()))[:10]:
            annualized_apy = float(rate) * 365 * 3 * 100
            print(f"{symbol:<10} {float(rate):>17.10f} {annualized_apy:>14.2f}%")
        
        if len(rates) > 10:
            print(f"... and {len(rates) - 10} more symbols")
        print(f"{'-'*70}")
        
        # Test symbol normalization
        print(f"\n🔄 Symbol Normalization Test:")
        test_symbols = {
            'lighter': ["BTC-PERP", "ETH-PERP", "1000PEPE-PERP"],
            'paradex': ["BTC-USD-PERP", "ETH-USD-PERP", "SOL-USD-PERP"],
            'grvt': ["BTC", "ETH", "SOL"],
            'edgex': ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        }
        
        for test_sym in test_symbols.get(name.lower(), ["BTC-PERP", "ETH-PERP"]):
            normalized = adapter.normalize_symbol(test_sym)
            reverse = adapter.get_dex_symbol_format(normalized)
            print(f"   {test_sym:<18} -> {normalized:<10} -> {reverse}")
        
        # Test market data fetching
        print(f"\n📊 Market Data Test:")
        try:
            market_data = await adapter.fetch_market_data()
            
            if market_data:
                print(f"   ✅ Fetched market data for {len(market_data)} symbols")
                print(f"\n{'-'*70}")
                print(f"{'Symbol':<10} {'Volume 24h (USD)':<25} {'Open Interest (USD)':<25}")
                print(f"{'-'*70}")
                
                for symbol, data in list(sorted(market_data.items()))[:5]:
                    volume = data.get('volume_24h')
                    oi = data.get('open_interest')
                    volume_str = f"${volume:,.2f}" if volume else "N/A"
                    oi_str = f"${oi:,.2f}" if oi else "N/A"
                    print(f"{symbol:<10} {volume_str:<25} {oi_str:<25}")
                
                if len(market_data) > 5:
                    print(f"... and {len(market_data) - 5} more symbols")
                print(f"{'-'*70}")
            else:
                print(f"   ⚠️  No market data returned (may not be implemented yet)")
        except Exception as e:
            print(f"   ⚠️  Market data fetch failed (non-critical): {e}")
        
        print(f"\n✅ {name} adapter test passed!\n")
        return True, len(rates)
        
    except Exception as e:
        print(f"\n❌ {name} adapter test failed: {e}")
        logger.exception(f"{name} adapter test failed")
        return False, 0
    
    finally:
        await adapter.close()


async def main():
    """Main test function"""
    parser = argparse.ArgumentParser(description='Test DEX adapters')
    parser.add_argument(
        '--adapter',
        choices=['lighter', 'paradex', 'grvt', 'edgex', 'all'],
        default='all',
        help='Which adapter to test'
    )
    args = parser.parse_args()
    
    print("\n" + "="*70)
    print("DEX Adapters Test Suite")
    print("="*70)
    
    # Define adapters to test
    adapters = {
        # 'lighter': (LighterAdapter, 'Lighter'),
        # 'grvt': (GrvtAdapter, 'GRVT'),
        'edgex': (EdgeXAdapter, 'EdgeX'),
    }
    
    # Determine which adapters to test
    if args.adapter == 'all':
        adapters_to_test = adapters.items()
    else:
        adapters_to_test = [(args.adapter, adapters[args.adapter])]
    
    # Test each adapter
    results = []
    total_rates = 0
    
    for adapter_name, (adapter_class, display_name) in adapters_to_test:
        success, rate_count = await test_adapter(adapter_class, display_name)
        results.append((display_name, success, rate_count))
        total_rates += rate_count
        
        # Small delay between tests
        if len(adapters_to_test) > 1:
            await asyncio.sleep(1)
    
    # Summary
    print("="*70)
    print("Test Summary")
    print("="*70)
    for name, success, rate_count in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{name:<20} {status:>10}  ({rate_count} rates)")
    print("="*70)
    print(f"Total rates fetched: {total_rates}")
    print("="*70 + "\n")
    
    # Exit code
    all_passed = all(success for _, success, _ in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())

