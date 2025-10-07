"""
Test the new /funding-rates/compare endpoint

This script tests the newly implemented funding rate comparison endpoint
that allows users to compare funding rates between two DEXs for a specific symbol.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from api.routes.funding_rates import compare_funding_rates
from fastapi import Query


async def test_compare_endpoint():
    """Test the funding-rates/compare endpoint"""
    
    print("🧪 Testing /funding-rates/compare endpoint\n")
    
    try:
        # Connect to database
        print("📊 Connecting to database...")
        await database.connect()
        
        # Load mappers
        print("📥 Loading mappers...")
        await dex_mapper.load_from_db(database)
        await symbol_mapper.load_from_db(database)
        print(f"✅ Loaded {len(dex_mapper)} DEXs and {len(symbol_mapper)} symbols\n")
        
        # Get available DEXs and symbols
        dexes = list(dex_mapper._name_to_id.keys())
        symbols = list(symbol_mapper._name_to_id.keys())
        
        print(f"Available DEXs: {', '.join(dexes)}")
        print(f"Available symbols: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}\n")
        
        # Test with the first two DEXs and first symbol (if available)
        if len(dexes) >= 2 and len(symbols) >= 1:
            dex1 = dexes[0]
            dex2 = dexes[1]
            symbol = symbols[0]
            
            print(f"🔍 Testing comparison: {symbol} between {dex1} and {dex2}\n")
            
            try:
                result = await compare_funding_rates(
                    symbol=symbol,
                    dex1=dex1,
                    dex2=dex2
                )
                
                print("✅ Endpoint Test Result:")
                print(f"   Symbol: {result['symbol']}")
                print(f"   DEX 1 ({result['dex1']['name']}):")
                print(f"      - Funding Rate: {result['dex1']['funding_rate']:.6f}")
                print(f"      - Next Funding: {result['dex1']['next_funding_time']}")
                print(f"      - Updated: {result['dex1']['timestamp']}")
                print(f"   DEX 2 ({result['dex2']['name']}):")
                print(f"      - Funding Rate: {result['dex2']['funding_rate']:.6f}")
                print(f"      - Next Funding: {result['dex2']['next_funding_time']}")
                print(f"      - Updated: {result['dex2']['timestamp']}")
                print(f"   Divergence: {result['divergence']:.6f} ({result['divergence_bps']:.2f} bps)")
                print(f"   Long Recommendation: {result['long_recommendation']}")
                print(f"   Short Recommendation: {result['short_recommendation']}")
                print(f"   Estimated Net Profit (8h): {result['estimated_net_profit_8h']:.6f}\n")
                
                print("✅ Test PASSED - Endpoint is working correctly!")
                
            except Exception as e:
                print(f"❌ Test FAILED: {e}")
                print(f"\nThis might be because there's no funding rate data for {symbol} on both DEXs.")
                print("Try running the collection system first to populate data:")
                print("  python scripts/test_collection_system.py\n")
        else:
            print("⚠️  Not enough DEXs or symbols in database to test.")
            print("Please seed the database first:")
            print("  python scripts/init_db.py")
            print("  python scripts/seed_dexes.py\n")
        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Disconnect
        print("\n🔌 Disconnecting from database...")
        await database.disconnect()
        print("✅ Done!")


if __name__ == "__main__":
    asyncio.run(test_compare_endpoint())

