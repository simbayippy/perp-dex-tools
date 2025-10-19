#!/usr/bin/env python3
"""
Test script for Lighter adapter

This script tests the Lighter adapter to ensure it can successfully
fetch funding rates from Lighter.

Usage:
    python scripts/test_lighter_adapter.py
"""

import asyncio
import sys
from pathlib import Path

# Add both project root (for exchange_clients) and funding_rate_service (for internal imports)
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))  # For exchange_clients
sys.path.insert(0, str(project_root / "funding_rate_service"))  # For funding_rate_service internal imports

from exchange_clients.lighter import LighterFundingAdapter
from funding_rate_service.utils.logger import logger


async def main():
    """Test the Lighter adapter"""
    print("\n" + "="*60)
    print("Testing Lighter Adapter")
    print("="*60 + "\n")
    
    # Create adapter
    adapter = LighterFundingAdapter(
        api_base_url="https://mainnet.zklighter.elliot.ai"
    )
    
    try:
        # Fetch funding rates with metrics
        print("📡 Fetching funding rates from Lighter...")
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ Success!")
        print(f"   Latency: {latency_ms}ms")
        print(f"   Fetched: {len(rates)} funding rates\n")
        
        if not rates:
            print("⚠️  No rates returned (this might be expected if no perpetuals are available)")
            return
        
        # Display results
        print("-"*78)
        print(f"{'Symbol':<10} {'Funding Rate (8h)':<22} {'Interval (h)':<14} {'Annualized APY':<15}")
        print("-"*78)
        
        # Sort by symbol
        for symbol, sample in sorted(rates.items())[:15]:  # Show first 15
            rate_value = sample.normalized_rate
            annualized_apy = float(rate_value) * 365 * 3 * 100
            interval_hours = float(sample.interval_hours)
            
            print(
                f"{symbol:<10} {float(rate_value):>21.8f} "
                f"{interval_hours:>13.2f} {annualized_apy:>14.2f}%"
            )
        
        if len(rates) > 15:
            print(f"... and {len(rates) - 15} more symbols")
        
        print("-"*78)
        
        # Show statistics
        rates_list = [float(sample.normalized_rate) for sample in rates.values()]
        avg_rate = sum(rates_list) / len(rates_list)
        max_rate = max(rates_list)
        min_rate = min(rates_list)
        
        print(f"\n📊 Statistics:")
        print(f"   Average rate: {avg_rate:.8f} ({avg_rate * 365 * 3 * 100:.2f}% APY)")
        print(f"   Max rate:     {max_rate:.8f} ({max_rate * 365 * 3 * 100:.2f}% APY)")
        print(f"   Min rate:     {min_rate:.8f} ({min_rate * 365 * 3 * 100:.2f}% APY)")
        
        # Test symbol normalization
        print(f"\n🔄 Testing symbol normalization:")
        test_symbols = ["BTC-PERP", "ETH-PERP", "1000PEPE-PERP", "SOL-USD"]
        for dex_symbol in test_symbols:
            normalized = adapter.normalize_symbol(dex_symbol)
            reverse = adapter.get_dex_symbol_format(normalized)
            print(f"   {dex_symbol:<15} -> {normalized:<10} -> {reverse}")
        
        # Test market data fetching
        print(f"\n📊 Testing market data (volume, OI) fetching:")
        market_data = await adapter.fetch_market_data()
        
        if market_data:
            print(f"   ✅ Fetched market data for {len(market_data)} symbols\n")
            print("-"*70)
            print(f"{'Symbol':<10} {'Volume 24h (USD)':<20} {'Open Interest':<20}")
            print("-"*70)
            
            for symbol, data in list(sorted(market_data.items()))[:10]:
                volume = data.get('volume_24h')
                oi = data.get('open_interest')
                volume_str = f"${volume:,.2f}" if volume else "N/A"
                oi_str = f"${oi:,.2f}" if oi else "N/A"
                print(f"{symbol:<10} {volume_str:<20} {oi_str:<20}")
            
            if len(market_data) > 10:
                print(f"... and {len(market_data) - 10} more symbols")
            print("-"*70)
            
        else:
            print("   ⚠️  No market data returned")
        
        print("\n✅ All tests passed!\n")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        logger.exception("Lighter adapter test failed")
        sys.exit(1)
    
    finally:
        await adapter.close()
        print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
